"""Operational intelligence service for attention classification."""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from backend.adapters.base import SourceService, EventType, EvidenceBoundary
from backend.correlation.engine import CorrelationResult

logger = logging.getLogger("arr-control.services.orchestrator")


def _norm_ts(ts: Optional[datetime]) -> datetime:
    """Normalize any event timestamp to naive-UTC so it can be compared with
    datetime.utcnow(). Strips tzinfo (converting aware datetimes to UTC first).
    """
    if ts is None:
        return datetime.utcnow()
    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _seconds_since(ts: Optional[datetime]) -> float:
    return (_norm_ts(ts) and (datetime.utcnow() - _norm_ts(ts)).total_seconds()) or 0.0


class AttentionLevel:
    """Levels of operational attention."""
    NONE = "none"
    POSSIBLE = "possible"
    REQUIRED = "required"
    BLOCKED = "blocked"
    SYNTHETIC = "synthetic"  # Filter out in production


class Orchestrator:
    """Classifies media items into operational attention levels."""

    # Thresholds (in seconds)
    STALL_DURATION_THRESHOLD = timedelta(hours=2)  # No progress for 2h = stalled
    DOWNLOAD_START_TIMEOUT = timedelta(minutes=10)  # Grabbed but no download start
    DEFAULT_TIMEOUT = timedelta(hours=1)  # Stuck detection for search

    # Time windows for observation
    RECENT_WINDOW = timedelta(hours=24)  # Look back for recent activity

    def classify_item(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify a single item's operational state and attention level.

        Returns a dict with:
        - attention_level: none | possible | required | blocked | synthetic
        - reason: human-readable explanation
        - evidence: list of supporting evidence points
        - evidence_boundary: the evidence boundary (OBSERVED, CORRELATED, etc.)
        - confidence_basis: why we have this confidence
        """
        # Never classify synthetic data as requiring attention
        if item.evidence_boundary == EvidenceBoundary.SYNTHETIC:
            return {
                "attention_level": AttentionLevel.SYNTHETIC,
                "reason": "Item is based on synthetic/test data, not live environment",
                "evidence": [],
                "evidence_boundary": "synthetic",
                "confidence_basis": None,
                "requires_attention": False,
            }

        # Check for blocked evidence
        if item.evidence_boundary == EvidenceBoundary.BLOCKED:
            return self._blocked_classification(item)

        # Check for known failure states
        if item.current_state in (EventType.DOWNLOAD_FAILED, EventType.IMPORT_FAILED):
            return self._failure_classification(item)

        # Check for stuck states
        if item.current_state == EventType.STUCK:
            return self._stuck_classification(item)

        # Check for incomplete states (downloaded but not imported, etc.)
        if item.current_state in (EventType.DOWNLOAD_COMPLETED, EventType.IMPORT_STARTED):
            return self._incomplete_classification(item)

        # Check for active downloads that may be stalled
        if item.current_state in (EventType.DOWNLOAD_PROGRESS, EventType.DOWNLOAD_STARTED, EventType.RELEASE_GRABBED):
            return self._active_download_classification(item)

        # Check for search states
        if item.current_state in (EventType.SEARCH_STARTED, EventType.WANTED):
            return self._search_classification(item)

        # Completed/available - no attention needed
        if item.current_state == EventType.AVAILABLE:
            return {
                "attention_level": AttentionLevel.NONE,
                "reason": "Import completed successfully",
                "evidence": [],
                "evidence_boundary": item.evidence_boundary.value,
                "confidence_basis": item.confidence_basis,
                "requires_attention": False,
            }

        # Default: low-confidence classification
        return self._default_classification(item)

    def _failure_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items with known failure states."""
        reasons = []
        evidence = []

        if item.last_event:
            evidence.append(f"{item.last_event.event_type.value} from {item.last_event.source_service.value}")
            if item.last_event.error_message:
                evidence.append(f"Error: {item.last_event.error_message}")

        # Determine reason based on state
        if item.current_state == EventType.DOWNLOAD_FAILED:
            reason = "Download failed according to observed qBittorrent state"
            if item.last_event and item.last_event.error_message:
                reason += f": {item.last_event.error_message}"
        elif item.current_state == EventType.IMPORT_FAILED:
            reason = "Import failed according to observed Sonarr/Radarr history"
            if item.last_event and item.last_event.error_message:
                reason += f": {item.last_event.error_message}"
        else:
            reason = "Item has a failed state according to available evidence"

        return {
            "attention_level": AttentionLevel.REQUIRED,
            "reason": reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": True,
            "severity": "high",
            "state": item.current_state.value,
        }

    def _stuck_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify stuck items."""
        evidence = []

        # Get the stuck reason from state machine if available
        stuck_reason = None
        if item.last_event:
            evidence.append(f"Stuck state from {item.last_event.source_service.value}")

        # Check for qBittorrent stalled indicators
        for event in reversed(item.events):
            if event.source_service == SourceService.QBITTORRENT and event.normalized_metadata:
                qbit_state = event.normalized_metadata.get("state", "").lower()
                if qbit_state in ["stalleddl", "stallledup"]:
                    progress = event.normalized_metadata.get("progress", 0)
                    evidence.append(f"qBittorrent reports stalled ({qbit_state}) at {progress}%")
                    stuck_reason = f"qBittorrent reports stalled ({qbit_state})"
                    break

        # Check for no-progress duration
        if len(item.events) > 1:
            first_ts = item.events[0].timestamp
            last_ts = item.last_event.timestamp
            duration = last_ts - first_ts

            if duration > self.STALL_DURATION_THRESHOLD:
                evidence.append(f"No progress detected for {duration.total_seconds() / 3600:.1f} hours")
                if stuck_reason is None:
                    stuck_reason = f"No progress for {duration.total_seconds() / 3600:.1f} hours"

        if stuck_reason is None:
            stuck_reason = "Item is stuck according to observed state"

        return {
            "attention_level": AttentionLevel.REQUIRED,
            "reason": stuck_reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": True,
            "severity": "high",
            "state": item.current_state.value,
        }

    def _blocked_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items where evidence is blocked."""
        return {
            "attention_level": AttentionLevel.BLOCKED,
            "reason": "Required evidence could not be obtained because an external service was unavailable",
            "evidence": ["Evidence source unavailable"],
            "evidence_boundary": "blocked",
            "confidence_basis": None,
            "requires_attention": True,
            "severity": "medium",
            "state": item.current_state.value,
        }

    def _active_download_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify active downloads for potential stalls."""
        evidence = []
        attention = AttentionLevel.NONE
        severity = "none"
        reason = "Download is progressing normally"

        # Check if download is stalled (no progress for extended time)
        download_events = [
            e for e in item.events
            if e.source_service == SourceService.QBITTORRENT
            and e.event_type in (EventType.DOWNLOAD_PROGRESS, EventType.DOWNLOAD_STARTED)
        ]

        if download_events:
            latest_progress = None
            latest_progress_time = None

            for event in download_events:
                progress = event.normalized_metadata.get("progress", 0) if event.normalized_metadata else None
                if progress is not None:
                    latest_progress = progress
                    latest_progress_time = event.timestamp

            if latest_progress is not None and latest_progress_time:
                time_since_last_progress = _norm_ts(latest_progress_time)
                time_since_last_progress = datetime.utcnow() - time_since_last_progress

                # If progress hasn't changed and is not complete, flag as possible stall
                if latest_progress < 100 and time_since_last_progress > self.STALL_DURATION_THRESHOLD:
                    attention = AttentionLevel.POSSIBLE
                    severity = "medium"
                    reason = f"Download at {latest_progress}% for {time_since_last_progress.total_seconds() / 3600:.1f} hours - may be stalled"
                    evidence.append(f"No progress update for {time_since_last_progress.total_seconds() / 3600:.1f} hours")
                elif latest_progress == 100:
                    reason = "Download complete, awaiting import"
                    attention = AttentionLevel.POSSIBLE
                    severity = "low"

        # Check if grabbed but no download started
        if item.current_state == EventType.RELEASE_GRABBED:
            grabbed_event = None
            for event in reversed(item.events):
                if event.event_type == EventType.RELEASE_GRABBED:
                    grabbed_event = event
                    break

            if grabbed_event:
                time_since_grab = datetime.utcnow() - _norm_ts(grabbed_event.timestamp)
                if time_since_grab > self.DOWNLOAD_START_TIMEOUT:
                    attention = AttentionLevel.POSSIBLE
                    severity = "medium"
                    reason = f"Release grabbed but no download started for {time_since_grab.total_seconds() / 3600:.1f} hours"
                    evidence.append("No download_start after grab event")

        # If LOW confidence, be more cautious
        if item.confidence == "LOW":
            if attention == AttentionLevel.REQUIRED:
                attention = AttentionLevel.POSSIBLE
                reason += " (based on heuristic correlation)"

        return {
            "attention_level": attention,
            "reason": reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": attention != AttentionLevel.NONE,
            "severity": severity,
            "state": item.current_state.value,
        }

    def _incomplete_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify incomplete states (downloaded but not imported)."""
        evidence = []

        if item.current_state == EventType.DOWNLOAD_COMPLETED:
            reason = "Download completed but import not yet started"
            attention = AttentionLevel.POSSIBLE
            severity = "medium"
            evidence.append("Awaiting import triggered by *Arr")
        elif item.current_state == EventType.IMPORT_STARTED:
            reason = "Import in progress"
            attention = AttentionLevel.NONE
            severity = "none"
        else:
            reason = "Item in incomplete state"
            attention = AttentionLevel.POSSIBLE
            severity = "low"

        return {
            "attention_level": attention,
            "reason": reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": attention != AttentionLevel.NONE,
            "severity": severity,
            "state": item.current_state.value,
        }

    def _search_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items in search/waiting state."""
        evidence = []

        if item.current_state == EventType.SEARCH_STARTED:
            # Check for search timeout
            search_events = [e for e in item.events if e.event_type == EventType.SEARCH_STARTED]
            if search_events:
                last_search = search_events[-1]
                time_since_search = datetime.utcnow() - _norm_ts(last_search.timestamp)

                if time_since_search > self.DEFAULT_TIMEOUT:
                    attention = AttentionLevel.POSSIBLE
                    severity = "low"
                    reason = f"Search started {time_since_search.total_seconds() / 3600:.1f} hours ago without completion"
                    evidence.append("No search_completed or grab after extended period")
                else:
                    attention = AttentionLevel.NONE
                    severity = "none"
                    reason = "Search in progress"
            else:
                attention = AttentionLevel.NONE
                severity = "none"
                reason = "Waiting for release availability"
        else:
            attention = AttentionLevel.NONE
            severity = "none"
            reason = "Item waiting in queue"

        return {
            "attention_level": attention,
            "reason": reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": attention != AttentionLevel.NONE,
            "severity": severity,
            "state": item.current_state.value,
        }

    def _default_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Default classification when state is unclear."""
        return {
            "attention_level": AttentionLevel.NONE,
            "reason": f"Item is in state: {item.current_state.value}",
            "evidence": [],
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": False,
            "severity": "none",
            "state": item.current_state.value,
        }

    def get_attention_items(
        self,
        items: List[CorrelationResult],
        include_blocked: bool = True,
        exclude_synthetic: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get all items requiring attention, sorted by priority.

        Args:
            items: List of CorrelationResult to classify
            include_blocked: Whether to include BLOCKED items
            exclude_synthetic: Whether to exclude SYNTHETIC items (default True)

        Returns:
            List of attention dicts sorted by priority (high → medium → low → blocked)
        """
        results = []

        for item in items:
            classification = self.classify_item(item)

            # Skip synthetic items by default (they're test data)
            if exclude_synthetic and classification["attention_level"] == AttentionLevel.SYNTHETIC:
                continue

            # Skip items with no attention
            if classification["attention_level"] == AttentionLevel.NONE:
                continue

            # Skip blocked unless explicitly requested
            if classification["attention_level"] == AttentionLevel.BLOCKED and not include_blocked:
                continue

            # Add metadata
            classification["item"] = {
                "id": item.correlation_key,
                "title": item.title,
                "media_type": item.media_type.value,
                "current_state": item.current_state.value,
                "last_event_at": item.last_event.timestamp.isoformat() if item.last_event else None,
                "event_count": len(item.events),
            }

            results.append(classification)

        # Sort by priority: required > possible > blocked
        priority_order = {
            AttentionLevel.REQUIRED: 0,
            AttentionLevel.POSSIBLE: 1,
            AttentionLevel.BLOCKED: 2,
        }

        results.sort(key=lambda x: priority_order.get(x["attention_level"], 99))

        return results


# Global orchestrator instance
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get or create the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator

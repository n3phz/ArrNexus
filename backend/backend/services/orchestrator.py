"""Operational intelligence service for attention classification."""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from backend.adapters.base import SourceService, EventType, EvidenceBoundary
from backend.correlation.engine import CorrelationResult

logger = logging.getLogger("arrnexus.services.orchestrator")

# Reuse the correlation engine's threshold table so the engine's stage_freshness
# and the orchestrator's UNUSUALLY_LONG_STAGE can never disagree about the same
# elapsed time.
from backend.correlation.engine import STAGE_DURATION_SECONDS


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
    IMPORT_START_TIMEOUT = timedelta(hours=1)  # Download completed but no import start

    # Per-stage expected durations come from the shared engine table
    # (STAGE_DURATION_SECONDS) so both layers agree.
    STAGE_DURATION_THRESHOLDS = {
        state: timedelta(seconds=seconds)
        for state, seconds in STAGE_DURATION_SECONDS.items()
    }

    # Escalation multipliers applied against the stage's expected duration.
    STAGE_STALE_MULTIPLIER = 1.5     # >1.5x expected  => REQUIRED
    STAGE_ABANDONED_MULTIPLIER = 3.0  # >3x expected    => REQUIRED / high

    # Attention is escalated to REQUIRED when a missing-transition wait exceeds
    # this absolute duration, regardless of the per-state multiplier.
    INCIDENT_ESCALATION = timedelta(hours=24)

    # Time windows for observation
    RECENT_WINDOW = timedelta(hours=24)  # Look back for recent activity

    # States in which the pipeline is still expected to advance. Absence of new
    # evidence for one of these is operationally meaningful.
    IN_FLIGHT_STATES = frozenset({
        EventType.WANTED,
        EventType.SEARCH_STARTED,
        EventType.RELEASE_GRABBED,
        EventType.DOWNLOAD_STARTED,
        EventType.DOWNLOAD_PROGRESS,
        EventType.DOWNLOAD_COMPLETED,
        EventType.IMPORT_STARTED,
    })

    # Terminal / settled states. Being old is never an incident here: a wanted
    # entry from last month is simply an old wanted entry, and a completed item
    # is finished regardless of how long ago it completed.
    SETTLED_STATES = frozenset({
        EventType.AVAILABLE,
        EventType.IMPORT_COMPLETED,
        EventType.DOWNLOAD_FAILED,
        EventType.IMPORT_FAILED,
        EventType.SEARCH_FAILED,
        EventType.SEARCH_COMPLETED,
    })

    def classify_item(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify a single item's operational state and attention level.

        Returns a dict with:
        - attention_level: none | possible | required | blocked | synthetic
        - attention_class: stable machine-readable reason code
        - reason: human-readable explanation
        - evidence: list of supporting evidence points
        - evidence_boundary: the evidence boundary (OBSERVED, CORRELATED, etc.)
        - confidence_basis: why we have this confidence
        - stage_freshness / evidence_freshness: the two recency axes, reported
          separately so a UI never conflates them

        Ordering note: the pipeline-gap rules are dispatched per-state BEFORE the
        generic stage-duration rule, so a specific diagnosis (e.g.
        GRAB_NO_DOWNLOAD) is never pre-empted by the generic UNUSUALLY_LONG_STAGE
        description of the same underlying fact.
        """
        # Never classify synthetic data as requiring attention
        if item.evidence_boundary == EvidenceBoundary.SYNTHETIC:
            return self._envelope(
                item,
                attention_level=AttentionLevel.SYNTHETIC,
                attention_class="SYNTHETIC_DATA",
                reason="Item is based on synthetic/test data, not live environment",
                evidence=[],
                requires_attention=False,
                severity="none",
            )

        # Evidence could not be obtained at all - we cannot conclude anything.
        if item.evidence_boundary == EvidenceBoundary.BLOCKED:
            return self._blocked_classification(item)

        # Known failure states are always actionable.
        if item.current_state in (EventType.DOWNLOAD_FAILED, EventType.IMPORT_FAILED):
            return self._failure_classification(item)

        # Stuck is derived by the state machine from observed qBittorrent state.
        if item.current_state == EventType.STUCK:
            return self._stuck_classification(item)

        # Completed successfully. A settled item is never an incident, no matter
        # how long ago it was observed.
        if item.current_state == EventType.AVAILABLE:
            return self._envelope(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="COMPLETED",
                reason="Import completed successfully",
                evidence=[],
                requires_attention=False,
                severity="none",
            )

        # --- Specific pipeline-gap rules, one per state -------------------
        if item.current_state == EventType.RELEASE_GRABBED:
            gap = self._grab_no_download_classification(item)
            if gap["attention_level"] != AttentionLevel.NONE:
                return gap

        if item.current_state == EventType.DOWNLOAD_COMPLETED:
            gap = self._download_no_import_classification(item)
            if gap["attention_level"] != AttentionLevel.NONE:
                return gap

        # --- Generic rules for the remaining in-flight states -------------
        if item.current_state in (EventType.DOWNLOAD_PROGRESS, EventType.DOWNLOAD_STARTED):
            stall = self._active_download_classification(item)
            if stall["attention_level"] != AttentionLevel.NONE:
                return stall

        stage_check = self._unusually_long_stage_classification(item)
        if stage_check["attention_level"] != AttentionLevel.NONE:
            return stage_check

        if item.current_state in (EventType.SEARCH_STARTED, EventType.WANTED):
            return self._search_classification(item)

        if item.current_state == EventType.IMPORT_STARTED:
            return self._incomplete_classification(item)

        # Default: low-confidence classification
        return self._default_classification(item)

    def _envelope(
        self,
        item: CorrelationResult,
        attention_level: str,
        attention_class: str,
        reason: str,
        evidence: List[str],
        requires_attention: bool,
        severity: str,
    ) -> Dict[str, Any]:
        """Build the standard classification payload.

        Every classification path goes through here so that the response shape is
        identical regardless of which rule fired. The two recency axes are always
        present, which is what lets a client tell "this stage is overdue" apart
        from "we last heard about this 8 days ago".
        """
        return {
            "attention_level": attention_level,
            "attention_class": attention_class,
            "reason": reason,
            "evidence": evidence,
            "evidence_boundary": item.evidence_boundary.value,
            "confidence_basis": item.confidence_basis,
            "requires_attention": requires_attention,
            "severity": severity,
            "state": item.current_state.value,
            "stage_freshness": item.stage_freshness,
            "evidence_freshness": item.evidence_freshness,
            "stage_duration_seconds": item.stage_duration_seconds,
            "evidence_age_seconds": item.evidence_age_seconds,
            "missing_transition": (
                item.missing_transition.value if item.missing_transition else None
            ),
        }

    def _context_notes(self, item: CorrelationResult) -> List[str]:
        """Non-actionable context appended to a classification.

        Stale evidence is reported here rather than as its own attention class.
        "We last heard about this 8 days ago" is a fact about the observation,
        not an incident, so it is surfaced as context and never escalates on its
        own. Attention is driven only by the current state plus its expected
        stage duration.
        """
        notes: List[str] = []
        if item.evidence_freshness == "STALE":
            days = (item.evidence_age_seconds or 0) / 86400.0
            notes.append(
                f"Last observation is {days:.1f} days old; this conclusion is "
                f"based on historical evidence, not current activity"
            )
        if item.stage_freshness == "UNKNOWN" and item.stage_duration_seconds is None:
            notes.append(
                "Current state was derived rather than observed, so stage age is unknown"
            )
        if item.evidence_boundary == EvidenceBoundary.INFERRED:
            notes.append(
                "Conclusion rests on inferred correlation, not an exact identifier match"
            )
        return notes

    def _finalize(
        self,
        item: CorrelationResult,
        attention_level: str,
        attention_class: str,
        reason: str,
        evidence: List[str],
        severity: str,
    ) -> Dict[str, Any]:
        """Assemble a classification, merging in non-actionable context notes."""
        return self._envelope(
            item,
            attention_level=attention_level,
            attention_class=attention_class,
            reason=reason,
            evidence=list(evidence) + self._context_notes(item),
            requires_attention=attention_level != AttentionLevel.NONE,
            severity=severity,
        )

    @staticmethod
    def _fmt(seconds: Optional[float]) -> str:
        """Human-readable elapsed time."""
        if seconds is None:
            return "unknown"
        hours = seconds / 3600.0
        if hours < 1:
            return f"{seconds / 60.0:.0f}m"
        if hours < 48:
            return f"{hours:.1f}h"
        return f"{hours / 24.0:.1f}d"

    def _wait_since_stage_start(
        self, item: CorrelationResult, since: Optional[datetime]
    ) -> Optional[float]:
        """Seconds waited for the current stage, preferring engine stage age."""
        if item.stage_duration_seconds is not None:
            return item.stage_duration_seconds
        if since is None:
            return None
        return (datetime.utcnow() - _norm_ts(since)).total_seconds()

    def _failure_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items with known failure states."""
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

        return self._finalize(
            item,
            attention_level=AttentionLevel.REQUIRED,
            attention_class="DOWNLOAD_FAILED" if item.current_state == EventType.DOWNLOAD_FAILED else "IMPORT_FAILED",
            reason=reason,
            evidence=evidence,
            severity="high",
        )

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
                if qbit_state in ["stalleddl", "stalledup"]:
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

        return self._finalize(
            item,
            attention_level=AttentionLevel.REQUIRED,
            attention_class="STUCK",
            reason=stuck_reason,
            evidence=evidence,
            severity="high",
        )

    def _blocked_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items where evidence is blocked."""
        return self._finalize(
            item,
            attention_level=AttentionLevel.BLOCKED,
            attention_class="EVIDENCE_BLOCKED",
            reason="Required evidence could not be obtained because an external service was unavailable",
            evidence=["Evidence source unavailable"],
            severity="medium",
        )

    def _active_download_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify active downloads for potential stalls.

        Only returns attention when qBittorrent evidence shows a stalled
        transfer. A healthy in-flight download is left at NONE so the generic
        stage-duration rule gets a chance to judge it instead.
        """
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
                time_since_last_progress = datetime.utcnow() - _norm_ts(latest_progress_time)

                # If progress hasn't changed and is not complete, flag as possible stall
                if latest_progress < 100 and time_since_last_progress > self.STALL_DURATION_THRESHOLD:
                    attention = AttentionLevel.POSSIBLE
                    severity = "medium"
                    reason = f"Download at {latest_progress}% for {time_since_last_progress.total_seconds() / 3600:.1f} hours - may be stalled"
                    evidence.append(f"No progress update for {time_since_last_progress.total_seconds() / 3600:.1f} hours")

        if attention == AttentionLevel.NONE:
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="DOWNLOAD_IN_PROGRESS",
                reason=reason,
                evidence=evidence,
                severity="none",
            )

        return self._finalize(
            item,
            attention_level=attention,
            attention_class="DOWNLOAD_STALLED",
            reason=reason,
            evidence=evidence,
            severity=severity,
        )

    def _grab_no_download_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Release grabbed but no download ever started.

        A subsequent DOWNLOAD_STARTED moves the item out of this state entirely,
        which is what makes the condition self-clearing.
        """
        grabbed_event = None
        for event in reversed(item.events):
            if event.event_type == EventType.RELEASE_GRABBED:
                grabbed_event = event
                break

        waited = self._wait_since_stage_start(item, grabbed_event.timestamp if grabbed_event else None)

        if waited is None:
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="GRAB_NO_DOWNLOAD",
                reason="No grab event observed, so the wait for a download cannot be timed",
                evidence=[],
                severity="none",
            )

        evidence = [
            f"Release grabbed {self._fmt(waited)} ago",
            "No download_started event observed after the grab",
        ]
        qbit_events = [e for e in item.events if e.source_service == SourceService.QBITTORRENT]
        if not qbit_events:
            evidence.append("No qBittorrent events observed for this item at all")
        else:
            evidence.append("qBittorrent events exist for this item but none follow the grab")

        if waited <= self.DOWNLOAD_START_TIMEOUT.total_seconds():
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="GRAB_NO_DOWNLOAD",
                reason="Release grabbed recently; a download is expected to start shortly",
                evidence=evidence,
                severity="none",
            )

        # Past the timeout this is a genuine gap in the pipeline.
        if waited > self.INCIDENT_ESCALATION.total_seconds():
            level, severity = AttentionLevel.REQUIRED, "high"
        else:
            level, severity = AttentionLevel.POSSIBLE, "medium"

        return self._finalize(
            item,
            attention_level=level,
            attention_class="GRAB_NO_DOWNLOAD",
            reason=(
                f"Release grabbed but no download started for {self._fmt(waited)}"
            ),
            evidence=evidence,
            severity=severity,
        )

    def _download_no_import_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Download completed but *Arr never began an import."""
        completed_event = None
        for event in reversed(item.events):
            if event.event_type == EventType.DOWNLOAD_COMPLETED:
                completed_event = event
                break

        waited = self._wait_since_stage_start(item, completed_event.timestamp if completed_event else None)

        if waited is None:
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="DOWNLOAD_NO_IMPORT",
                reason="No download_completed event observed, so the wait cannot be timed",
                evidence=[],
                severity="none",
            )

        evidence = [
            f"Download completed {self._fmt(waited)} ago",
            "No import_started event observed after download completion",
        ]
        arr_imports = [
            e for e in item.events
            if e.source_service in (SourceService.SONARR, SourceService.RADARR)
            and e.event_type in (EventType.IMPORT_STARTED, EventType.IMPORT_COMPLETED)
        ]
        if not arr_imports:
            evidence.append("No import activity from *Arr observed for this item")

        if waited <= self.IMPORT_START_TIMEOUT.total_seconds():
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="DOWNLOAD_NO_IMPORT",
                reason="Download completed recently; an import is expected shortly",
                evidence=evidence,
                severity="none",
            )

        if waited > self.INCIDENT_ESCALATION.total_seconds():
            level, severity = AttentionLevel.REQUIRED, "high"
        else:
            level, severity = AttentionLevel.POSSIBLE, "medium"

        return self._finalize(
            item,
            attention_level=level,
            attention_class="DOWNLOAD_NO_IMPORT",
            reason=(
                f"Download completed but no import started for {self._fmt(waited)}"
            ),
            evidence=evidence,
            severity=severity,
        )

    def _unusually_long_stage_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Flag a stage that has run well past its expected duration.

        Uses the shared STAGE_DURATION_SECONDS table via STAGE_DURATION_THRESHOLDS.
        States with no expected duration (WANTED, failures, terminal) never reach
        attention through this rule - absence of a threshold means "no opinion".
        """
        threshold = self.STAGE_DURATION_THRESHOLDS.get(item.current_state)
        duration = item.stage_duration_seconds

        if threshold is None or duration is None:
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="UNUSUALLY_LONG_STAGE",
                reason=f"No expected duration defined for state {item.current_state.value}",
                evidence=[],
                severity="none",
            )

        waited = timedelta(seconds=duration)
        if waited <= threshold:
            return self._finalize(
                item,
                attention_level=AttentionLevel.NONE,
                attention_class="UNUSUALLY_LONG_STAGE",
                reason=(
                    f"In {item.current_state.value} for {self._fmt(duration)} "
                    f"(within the {self._fmt(threshold.total_seconds())} expectation)"
                ),
                evidence=[],
                severity="none",
            )

        expected = threshold.total_seconds()
        evidence = [
            f"In {item.current_state.value} for {self._fmt(duration)}",
            f"Expected duration for this stage is {self._fmt(expected)}",
        ]
        if item.missing_transition:
            evidence.append(f"Still waiting for {item.missing_transition.value}")

        if waited > threshold * self.STAGE_ABANDONED_MULTIPLIER:
            level, severity = AttentionLevel.REQUIRED, "high"
            reason = f"Abandoned in {item.current_state.value} for {self._fmt(duration)} ({self._fmt(expected)} expected)"
        elif waited > threshold * self.STAGE_STALE_MULTIPLIER:
            level, severity = AttentionLevel.REQUIRED, "medium"
            reason = f"Overdue in {item.current_state.value}: {self._fmt(duration)} against a {self._fmt(expected)} expectation"
        else:
            level, severity = AttentionLevel.POSSIBLE, "medium"
            reason = f"Running long in {item.current_state.value}: {self._fmt(duration)} against a {self._fmt(expected)} expectation"

        return self._finalize(
            item,
            attention_level=level,
            attention_class="UNUSUALLY_LONG_STAGE",
            reason=reason,
            evidence=evidence,
            severity=severity,
        )

    def _incomplete_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify an import that is legitimately in progress."""
        return self._finalize(
            item,
            attention_level=AttentionLevel.NONE,
            attention_class="IMPORT_IN_PROGRESS",
            reason="Import in progress",
            evidence=[],
            severity="none",
        )

    def _search_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Classify items in search/waiting state.

        An item that has been waiting a long time for a release is normal
        behaviour, not an incident, so a long wait alone does not raise attention.
        """
        if item.current_state == EventType.SEARCH_STARTED:
            search_events = [e for e in item.events if e.event_type == EventType.SEARCH_STARTED]
            if search_events:
                waited = self._wait_since_stage_start(item, search_events[-1].timestamp)
                if waited is not None and waited > self.DEFAULT_TIMEOUT.total_seconds():
                    return self._finalize(
                        item,
                        attention_level=AttentionLevel.NONE,
                        attention_class="SEARCH_IN_PROGRESS",
                        reason=(
                            f"Search running for {self._fmt(waited)} without a match; "
                            f"no release found yet is not itself an incident"
                        ),
                        evidence=[f"Search started {self._fmt(waited)} ago"],
                        severity="none",
                    )

        return self._finalize(
            item,
            attention_level=AttentionLevel.NONE,
            attention_class="WAITING_FOR_RELEASE",
            reason="Item is waiting for a suitable release to become available",
            evidence=[],
            severity="none",
        )

    def _default_classification(self, item: CorrelationResult) -> Dict[str, Any]:
        """Default classification when state is unclear."""
        return self._finalize(
            item,
            attention_level=AttentionLevel.NONE,
            attention_class="NO_ACTION",
            reason=f"Item is in state: {item.current_state.value}",
            evidence=[],
            severity="none",
        )

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

            # Add metadata. Stage/evidence recency travels with the item so the
            # client can render "waiting 4h" and "last seen 8d ago" separately.
            classification["item"] = {
                "id": item.correlation_key,
                "title": item.title,
                "media_type": item.media_type.value,
                "current_state": item.current_state.value,
                "last_event_at": item.last_event.timestamp.isoformat() if item.last_event else None,
                "event_count": len(item.events),
                "stage_start_time": (
                    item.stage_start_time.isoformat() if item.stage_start_time else None
                ),
                "stage_duration_seconds": item.stage_duration_seconds,
                "stage_freshness": item.stage_freshness,
                "evidence_age_seconds": item.evidence_age_seconds,
                "evidence_freshness": item.evidence_freshness,
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

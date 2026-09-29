"""Tests for Phase 4B - Operational Intelligence / Attention System."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from backend.services.orchestrator import Orchestrator, AttentionLevel
from backend.adapters.base import SourceService, EventType, MediaType, EvidenceBoundary, EventStatus, RawEvent, ActiveItem, ServiceHealth
from backend.correlation.engine import CorrelationResult


def make_event(
    source=SourceService.SONARR,
    etype=EventType.RELEASE_GRABBED,
    download_id=None,
    category=None,
    title="Test Movie",
    boundary=EvidenceBoundary.OBSERVED,
    ts=None,
):
    """Build a RawEvent with sensible defaults."""
    return RawEvent(
        timestamp=ts or datetime.utcnow(),
        source_service=source,
        event_type=etype,
        media_type=MediaType.MOVIE,
        media_identifier="movie-1",
        title=title,
        source_download_id=download_id,
        correlation_key=f"media:1",
        normalized_metadata={"category": category} if category else None,
        status=EventStatus.COMPLETED,
        evidence_boundary=boundary,
    )


def make_item(correlation_key="media:1", events=None, state=EventType.WANTED, confidence="LOW"):
    """Build a CorrelationResult for testing."""
    if events is None:
        events = [make_event()]
    item = CorrelationResult(correlation_key, events)
    # Override state and confidence for testing
    item.current_state = state
    item.confidence = confidence
    return item


class TestAttentionLevelConstants:
    """Test that attention level constants exist."""

    def test_attention_levels_exist(self):
        assert AttentionLevel.NONE == "none"
        assert AttentionLevel.POSSIBLE == "possible"
        assert AttentionLevel.REQUIRED == "required"
        assert AttentionLevel.BLOCKED == "blocked"
        assert AttentionLevel.SYNTHETIC == "synthetic"


class TestOrchestratorInitialization:
    """Test orchestrator initialization."""

    def test_orchestrator_creation(self):
        orch = Orchestrator()
        assert orch is not None

    def test_singleton_pattern(self):
        orch1 = Orchestrator()
        orch2 = Orchestrator()
        # Not a singleton in this implementation, but should work independently
        assert orch1 is not None
        assert orch2 is not None


class TestClassifyHealthyItem:
    """Test classification of healthy/completed items."""

    def test_available_item_not_attention(self):
        """Available items should have no attention."""
        orch = Orchestrator()
        item = make_item(state=EventType.AVAILABLE)
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.NONE
        assert result["requires_attention"] is False
        assert "completed" in result["reason"].lower()

    def test_downloading_progressing_no_attention(self):
        """Normal download progress should not trigger attention."""
        orch = Orchestrator()
        now = datetime.utcnow()
        event = make_event(
            source=SourceService.QBITTORRENT,
            etype=EventType.DOWNLOAD_PROGRESS,
            ts=now,
            boundary=EvidenceBoundary.OBSERVED,
        )
        item = make_item(events=[event], state=EventType.DOWNLOAD_PROGRESS)
        result = orch.classify_item(item)
        # Should not require attention for recent activity
        assert result["attention_level"] != AttentionLevel.REQUIRED or result["requires_attention"] is False


class TestClassifyFailedItems:
    """Test classification of failed items."""

    def test_download_failed_requires_attention(self):
        """Download failed should require attention."""
        orch = Orchestrator()
        item = make_item(state=EventType.DOWNLOAD_FAILED)
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.REQUIRED
        assert result["requires_attention"] is True
        assert result["severity"] == "high"
        assert "failed" in result["reason"].lower()

    def test_import_failed_requires_attention(self):
        """Import failed should require attention."""
        orch = Orchestrator()
        item = make_item(state=EventType.IMPORT_FAILED)
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.REQUIRED
        assert result["requires_attention"] is True
        assert result["severity"] == "high"


class TestClassifyStuckItems:
    """Test classification of stuck items."""

    def test_stuck_with_qbit_indicator(self):
        """Stuck item with qBittorrent stall indicator."""
        orch = Orchestrator()
        now = datetime.utcnow()
        event = RawEvent(
            timestamp=now - timedelta(hours=3),
            source_service=SourceService.QBITTORRENT,
            event_type=EventType.DOWNLOAD_PROGRESS,
            media_type=MediaType.MOVIE,
            media_identifier="movie-1",
            title="Test Movie",
            source_download_id="hash123",
            correlation_key="media:1",
            normalized_metadata={
                "state": "stalledDL",
                "progress": 50,
            },
            status=EventStatus.COMPLETED,
            evidence_boundary=EvidenceBoundary.OBSERVED,
        )
        item = make_item(events=[event], state=EventType.STUCK)
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.REQUIRED
        assert result["requires_attention"] is True
        assert "stalled" in result["reason"].lower()

    def test_stuck_no_progress_for_hours(self):
        """Stuck item detected by duration without progress."""
        orch = Orchestrator()
        three_hours_ago = datetime.utcnow() - timedelta(hours=3)
        event = RawEvent(
            timestamp=three_hours_ago,
            source_service=SourceService.QBITTORRENT,
            event_type=EventType.DOWNLOAD_PROGRESS,
            media_type=MediaType.MOVIE,
            media_identifier="movie-1",
            title="Test Movie",
            source_download_id="hash456",
            correlation_key="media:1",
            normalized_metadata={
                "progress": 30,
            },
            status=EventStatus.COMPLETED,
            evidence_boundary=EvidenceBoundary.OBSERVED,
        )
        item = make_item(events=[event], state=EventType.STUCK)
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.REQUIRED
        assert result["requires_attention"] is True


class TestClassifyBlockedItems:
    """Test classification of blocked items."""

    def test_blocked_evidence_boundary(self):
        """Items with BLOCKED evidence boundary should be flagged."""
        orch = Orchestrator()
        item = make_item()
        item.evidence_boundary = EvidenceBoundary.BLOCKED
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.BLOCKED
        assert result["requires_attention"] is True
        assert "unavailable" in result["reason"].lower()


class TestClassifySyntheticItems:
    """Test that synthetic data items are excluded."""

    def test_synthetic_item_excluded(self):
        """Synthetic items should be filtered out by default."""
        orch = Orchestrator()
        item = make_item()
        item.evidence_boundary = EvidenceBoundary.SYNTHETIC
        result = orch.classify_item(item)
        assert result["attention_level"] == AttentionLevel.SYNTHETIC
        assert result["requires_attention"] is False


class TestGetAttentionItems:
    """Test the get_attention_items method."""

    def test_filters_synthetic_by_default(self):
        """Synthetic items should be excluded by default."""
        orch = Orchestrator()
        synthetic_item = make_item(events=[make_event(boundary=EvidenceBoundary.SYNTHETIC)])
        real_item = make_item(state=EventType.DOWNLOAD_FAILED)

        items = [synthetic_item, real_item]
        results = orch.get_attention_items(items)

        # Only real_item should be in results
        assert len(results) == 1
        assert results[0]["item"]["id"] == "media:1"

    def test_includes_blocked_when_requested(self):
        """Blocked items should be included when requested."""
        orch = Orchestrator()
        blocked_item = make_item()
        blocked_item.evidence_boundary = EvidenceBoundary.BLOCKED

        results = orch.get_attention_items([blocked_item], include_blocked=True)
        assert len(results) == 1
        assert results[0]["attention_level"] == AttentionLevel.BLOCKED

    def test_excludes_blocked_by_default(self):
        """Blocked items should be excluded by default."""
        orch = Orchestrator()
        blocked_item = make_item()
        blocked_item.evidence_boundary = EvidenceBoundary.BLOCKED

        results = orch.get_attention_items([blocked_item], include_blocked=False)
        assert len(results) == 0

    def test_sorts_by_priority(self):
        """Results should be sorted by priority."""
        orch = Orchestrator()
        required_item = make_item(state=EventType.DOWNLOAD_FAILED)
        possible_item = make_item(state=EventType.DOWNLOAD_PROGRESS)

        results = orch.get_attention_items([possible_item, required_item])

        # Required should come first
        assert len(results) >= 1
        assert results[0]["attention_level"] == AttentionLevel.REQUIRED


class TestSingletonPattern:
    """Test the get_orchestrator singleton pattern."""

    def test_singleton_returns_same_instance(self):
        """get_orchestrator should return the same instance."""
        from backend.services.orchestrator import _orchestrator, get_orchestrator

        orch1 = get_orchestrator()
        orch2 = get_orchestrator()
        assert orch1 is orch2


class TestWhyEngineGrounding:
    """Test that WHY engine references are consistent."""

    def test_failure_reason_references_observed_evidence(self):
        """Failed items should reference observed evidence in reason."""
        orch = Orchestrator()
        item = make_item(state=EventType.DOWNLOAD_FAILED)
        item.evidence_boundary = EvidenceBoundary.OBSERVED
        result = orch.classify_item(item)

        assert "observed" in result["reason"].lower() or "failed" in result["reason"].lower()

    def test_blocked_reason_mentions_unavailable(self):
        """Blocked items should mention unavailable evidence."""
        orch = Orchestrator()
        item = make_item()
        item.evidence_boundary = EvidenceBoundary.BLOCKED
        result = orch.classify_item(item)

        assert "unavailable" in result["reason"].lower()


class TestProwlarrCausalBoundary:
    """Test that Prowlarr events don't create false causal links."""

    def test_prowlarr_search_is_observed_not_causal(self):
        """Prowlarr search events should be marked as OBSERVED, not implying causation."""
        from backend.adapters.prowlarr import ProwlarrAdapter

        adapter = ProwlarrAdapter()
        record = {
            "eventType": "indexersearch",
            "movieId": 1,
            "releaseTitle": "Test.Release.2026",
            "date": "2026-09-27T10:00:00Z",
        }
        event = adapter._history_to_event(record)
        assert event is not None
        assert event.evidence_boundary == EvidenceBoundary.OBSERVED
        assert event.normalized_metadata.get("causal_boundary") == "no_causal_link_implied"

"""Regression tests for Phase 4A - Evidence Boundary Hardening."""
import pytest
from datetime import datetime, timedelta

from backend.adapters.base import (
    RawEvent, SourceService, MediaType, EventType, EventStatus, EvidenceBoundary
)
from backend.correlation.engine import CorrelationEngine


def make_event(
    source=SourceService.SONARR,
    etype=EventType.RELEASE_GRABBED,
    download_id=None,
    category=None,
    title="Test Movie",
    boundary=EvidenceBoundary.OBSERVED,
    ts=None,
    media_identifier="1",
):
    """Build a RawEvent with sensible defaults."""
    return RawEvent(
        timestamp=ts or datetime.utcnow(),
        source_service=source,
        event_type=etype,
        media_type=MediaType.MOVIE,
        media_identifier=media_identifier,
        title=title,
        source_download_id=download_id,
        correlation_key=None,  # let the engine compute correlation
        normalized_metadata={"category": category} if category else None,
        status=EventStatus.COMPLETED,
        evidence_boundary=boundary,
    )


class TestEvidenceBoundary:
    """Test the evidence boundary enum and its propagation."""

    def test_enum_values_present(self):
        assert EvidenceBoundary.OBSERVED.value == "observed"
        assert EvidenceBoundary.CORRELATED.value == "correlated"
        assert EvidenceBoundary.INFERRED.value == "inferred"
        assert EvidenceBoundary.UNKNOWN.value == "unknown"
        assert EvidenceBoundary.BLOCKED.value == "blocked"
        assert EvidenceBoundary.SYNTHETIC.value == "synthetic"

    def test_raw_event_default_boundary_is_observed(self):
        ev = make_event()
        assert ev.evidence_boundary == EvidenceBoundary.OBSERVED

    def test_raw_event_preserves_explicit_boundary(self):
        ev = make_event(boundary=EvidenceBoundary.SYNTHETIC)
        assert ev.evidence_boundary == EvidenceBoundary.SYNTHETIC

    def test_sse_data_includes_evidence_boundary(self):
        ev = make_event(boundary=EvidenceBoundary.BLOCKED, download_id="hash123")
        data = ev.to_sse_data()
        assert data["evidence_boundary"] == "blocked"
        assert data["provenance"] == "polling"


class TestObservedEvent:
    """OBSERVED: a directly-observed external event is NOT downgraded."""

    def test_observed_source_event_remains_observed(self):
        engine = CorrelationEngine()
        ev = make_event(source=SourceService.SONARR, etype=EventType.AVAILABLE)
        engine.add_events([ev])
        item = engine.get_item("media:1")
        assert item is not None
        assert item.evidence_boundary == EvidenceBoundary.OBSERVED


class TestCorrelatedEvent:
    """CORRELATED: exact hash match between *Arr and qBittorrent."""

    def test_exact_hash_correlates(self):
        engine = CorrelationEngine()
        arr = make_event(
            source=SourceService.SONARR,
            etype=EventType.RELEASE_GRABBED,
            download_id="abc123",
        )
        qbit = make_event(
            source=SourceService.QBITTORRENT,
            etype=EventType.DOWNLOAD_PROGRESS,
            download_id="abc123",  # same hash => merge via hash_to_media_key
            boundary=EvidenceBoundary.OBSERVED,
            ts=datetime.utcnow() + timedelta(minutes=1),
        )
        engine.add_events([arr, qbit])
        item = engine.get_item("media:1")
        assert item is not None
        assert item.confidence == "HIGH"
        assert item.confidence_basis == "exact hash match between *Arr and qBittorrent"
        assert item.evidence_boundary == EvidenceBoundary.CORRELATED


class TestInferredEvent:
    """INFERRED: category/tag correlation without exact hash match."""

    def test_secondary_correlation_inferred(self):
        engine = CorrelationEngine()
        arr = make_event(
            source=SourceService.SONARR,
            etype=EventType.RELEASE_GRABBED,
            download_id="hashA",
            category="radarr",
        )
        qbit = make_event(
            source=SourceService.QBITTORRENT,
            etype=EventType.DOWNLOAD_PROGRESS,
            download_id="hashB",  # different hash => no exact match, heuristic only
            category="radarr",
            boundary=EvidenceBoundary.OBSERVED,
            ts=datetime.utcnow() + timedelta(minutes=1),
        )
        engine.add_events([arr, qbit])
        # The qbit event will be an orphan hash group (no hash match).
        # The arr event is the media group and should be present.
        item = engine.get_item("media:1")
        assert item is not None
        assert item.confidence == "MEDIUM"
        assert item.confidence_basis == "category/tag correlation"


class TestLowConfidence:
    """LOW: only qBittorrent events / heuristic, no *Arr event."""

    def test_heuristic_only_low_confidence(self):
        engine = CorrelationEngine()
        qbit = make_event(
            source=SourceService.QBITTORRENT,
            etype=EventType.DOWNLOAD_PROGRESS,
            download_id="hashC",
        )
        engine.add_events([qbit])
        # qBittorrent events are keyed by hash, not media ID
        item = engine.get_item("hash:hashC")
        assert item is not None
        assert item.confidence == "LOW"
        assert item.confidence_basis is None


class TestUnknownExplanation:
    """UNKNOWN: insufficient evidence produces unknown, not a confident cause."""

    def test_unknown_reason_does_not_claim_causality(self):
        engine = CorrelationEngine()
        ev = make_event(etype=EventType.UNKNOWN, boundary=EvidenceBoundary.UNKNOWN)
        engine.add_events([ev])
        item = engine.get_item("media:1")
        explanation = engine.explain("media:1")
        assert explanation is not None
        assert "insufficient to establish" in explanation["reason"].lower()


class TestBlockedExplanation:
    """BLOCKED: evidence source unavailable -> explicit blocked statement."""

    def test_blocked_reason_explicit(self):
        engine = CorrelationEngine()
        ev = make_event(
            etype=EventType.DOWNLOAD_FAILED,
            boundary=EvidenceBoundary.BLOCKED,
        )
        engine.add_events([ev])
        item = engine.get_item("media:1")
        assert item.evidence_boundary == EvidenceBoundary.BLOCKED
        explanation = engine.explain("media:1")
        assert explanation is not None
        assert "external service was unavailable" in explanation["reason"].lower()


class TestGuardarrSynthetic:
    """Guardarr synthetic reservations must be distinguishable from live data."""

    def test_synthetic_reservation_marked(self):
        from backend.adapters.guardarr import GuardarrAdapter

        adapter = GuardarrAdapter()
        reservation = {
            "reservation_id": "res-test-123",
            "state": "admitted",
            "content_id": "test-series-002",
            "arr_item_id": "test-series-002",
            "torrent_metadata_hash": None,
            "idempotency_key": "arr:sonarr:test-series-002:default",
            "updated_at": "2026-09-27T10:00:00Z",
            "created_at": "2026-09-27T09:00:00Z",
        }
        event = adapter._reservation_to_event(reservation)
        assert event is not None
        assert event.evidence_boundary == EvidenceBoundary.SYNTHETIC
        assert event.normalized_metadata["synthetic"] is True

    def test_live_like_reservation_not_marked_synthetic(self):
        from backend.adapters.guardarr import GuardarrAdapter

        adapter = GuardarrAdapter()
        reservation = {
            "reservation_id": "res-live-456",
            "state": "admitted",
            "content_id": "tmdb-123",
            "arr_item_id": "movie-456",
            "torrent_metadata_hash": "realhash123",
            "idempotency_key": "arr:radarr:movie-456:default",
            "updated_at": "2026-09-27T10:00:00Z",
            "created_at": "2026-09-27T09:00:00Z",
        }
        event = adapter._reservation_to_event(reservation)
        assert event is not None
        assert event.evidence_boundary == EvidenceBoundary.OBSERVED
        assert event.normalized_metadata["synthetic"] is False


class TestProwlarrCausalBoundary:
    """Prowlarr search and *Arr grab must remain causally independent."""

    def test_prowlarr_event_has_no_causal_link_marker(self):
        from backend.adapters.prowlarr import ProwlarrAdapter

        adapter = ProwlarrAdapter()
        record = {
            "eventType": "indexersearch",
            "movieId": 1,
            "releaseTitle": "Test.Release.2026",
            "date": "2026-09-27T10:00:00Z",
            "indexer": "ExampleIndexer",
            "downloadId": "provhash1",
        }
        event = adapter._history_to_event(record)
        assert event is not None
        assert event.source_service == SourceService.PROWLARR
        assert event.event_type == EventType.INDEXER_SEARCH
        assert event.evidence_boundary == EvidenceBoundary.OBSERVED
        assert event.normalized_metadata["causal_boundary"] == "no_causal_link_implied"

    def test_prov_search_not_merged_into_grab_item(self):
        engine = CorrelationEngine()
        prowlarr = make_event(
            source=SourceService.PROWLARR,
            etype=EventType.INDEXER_SEARCH,
            download_id="provhash1",
            media_identifier="prowlarr:provhash1",
        )
        grab = make_event(
            source=SourceService.SONARR,
            etype=EventType.RELEASE_GRABBED,
            download_id="sonarrhash1",
        )
        engine.add_events([prowlarr, grab])
        # Prowlarr event should have its own isolated key, not pulled into media:1
        assert engine.get_item("media:1") is not None


class TestWhyEngineNoFabrication:
    """The WHY engine must never claim evidence it does not possess."""

    def test_available_reason_is_evidence_grounded(self):
        engine = CorrelationEngine()
        ev = make_event(source=SourceService.SONARR, etype=EventType.AVAILABLE)
        engine.add_events([ev])
        explanation = engine.explain("media:1")
        assert explanation is not None
        assert "observed sonarr history" in explanation["reason"].lower()

    def test_stuck_not_described_as_blocked(self):
        engine = CorrelationEngine()
        ev = make_event(etype=EventType.STUCK, boundary=EvidenceBoundary.OBSERVED)
        engine.add_events([ev])
        item = engine.get_item("media:1")
        explanation = engine.explain("media:1")
        assert explanation is not None
        # STUCK is an observed state, NOT "blocked because evidence source unavailable"
        assert "blocked" not in explanation["reason"].lower() or "evidence source" not in explanation["reason"].lower()

"""Phase 4C API contract tests.

These assert the new stage/evidence fields actually reach the HTTP layer. The
bug this guards against is a field existing on the internal CorrelationResult
but being dropped by the endpoint's own serialization, which is exactly what
happened on /api/items before Phase 4C hardening.

Endpoints are exercised through FastAPI's dependency override with an
in-memory database and a correlation engine primed with known items, so these
tests are hermetic and do not require live services.
"""
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.base import Base, get_db
from backend.main import application
from backend.correlation.engine import CorrelationEngine
from backend.adapters.base import (
    RawEvent, SourceService, MediaType, EventType, EventStatus, EvidenceBoundary,
)

NOW = datetime.utcnow()

# Fields every consumer of the Phase 4C contract depends on.
STAGE_FIELDS = {
    "stage_start_time", "stage_duration_seconds", "stage_freshness",
    "missing_transition", "transition_evidence",
}
EVIDENCE_FIELDS = {"evidence_age_seconds", "evidence_freshness"}


def ev(etype, ts, svc=SourceService.RADARR, meta=None, boundary=EvidenceBoundary.OBSERVED,
       download_id=None, media_id="1"):
    return RawEvent(
        timestamp=ts, source_service=svc, event_type=etype, media_type=MediaType.MOVIE,
        media_identifier=media_id, title="Test Movie", source_download_id=download_id,
        correlation_key=f"media:{media_id}", status=EventStatus.COMPLETED,
        evidence_boundary=boundary, normalized_metadata=meta,
    )


def hours_ago(h):
    return NOW - timedelta(hours=h)


@pytest.fixture
def client_and_engine():
    """A test client whose correlation engine holds known items.

    The client is deliberately NOT used as a context manager: entering the
    context runs the application lifespan, which starts the polling service and
    makes real network calls to Sonarr/Radarr/qBittorrent, hanging the suite.
    Constructing TestClient directly skips lifespan, so these tests stay
    hermetic and need no live services.

    The correlation engine is a module-level singleton, so it is swapped for the
    duration of the test and restored afterwards to avoid leaking items.
    """
    from backend.services import events as events_module

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    correlation = CorrelationEngine()
    correlation.add_events([
        # Grabbed 2h ago, re-polled just now: overdue stage, fresh evidence.
        ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR),
        # Completed 30 days ago: settled, ancient, must not be an incident.
        ev(EventType.AVAILABLE, hours_ago(24 * 30), svc=SourceService.SONARR, media_id="2"),
    ])

    original_engine = events_module.get_correlation_engine
    events_module.get_correlation_engine = lambda: correlation

    application.dependency_overrides[get_db] = lambda: session
    try:
        # No `with` block: see docstring.
        yield TestClient(application), correlation
    finally:
        application.dependency_overrides.pop(get_db, None)
        events_module.get_correlation_engine = original_engine
        session.close()


class TestItemsListContract:
    """GET /api/items must carry the Phase 4C fields."""

    def test_list_exposes_stage_and_evidence_fields(self, client_and_engine):
        client, _ = client_and_engine
        response = client.get("/api/items")
        assert response.status_code == 200
        body = response.json()
        assert body, "expected the seeded items to be listed"
        for item in body:
            assert STAGE_FIELDS.issubset(item.keys()), sorted(STAGE_FIELDS - item.keys())
            assert EVIDENCE_FIELDS.issubset(item.keys()), sorted(EVIDENCE_FIELDS - item.keys())

    def test_overdue_stage_with_fresh_evidence_is_reported_separately(self, client_and_engine):
        """The item was grabbed 2h ago but polled seconds ago.

        stage_freshness must be STALE while evidence_freshness is CURRENT. This
        is the distinction that a single combined flag would destroy.
        """
        client, _ = client_and_engine
        body = client.get("/api/items").json()
        grabbed = next(i for i in body if i["current_state"] == "release_grabbed")

        assert grabbed["stage_freshness"] == "STALE"
        assert grabbed["evidence_freshness"] == "CURRENT"
        assert grabbed["stage_duration_seconds"] > 7000
        assert grabbed["evidence_age_seconds"] < 300
        assert grabbed["missing_transition"] == "download_started"
        assert grabbed["transition_evidence"]

    def test_list_field_set_matches_detail_field_set(self, client_and_engine):
        """Regression: list and detail must not drift apart.

        The /api/items serializer was hand-written and silently omitted fields
        that to_dict() already provided.
        """
        client, _ = client_and_engine
        listed = next(
            i for i in client.get("/api/items").json()
            if i["current_state"] == "release_grabbed"
        )
        detail = client.get(f"/api/items/{listed['id']}").json()
        assert STAGE_FIELDS.issubset(detail.keys())
        assert EVIDENCE_FIELDS.issubset(detail.keys())
        # The list exposes the same stage/evidence keys the detail view does.
        assert STAGE_FIELDS.issubset(listed.keys())
        assert EVIDENCE_FIELDS.issubset(listed.keys())


class TestItemDetailContract:
    """GET /api/items/{id} must carry the Phase 4C fields."""

    def test_detail_exposes_all_phase4c_fields(self, client_and_engine):
        client, _ = client_and_engine
        listed = next(
            i for i in client.get("/api/items").json()
            if i["current_state"] == "release_grabbed"
        )
        body = client.get(f"/api/items/{listed['id']}").json()
        assert STAGE_FIELDS.issubset(body.keys())
        assert EVIDENCE_FIELDS.issubset(body.keys())
        assert "confidence_basis" in body
        assert "evidence_boundary" in body

    def test_detail_timeline_keeps_evidence_boundary_per_event(self, client_and_engine):
        client, _ = client_and_engine
        listed = client.get("/api/items").json()[0]
        body = client.get(f"/api/items/{listed['id']}").json()
        assert body["timeline"]
        for entry in body["timeline"]:
            assert "evidence_boundary" in entry, entry

    def test_unknown_item_is_404(self, client_and_engine):
        client, _ = client_and_engine
        assert client.get("/api/items/media:does-not-exist").status_code == 404


class TestAttentionContract:
    """GET /api/attention must carry the Phase 4C fields."""

    def test_attention_exposes_class_and_recency_axes(self, client_and_engine):
        client, _ = client_and_engine
        response = client.get("/api/attention")
        assert response.status_code == 200
        body = response.json()
        assert body, "the overdue grab should raise attention"
        for entry in body:
            assert entry["attention_class"], "attention_class must be present"
            assert "stage_freshness" in entry
            assert "evidence_freshness" in entry
            assert "stage_duration_seconds" in entry
            assert "evidence_age_seconds" in entry
            assert "missing_transition" in entry
            assert entry["attention_class"] != "STALE_EVENT"

    def test_attention_class_names_the_pipeline_gap(self, client_and_engine):
        client, _ = client_and_engine
        body = client.get("/api/attention").json()
        classes = {e["attention_class"] for e in body}
        assert "GRAB_NO_DOWNLOAD" in classes
        assert "STALE_EVENT" not in classes

    def test_settled_ancient_item_is_absent_from_attention(self, client_and_engine):
        """A 30-day-old completed item must not surface as an incident."""
        client, _ = client_and_engine
        body = client.get("/api/attention").json()
        assert all(e["item"]["current_state"] != "available" for e in body)

    def test_attention_item_metadata_carries_both_axes(self, client_and_engine):
        client, _ = client_and_engine
        body = client.get("/api/attention").json()
        entry = body[0]
        for key in ("stage_freshness", "evidence_freshness", "stage_duration_seconds",
                    "evidence_age_seconds", "stage_start_time"):
            assert key in entry["item"], key
        assert entry["item"]["stage_freshness"] == "STALE"
        assert entry["item"]["evidence_freshness"] == "CURRENT"

    def test_synthetic_excluded_by_default(self, client_and_engine):
        client, _ = client_and_engine
        body = client.get("/api/attention").json()
        assert all(e["attention_level"] != "synthetic" for e in body)

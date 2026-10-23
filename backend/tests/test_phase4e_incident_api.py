"""Phase 4E: the incident API contract the Incident UI consumes.

Phase 4D proves the lifecycle works. These tests pin the *surface contract*
the frontend was built against, so a backend edit cannot silently break the
UI:

* the exact field set of list/detail responses (the IncidentCard and
  IncidentDetail views render precisely these keys),
* the stats shape the nav counter and the incidents page read,
* 400/404/409 semantics with human-readable details,
* list ordering that puts still-open work ahead of history.

Same hermetic harness as the Phase 4C/4D suites: no lifespan, no network.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.adapters.base import (
    RawEvent, SourceService, MediaType, EventType, EventStatus, EvidenceBoundary,
)
from backend.correlation.engine import CorrelationEngine
from backend.database.base import Base, get_db
from backend.main import application
from backend.services import events as events_module
from backend.services.incidents import reset_incident_store
from datetime import datetime, timedelta

NOW = datetime.utcnow()


def ev(etype, ts, svc=SourceService.RADARR, media_id="1", boundary=EvidenceBoundary.OBSERVED, meta=None):
    return RawEvent(
        timestamp=ts, source_service=svc, event_type=etype, media_type=MediaType.MOVIE,
        media_identifier=media_id, title="Test Movie", correlation_key=f"media:{media_id}",
        status=EventStatus.COMPLETED, evidence_boundary=boundary, normalized_metadata=meta,
    )


def hours_ago(h):
    return NOW - timedelta(hours=h)


@pytest.fixture
def client_and_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    correlation = CorrelationEngine()
    correlation.add_events([
        ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR),
        ev(EventType.AVAILABLE, hours_ago(24 * 30), svc=SourceService.SONARR, media_id="2"),
    ])

    original_engine = events_module.get_correlation_engine
    events_module.get_correlation_engine = lambda: correlation

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    tmp_path = tmp.name
    os.environ["INCIDENT_STORE_PATH"] = tmp_path

    reset_incident_store()

    application.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(application), correlation, tmp_path
    finally:
        application.dependency_overrides.pop(get_db, None)
        events_module.get_correlation_engine = original_engine
        session.close()
        reset_incident_store()
        os.environ.pop("INCIDENT_STORE_PATH", None)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def seed_incident(client):
    """One synced, OPEN incident to work with."""
    client.post("/api/incidents/sync")
    return client.get("/api/incidents").json()[0]


class TestIncidentResponseContract:
    """The exact fields the IncidentCard / IncidentDetail views render."""

    def test_list_rows_carry_every_ui_field(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)

        for field in [
            "id", "correlation_key", "attention_class", "status",
            "attention_level", "severity", "reason", "evidence",
            "evidence_boundary", "state", "missing_transition",
            "stage_freshness", "evidence_freshness",
            "stage_duration_seconds", "evidence_age_seconds",
            "title", "media_type", "last_evidence_at",
            "first_observed_at", "last_observed_at",
            "acknowledged_at", "resolved_at", "archived_at",
            "recurrence_count",
        ]:
            assert field in incident, f"missing UI field: {field}"

        # Shape invariants the UI relies on.
        assert isinstance(incident["evidence"], list)
        assert incident["status"] == "OPEN"
        assert incident["recurrence_count"] == 0

    def test_detail_matches_list_row(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)
        detail = client.get(f"/api/incidents/{incident['id']}").json()
        assert detail == incident

    def test_status_filter_returns_only_that_state(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)
        client.post(f"/api/incidents/{incident['id']}/acknowledge")

        open_rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        ack_rows = client.get("/api/incidents", params={"status": "ACKNOWLEDGED"}).json()
        assert open_rows == []
        assert len(ack_rows) == 1 and ack_rows[0]["id"] == incident["id"]

        # Case-insensitive, per the UI's lowercase chips.
        assert len(client.get("/api/incidents", params={"status": "open"}).json()) == 0
        assert len(client.get("/api/incidents", params={"status": "acknowledged"}).json()) == 1

    def test_unknown_status_filter_is_400(self, client_and_engine):
        client, _, _ = client_and_engine
        resp = client.get("/api/incidents", params={"status": "done"})
        assert resp.status_code == 400
        assert "open" in resp.json()["detail"]  # names the valid values

    def test_unknown_incident_is_404_on_every_surface(self, client_and_engine):
        client, _, _ = client_and_engine
        seed_incident(client)
        assert client.get("/api/incidents/none").status_code == 404
        assert client.post("/api/incidents/none/acknowledge").status_code == 404
        assert client.post("/api/incidents/none/resolve").status_code == 404
        assert client.post("/api/incidents/none/archive").status_code == 404

    def test_invalid_transitions_are_409_with_guidance(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)

        too_early = client.post(f"/api/incidents/{incident['id']}/resolve")
        assert too_early.status_code == 409
        assert "acknowledge" in too_early.json()["detail"].lower()

        # Terminal state rejects everything.
        client.post(f"/api/incidents/{incident['id']}/acknowledge")
        client.post(f"/api/incidents/{incident['id']}/resolve")
        client.post(f"/api/incidents/{incident['id']}/archive")
        assert client.post(f"/api/incidents/{incident['id']}/acknowledge").status_code == 409


class TestIncidentStatsContract:
    """The nav counter and the page's stat strip read exactly these keys."""

    def test_stats_shape(self, client_and_engine):
        client, _, _ = client_and_engine
        seed_incident(client)
        stats = client.get("/api/incidents/stats").json()

        for field in ["total", "open", "acknowledged", "resolved", "archived", "active", "last_sync_at"]:
            assert field in stats, f"missing stats field: {field}"
        assert stats["total"] == 1
        assert stats["open"] == 1
        assert stats["active"] == 1
        assert stats["last_sync_at"] is not None

    def test_stats_track_the_lifecycle(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)
        for action in ("acknowledge", "resolve", "archive"):
            client.post(f"/api/incidents/{incident['id']}/{action}")
        stats = client.get("/api/incidents/stats").json()
        assert stats["archived"] == 1
        assert stats["active"] == 0
        assert stats["total"] == 1  # history is never deleted


class TestIncidentOrderingAndMaintenance:
    """Open work first; maintenance endpoints behave for the UI's 'Sync now'."""

    def test_list_orders_open_work_ahead_of_history(self, client_and_engine):
        client, _, _ = client_and_engine
        incident = seed_incident(client)

        # Add an older OPEN incident through the live store the API uses, so
        # memory and file stay in lockstep.
        from backend.services.incidents import get_incident_store

        store = get_incident_store()
        with store._lock:
            store._incidents["00000000000000aa"] = {
                "id": "00000000000000aa",
                "correlation_key": "media:older",
                "attention_class": "STUCK",
                "status": "OPEN",
                "first_observed_at": (NOW - timedelta(days=5)).isoformat(),
                "last_observed_at": (NOW - timedelta(days=5)).isoformat(),
                "evidence": [],
                "recurrence_count": 0,
            }
            store._save()

        rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        # Within OPEN: oldest first-observed first (longest-waiting on top).
        assert rows[0]["id"] == "00000000000000aa"
        assert rows[1]["id"] == incident["id"]

    def test_sync_reports_change_counters(self, client_and_engine):
        client, _, _ = client_and_engine
        first = client.post("/api/incidents/sync").json()
        assert first["created"] == 1
        assert first["updated"] == 0
        assert first["reset"] == 0

        # Nothing changed since: a quiet re-sync.
        second = client.post("/api/incidents/sync").json()
        assert second["created"] == 0
        assert second["updated"] == 1
        assert second["total"] == 1

    def test_cleanup_endpoint_reports_archived_count(self, client_and_engine):
        client, _, _ = client_and_engine
        result = client.post("/api/incidents/cleanup")
        assert result.status_code == 200
        body = result.json()
        assert body["archived"] == 0
        assert body["retention_days"] == 90

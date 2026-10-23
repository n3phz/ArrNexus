"""Phase 4D incident lifecycle regression tests.

Covers the incident contract the Phase 4E UI will consume:

* sync promotes attention conditions to tracked incidents (deterministic ids,
  no duplicates, synthetic data never incidentised),
* the lifecycle is strictly OPEN -> ACKNOWLEDGED -> RESOLVED -> ARCHIVED and
  invalid transitions are rejected (409),
* a resolved problem that re-appears in attention resets to OPEN (recurrence),
* cleanup only archives stale RESOLVED incidents and never mutates open work,
* stats/reporting reflect lifecycle state accurately.

The test client is constructed without entering the context manager so the
application lifespan (and its live polling) never runs; the correlation
engine is swapped in for known items, mirroring the Phase 4C contract tests.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta

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
from backend.services.incidents import (
    IncidentStore,
    get_incident_store,
    reset_incident_store,
)

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
    """Test client with a known correlation engine and an isolated incident store."""
    from backend.services import events as events_module

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    correlation = CorrelationEngine()
    correlation.add_events([
        # Overdue grab (2h in stage, fresh evidence) -> attention required-ish.
        ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR),
        # Settled item: completed 30 days ago, must never become an incident.
        ev(EventType.AVAILABLE, hours_ago(24 * 30), svc=SourceService.SONARR, media_id="2"),
    ])

    original_engine = events_module.get_correlation_engine
    events_module.get_correlation_engine = lambda: correlation

    # Isolate the module-level incident store in a temp file.
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    tmp_path = tmp.name
    os.environ["INCIDENT_STORE_PATH"] = tmp_path
    reset_incident_store()

    application.dependency_overrides[get_db] = lambda: session
    try:
        # No `with` block: entering it runs the lifespan and starts live polling.
        yield TestClient(application), correlation, tmp_path
    finally:
        application.dependency_overrides.pop(get_db, None)
        events_module.get_correlation_engine = original_engine
        session.close()
        reset_incident_store()
        os.environ.pop("INCIDENT_STORE_PATH", None)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


class TestIncidentSync:
    """POST /api/incidents/sync promotes attention into tracked incidents."""

    def test_sync_creates_open_incidents_from_attention(self, client_and_engine):
        client, _, _ = client_and_engine

        result = client.post("/api/incidents/sync")
        assert result.status_code == 200
        payload = result.json()
        assert payload["created"] == 1
        assert payload["total"] == 1  # only the overdue item produces attention

        listed = client.get("/api/incidents").json()
        assert len(listed) == 1
        incident = listed[0]
        assert incident["status"] == "OPEN"
        assert incident["correlation_key"] == "media:1"
        assert incident["attention_class"] == "GRAB_NO_DOWNLOAD"
        # The snapshot carries the Phase 4C axes, not just a title.
        # (EvidenceBoundary enum values serialize lowercase via .value.)
        assert incident["evidence_boundary"] in {"observed", "correlated", "inferred", "unknown", "blocked"}
        assert incident["first_observed_at"] is not None
        assert incident["acknowledged_at"] is None

    def test_incident_ids_are_deterministic_and_stable(self, client_and_engine):
        client, _, _ = client_and_engine

        client.post("/api/incidents/sync")
        first = client.get("/api/incidents").json()[0]["id"]

        client.post("/api/incidents/sync")
        after = client.get("/api/incidents").json()
        assert len(after) == 1  # re-observation never spawns a duplicate
        assert after[0]["id"] == first

        # The id is a pure function of (correlation_key, attention_class).
        key = IncidentStore.make_incident_id("media:1", "GRAB_NO_DOWNLOAD")
        assert key == first
        assert key != IncidentStore.make_incident_id("media:1", "STUCK")

    def test_synced_incident_survives_reloads(self, client_and_engine):
        client, _, tmp_path = client_and_engine

        client.post("/api/incidents/sync")
        with open(tmp_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        assert "incidents" in raw and raw["last_sync_at"] is not None

        # A fresh store instance over the same file sees the same incident.
        fresh = IncidentStore(tmp_path)
        assert len(fresh.list()) == 1

    def test_synthetic_never_creates_incidents(self, client_and_engine):
        client, _, _ = client_and_engine

        # Synthetic media with an overdue grab must not be tracked.
        correlation = CorrelationEngine()
        correlation.add_events([
            ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR,
               media_id="synth", boundary=EvidenceBoundary.SYNTHETIC),
            ev(EventType.RELEASE_GRABBED, NOW - timedelta(minutes=2), svc=SourceService.SONARR,
               media_id="synth", boundary=EvidenceBoundary.SYNTHETIC),
        ])
        from backend.services import events as events_module
        events_module.get_correlation_engine = lambda: correlation

        result = client.post("/api/incidents/sync").json()
        assert result["created"] == 0
        assert result["total"] == 0
        assert client.get("/api/incidents").json() == []

        # Store level: the upsert itself refuses synthetic/none levels.
        store = get_incident_store()
        synthetic_attention = {
            "attention_level": "synthetic", "attention_class": "SYNTHETIC_DATA",
            "item": {"id": "media:synth"},
        }
        assert store.upsert_from_attention(synthetic_attention) == ({}, "skipped")


class TestIncidentQueries:

    def test_list_filters_by_status(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        client.post(f"/api/incidents/{incident_id}/acknowledge")

        assert len(client.get("/api/incidents").json()) == 1
        assert len(client.get("/api/incidents", params={"status": "open"}).json()) == 0
        assert len(client.get("/api/incidents", params={"status": "acknowledged"}).json()) == 1
        bad = client.get("/api/incidents", params={"status": "bogus"})
        assert bad.status_code == 400

    def test_stats_report_lifecycle_counts(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        stats = client.get("/api/incidents/stats").json()
        assert stats["total"] == 1
        assert stats["open"] == 1
        assert stats["active"] == 1
        assert stats["last_sync_at"] is not None

        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")
        client.post(f"/api/incidents/{incident_id}/archive")

        stats = client.get("/api/incidents/stats").json()
        assert stats["archived"] == 1
        assert stats["active"] == 0  # no open or acknowledged work remains

    def test_get_incident_detail_and_404(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        detail = client.get(f"/api/incidents/{incident_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["id"] == incident_id
        assert data["title"] == "Test Movie"
        assert data["reason"]
        assert isinstance(data["evidence"], list)

        missing = client.get("/api/incidents/does-not-exist")
        assert missing.status_code == 404


class TestIncidentLifecycle:
    """Transitions are strictly forward; the API enforces them."""

    def test_acknowledge_moves_open_to_acknowledged(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        resp = client.post(f"/api/incidents/{incident_id}/acknowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACKNOWLEDGED"
        assert data["acknowledged_at"] is not None
        assert data["resolved_at"] is None

    def test_resolve_requires_prior_acknowledgement(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        too_early = client.post(f"/api/incidents/{incident_id}/resolve")
        assert too_early.status_code == 409
        assert "acknowledge" in too_early.json()["detail"].lower()

        client.post(f"/api/incidents/{incident_id}/acknowledge")
        ok = client.post(f"/api/incidents/{incident_id}/resolve")
        assert ok.status_code == 200
        assert ok.json()["status"] == "RESOLVED"
        assert ok.json()["resolved_at"] is not None

    def test_archive_requires_resolution(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]

        too_early = client.post(f"/api/incidents/{incident_id}/archive")
        assert too_early.status_code == 409

        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")
        ok = client.post(f"/api/incidents/{incident_id}/archive")
        assert ok.status_code == 200
        data = ok.json()
        assert data["status"] == "ARCHIVED"
        assert data["archived_at"] is not None

        # Archived is terminal: no further lifecycle action is valid.
        again = client.post(f"/api/incidents/{incident_id}/acknowledge")
        assert again.status_code == 409

    def test_recurrence_resets_resolved_to_open(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        incident_id = client.get("/api/incidents").json()[0]["id"]
        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")

        # The attention condition is still present, so the next sync must
        # resurrect the incident rather than silently leaving it resolved.
        result = client.post("/api/incidents/sync").json()
        assert result["reset"] == 1

        data = client.get(f"/api/incidents/{incident_id}").json()
        assert data["status"] == "OPEN"
        assert data["recurrence_count"] == 1
        assert data["resolved_at"] is None
        assert data["last_recurred_at"] is not None

    def test_cleanup_archives_only_stale_resolved(self, client_and_engine):
        client, _, _ = client_and_engine
        store = get_incident_store()

        incident = store.upsert_from_attention({
            "attention_level": "possible", "attention_class": "TEST_CLASS",
            "reason": "test", "evidence": [], "evidence_boundary": "OBSERVED",
            "item": {"id": "media:x", "title": "X", "media_type": "movie"},
        })[0]
        store.acknowledge(incident["id"])
        store.resolve(incident["id"])

        row0 = store.get(incident["id"])
        assert row0 is not None
        assert row0["status"] == "RESOLVED"

        # A freshly resolved incident is NOT stale.
        assert store.cleanup() == 0

        # Backdate the resolution past the 90-day window.
        stale_ts = (NOW - timedelta(days=100)).isoformat()
        row = store.get(incident["id"])
        assert row is not None
        with store._lock:
            store._incidents[row["id"]]["resolved_at"] = stale_ts
            store._save()

        assert store.cleanup() == 1
        archived = store.get(incident["id"])
        assert archived is not None and archived["status"] == "ARCHIVED"

        # Open work is untouched by cleanup.
        open_incident = store.upsert_from_attention({
            "attention_level": "possible", "attention_class": "OTHER_CLASS",
            "reason": "test", "evidence": [], "evidence_boundary": "OBSERVED",
            "item": {"id": "media:y", "title": "Y", "media_type": "movie"},
        })[0]
        assert store.cleanup() == 0
        open_row = store.get(open_incident["id"])
        assert open_row is not None and open_row["status"] == "OPEN"

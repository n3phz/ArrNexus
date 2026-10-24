"""Mandatory incident-semantics regression checks (Phase 4D review).

Each test maps to one mandated semantic from the review document. These are
deliberately explicit end-to-end guarantees:

  1  repeated sync never duplicates an incident
  2  a historical/stale event is not a current incident
  3  SYNTHETIC evidence never creates an incident
  4  BLOCKED is distinct from FAILED/REQUIRED
  5  ACKNOWLEDGED does not silently revert to OPEN
  6  RESOLVED/ARCHIVED reopen only when the condition is genuinely re-observed
  7  the recurrence counter increments once per recurrence episode
  8  invalid transitions fail without mutating stored state
  9  cleanup cannot touch active incidents and never deletes anything
  10 incidents survive restarts; a corrupt or interrupted write is
     quarantined, never silently overwritten

Same hermetic harness as the Phase 4C/4D suites: no lifespan, no network.
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
from backend.services import events as events_module
from backend.services.incidents import IncidentStore, get_incident_store, reset_incident_store

NOW = datetime.utcnow()


def ev(etype, ts, svc=SourceService.RADARR, media_id="1", boundary=EvidenceBoundary.OBSERVED, meta=None,
       status=EventStatus.COMPLETED, error=None):
    return RawEvent(
        timestamp=ts, source_service=svc, event_type=etype, media_type=MediaType.MOVIE,
        media_identifier=media_id, title="Test Movie", correlation_key=f"media:{media_id}",
        status=status, error_message=error, evidence_boundary=boundary, normalized_metadata=meta,
    )


def hours_ago(h, m=0):
    return NOW - timedelta(hours=h, minutes=m)


@pytest.fixture
def client_and_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    correlation = CorrelationEngine()
    correlation.add_events([
        # Overdue grab: produces attention now.
        ev(EventType.RELEASE_GRABBED, hours_ago(2), svc=SourceService.SONARR),
        ev(EventType.RELEASE_GRABBED, hours_ago(0, 2), svc=SourceService.SONARR),
        # Settled 30 days ago: historically stale, never a current incident.
        ev(EventType.AVAILABLE, hours_ago(24 * 30), svc=SourceService.SONARR, media_id="2"),
        # Synthetic overdue grab: must stay out of the incident log.
        ev(EventType.RELEASE_GRABBED, hours_ago(5), svc=SourceService.SONARR,
           media_id="synth", boundary=EvidenceBoundary.SYNTHETIC),
        # Blocked evidence: distinct from failures.
        ev(EventType.WANTED, hours_ago(2), svc=SourceService.SONARR, media_id="8",
           boundary=EvidenceBoundary.BLOCKED),
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


def make_attention(item_id, cls, level="possible", boundary="observed", severity="medium", reason="r"):
    return {
        "attention_level": level,
        "attention_class": cls,
        "reason": reason,
        "evidence": ["evidence line"],
        "evidence_boundary": boundary,
        "confidence_basis": None,
        "requires_attention": True,
        "severity": severity,
        "state": "release_grabbed",
        "missing_transition": "download_started",
        "stage_freshness": "STALE",
        "evidence_freshness": "CURRENT",
        "stage_duration_seconds": 7200,
        "evidence_age_seconds": 120,
        "item": {"id": item_id, "title": "T", "media_type": "movie",
                  "current_state": "release_grabbed", "last_event_at": NOW.isoformat()},
    }


class TestCheck1_NoDuplicatesOnRepeatedSync:

    def test_three_syncs_produce_one_incident(self, client_and_engine):
        client, _, _ = client_and_engine
        first = client.post("/api/incidents/sync").json()
        assert first["created"] == 2  # grab + blocked items; settled/synthetic excluded
        for _ in range(3):
            again = client.post("/api/incidents/sync").json()
            assert again["created"] == 0
        rows = client.get("/api/incidents").json()
        assert len(rows) == 2
        assert len({r["id"] for r in rows}) == 2  # deterministic ids, no drift


class TestCheck2_StaleHistoryIsNotACurrentIncident:

    def test_settled_ancient_item_never_becomes_an_incident(self, client_and_engine):
        client, _, _ = client_and_engine
        for _ in range(3):
            client.post("/api/incidents/sync")
        rows = client.get("/api/incidents").json()
        assert all(r["correlation_key"] != "media:2" for r in rows)
        stats = client.get("/api/incidents/stats").json()
        assert stats["total"] == 2  # only the two *current* conditions


class TestCheck3_SyntheticNeverIncidents:

    def test_synthetic_item_absent_from_incidents(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        rows = client.get("/api/incidents").json()
        assert all(r["correlation_key"] != "media:synth" for r in rows)
        # Store level: the upsert itself refuses the level.
        store = get_incident_store()
        _, outcome = store.upsert_from_attention(
            make_attention("media:synth", "SYNTHETIC_DATA", level="synthetic", boundary="synthetic")
        )
        assert outcome == "skipped"


class TestCheck4_BlockedDistinctFromFailures:

    def test_blocked_incident_carries_blocked_semantics(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        rows = {r["correlation_key"]: r for r in client.get("/api/incidents").json()}
        blocked = rows["media:8"]
        assert blocked["attention_level"] == "blocked"
        assert blocked["attention_class"] == "EVIDENCE_BLOCKED"
        assert blocked["severity"] == "medium"  # a diagnosis block, not a high failure
        # And it is its own incident, not merged with a failure incident.
        assert blocked["id"] != rows["media:1"]["id"]


class TestCheck5_AcknowledgedDoesNotRevert:

    def test_acknowledged_survives_repeated_syncs(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        open_rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        assert open_rows, "an OPEN incident is expected from the seeded grab"
        incident_id = open_rows[0]["id"]
        client.post(f"/api/incidents/{incident_id}/acknowledge")

        for _ in range(3):
            client.post("/api/incidents/sync")

        row = client.get(f"/api/incidents/{incident_id}").json()
        assert row is not None
        assert row["status"] == "ACKNOWLEDGED"
        assert row["recurrence_count"] == 0
        assert row["acknowledged_at"] is not None


class TestCheck6_ReopenOnlyOnGenuineReobservation:

    def test_resolved_incident_stays_closed_when_condition_gone(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        open_rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        assert open_rows, "an OPEN incident is expected from the seeded grab"
        rows_by_key = {r["correlation_key"]: r for r in open_rows}
        incident_id = rows_by_key["media:1"]["id"]
        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")

        store = get_incident_store()
        # A *different* condition being observed must not touch it…
        store.upsert_from_attention(make_attention("media:other", "OTHER_CLASS"))
        row = store.get(incident_id)
        assert row["status"] == "RESOLVED"
        assert row["recurrence_count"] == 0

        # …and the same condition genuinely observed again reopens it.
        _, outcome = store.upsert_from_attention(
            make_attention(
                "media:1", "GRAB_NO_DOWNLOAD",
            )
        )
        assert outcome == "reset"
        row = store.get(incident_id)
        assert row["status"] == "OPEN"


class TestCheck7_RecurrenceCountedOncePerEpisode:

    def test_count_stable_while_open_across_syncs(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        open_rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        incident_id = {r["correlation_key"]: r for r in open_rows}["media:1"]["id"]
        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")

        # Episode 1: condition returns -> exactly one increment.
        assert client.post("/api/incidents/sync").json()["reset"] == 1
        assert client.get(f"/api/incidents/{incident_id}").json()["recurrence_count"] == 1

        # While OPEN, every further sync must NOT increment again.
        for _ in range(4):
            client.post("/api/incidents/sync")
        assert client.get(f"/api/incidents/{incident_id}").json()["recurrence_count"] == 1

        # Episode 2: close and reopen again -> exactly one more.
        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")
        assert client.post("/api/incidents/sync").json()["reset"] == 1
        assert client.get(f"/api/incidents/{incident_id}").json()["recurrence_count"] == 2


class TestCheck8_InvalidTransitionsDoNotMutate:

    def test_409_leaves_stored_state_byte_identical(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        open_rows = client.get("/api/incidents", params={"status": "OPEN"}).json()
        incident_id = {r["correlation_key"]: r for r in open_rows}["media:1"]["id"]
        before = client.get(f"/api/incidents/{incident_id}").json()

        bad = client.post(f"/api/incidents/{incident_id}/resolve")  # skip acknowledge
        assert bad.status_code == 409
        assert "acknowledge" in bad.json()["detail"].lower()

        after = client.get(f"/api/incidents/{incident_id}").json()
        assert after == before  # no mutation of any stored field

        client.post(f"/api/incidents/{incident_id}/acknowledge")
        client.post(f"/api/incidents/{incident_id}/resolve")
        client.post(f"/api/incidents/{incident_id}/archive")
        archived = client.get(f"/api/incidents/{incident_id}").json()
        frozen = client.get(f"/api/incidents/{incident_id}").json()
        for action in ("acknowledge", "resolve", "archive"):
            resp = client.post(f"/api/incidents/{incident_id}/{action}")
            assert resp.status_code == 409
        assert client.get(f"/api/incidents/{incident_id}").json() == frozen
        assert archived["status"] == "ARCHIVED"


class TestCheck9_CleanupProtectsActiveWork:

    def test_cleanup_skips_active_and_deletes_nothing(self, client_and_engine):
        client, _, _ = client_and_engine
        client.post("/api/incidents/sync")
        store = get_incident_store()
        open_id = client.get("/api/incidents", params={"status": "OPEN"}).json()[0]["id"]
        store.acknowledge(open_id)
        second = [r for r in store.list() if r["id"] != open_id][0]
        store.acknowledge(second["id"])
        store.resolve(second["id"])
        store.archive(second["id"])

        # Backdate the ARCHIVED row's resolution far past the window.
        with store._lock:
            row = store._incidents[second["id"]]
            row["resolved_at"] = (NOW - timedelta(days=200)).isoformat()
            row["status"] = "RESOLVED"
            row["archived_at"] = None
            store._save()

        result = client.post("/api/incidents/cleanup").json()
        assert result["archived"] == 1

        statuses = {r["id"]: r["status"] for r in client.get("/api/incidents").json()}
        assert statuses[second["id"]] == "ARCHIVED"
        assert statuses[open_id] == "ACKNOWLEDGED"  # active work untouched
        assert len(statuses) == 2  # nothing was deleted


class TestCheck10_RestartAndCorruptionRecovery:

    def test_store_survives_process_restart(self, client_and_engine):
        client, _, tmp_path = client_and_engine
        client.post("/api/incidents/sync")
        client.post(f"/api/incidents/{client.get('/api/incidents').json()[0]['id']}/acknowledge")

        # A brand-new process (fresh store instance) sees the same rows.
        fresh = IncidentStore(tmp_path)
        assert fresh.load_failed is False
        rows = fresh.list()
        assert len(rows) == 2
        assert [r for r in rows if r["status"] == "ACKNOWLEDGED"][0]["acknowledged_at"] is not None

    def test_corrupt_store_is_quarantined_not_overwritten(self, client_and_engine):
        client, _, tmp_path = client_and_engine
        client.post("/api/incidents/sync")
        client.post(f"/api/incidents/{client.get('/api/incidents').json()[0]['id']}/acknowledge")

        # Simulate an interrupted/torn write: truncate the store to garbage.
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write('{"incidents": {"x": ')  # truncated JSON

        restarted = IncidentStore(tmp_path)
        assert restarted.load_failed is True
        assert restarted.list() == []  # fail-closed: no phantom data

        # First write must NOT clobber the corrupt file: it is quarantined
        # so an operator can recover from it.
        restarted.touch_sync()
        assert restarted.load_failed is False
        with open(tmp_path, encoding="utf-8") as fh:
            json.loads(fh.read())  # the new file is valid
        # The corrupt content still exists on disk (moved aside, not erased).
        prefix = os.path.basename(tmp_path) + ".corrupt-"
        all_files = os.listdir(os.path.dirname(tmp_path))
        assert any(name.startswith(prefix) for name in all_files), all_files

    def test_empty_file_is_a_valid_empty_store(self, client_and_engine):
        client, _, tmp_path = client_and_engine
        open(tmp_path, "w").close()
        store = IncidentStore(tmp_path)
        assert store.load_failed is False
        assert store.list() == []
        store.touch_sync()
        assert store.stats()["total"] == 0

"""Incident lifecycle management (Phase 4D).

Attention items persist as *tracked incidents* with a full lifecycle instead of
being re-derived (and forgotten) on every classification pass. The incident
lifecycle is:

    OPEN -> ACKNOWLEDGED -> RESOLVED -> ARCHIVED

Semantics that must never be broken:

* An incident is created from an *attention condition* (the orchestrator's
  classification of a media item), never from raw events alone.
* SYNTHETIC evidence never produces an incident: the orchestrator already
  filters synthetic data, and the store refuses to create incidents for it.
* A resolved or archived incident that is observed requiring attention again
  *recurs*: it resets to OPEN so the operator must acknowledge it again.
  Recurrence is recorded, not silently swallowed.
* Transitions are strictly forward: OPEN -> ACKNOWLEDGED -> RESOLVED ->
  ARCHIVED. Skipping a state (e.g. resolving an OPEN incident directly) is
  rejected, so an operator can never paper over an unacknowledged problem.
* Cleanup only touches RESOLVED incidents that have been resolved for longer
  than the retention window; it never mutates OPEN/ACKNOWLEDGED work.

Persistence is a single JSON file with atomic writes (temp file + rename),
which keeps the store trivially backable up, inspectable, and safe against
torn writes. The store is a plain in-process object guarded by a lock, so it
is safe under the app's multi-threaded polling + API usage.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("arrnexus.services.incidents")

# ---------------------------------------------------------------------------
# Lifecycle states
# ---------------------------------------------------------------------------

OPEN = "OPEN"
ACKNOWLEDGED = "ACKNOWLEDGED"
RESOLVED = "RESOLVED"
ARCHIVED = "ARCHIVED"

ALL_STATUSES = (OPEN, ACKNOWLEDGED, RESOLVED, ARCHIVED)

#: The only valid forward transitions. Anything else is a 409 upstream.
ALLOWED_TRANSITIONS: Dict[str, str] = {
    OPEN: ACKNOWLEDGED,
    ACKNOWLEDGED: RESOLVED,
    RESOLVED: ARCHIVED,
}

#: Action label for each allowed transition (used in error messages).
ACTION_FOR_TRANSITION: Dict[str, str] = {
    OPEN: "acknowledge",
    ACKNOWLEDGED: "resolve",
    RESOLVED: "archive",
}

#: Retention window for RESOLVED incidents before cleanup archives them.
STALE_RESOLVED_AFTER = timedelta(days=90)


def _utcnow() -> datetime:
    """Naive UTC, matching the rest of the codebase's timestamp handling."""
    return datetime.utcnow()


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.isoformat() if ts else None


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class IncidentTransitionError(ValueError):
    """Raised when a lifecycle transition is not allowed for the current state."""

    def __init__(self, incident_id: str, current: str, requested: str):
        self.incident_id = incident_id
        self.current = current
        self.requested = requested
        action = ACTION_FOR_TRANSITION.get(current, "no further action")
        allowed = "no further action is available" if current == ARCHIVED else \
            f"the only allowed action from {current} is '{action}'"
        super().__init__(
            f"Cannot move incident {incident_id} to {requested}: "
            f"state is {current}, and {allowed}."
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IncidentStore:
    """File-backed incident store with a strict lifecycle."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._incidents: Dict[str, Dict[str, Any]] = {}
        self._last_sync_at: Optional[str] = None
        # Fail-closed flag: if the on-disk store could not be parsed, we must
        # not silently overwrite it with an empty in-memory store. The corrupt
        # file is quarantined on the first save so the evidence survives for
        # manual recovery, and operators see a CRITICAL log, not a shrug.
        self._load_failed = False
        self._load_error: Optional[str] = None
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            if not content:
                return  # empty file = empty store (mid-write or brand new)
            data = json.loads(content)
            self._incidents = {k: v for k, v in data.get("incidents", {}).items()}
            self._last_sync_at = data.get("last_sync_at")
        except (json.JSONDecodeError, OSError) as exc:
            # Keep the in-memory store empty and refuse to clobber the file
            # until it is quarantined (see _save). The app stays up: an empty
            # incident view is fail-closed, not a data-loss event.
            self._load_failed = True
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.error(f"Incident store unreadable at {self.path}: {exc}")

    def _quarantine_corrupt_file(self) -> None:
        """Move an unreadable store file aside (with a timestamp) and clear
        the fail-closed flag. The quarantined copy is the recovery source;
        it is never deleted by the application."""
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        corrupt_path = f"{self.path}.corrupt-{stamp}"
        try:
            os.replace(self.path, corrupt_path)
            logger.critical(
                f"Incident store at {self.path} was unreadable ({self._load_error}); "
                f"quarantined to {corrupt_path}. Store restarted empty; restore "
                f"incidents from the quarantined file or backup before relying on it."
            )
        except OSError as exc:
            logger.critical(f"Could not quarantine corrupt incident store: {exc}")
        self._load_failed = False
        self._load_error = None

    def _save(self) -> None:
        """Atomic write: dump to a temp file in the same directory, then rename."""
        if self._load_failed and os.path.exists(self.path):
            self._quarantine_corrupt_file()
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent or ".", prefix=".incidents-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    {"incidents": self._incidents, "last_sync_at": self._last_sync_at},
                    fh,
                    indent=1,
                    sort_keys=True,
                )
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @property
    def load_failed(self) -> bool:
        """True while the store file could not be parsed (fail-closed mode)."""
        return self._load_failed

    # -- creation / upsert --------------------------------------------------

    @staticmethod
    def make_incident_id(correlation_key: str, attention_class: str) -> str:
        """Deterministic incident identity.

        Same media item + same problem class = same incident, so re-evaluating
        attention never spawns duplicates.
        """
        import hashlib

        return hashlib.sha256(f"{correlation_key}:{attention_class}".encode("utf-8")).hexdigest()[:16]

    def upsert_from_attention(self, attention: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Upsert one orchestrator attention payload into the incident store.

        Returns the incident dict and one of: 'created' | 'updated' | 'reset' |
        'skipped'. SYNTHETIC and NONE attention never create incidents.
        """
        level = attention.get("attention_level")
        if level in ("none", "synthetic"):
            return {}, "skipped"

        item = attention.get("item") or {}
        correlation_key = item.get("id") or attention.get("correlation_key")
        attention_class = attention.get("attention_class") or "UNCLASSIFIED"
        if not correlation_key:
            return {}, "skipped"

        incident_id = self.make_incident_id(correlation_key, attention_class)

        with self._lock:
            snapshot = self._snapshot(attention, item, correlation_key, attention_class)
            existing = self._incidents.get(incident_id)

            if existing is None:
                incident = dict(snapshot)
                incident["id"] = incident_id
                incident["status"] = OPEN
                incident["first_observed_at"] = _iso(_utcnow())
                incident["last_observed_at"] = incident["first_observed_at"]
                incident["acknowledged_at"] = None
                incident["resolved_at"] = None
                incident["archived_at"] = None
                incident["recurrence_count"] = 0
                self._incidents[incident_id] = incident
                self._save()
                logger.info(f"Created incident {incident_id} ({attention_class} for {correlation_key})")
                return incident, "created"

            # Already tracked. Refresh the evidence snapshot.
            prev_status = existing["status"]
            for key, value in snapshot.items():
                existing[key] = value
            existing["last_observed_at"] = _iso(_utcnow())

            if prev_status in (RESOLVED, ARCHIVED):
                # The problem came back after resolution: it must be
                # acknowledged again. Reset to OPEN and record the recurrence.
                existing["status"] = OPEN
                existing["acknowledged_at"] = None
                existing["resolved_at"] = None
                existing["archived_at"] = None
                existing["recurrence_count"] = int(existing.get("recurrence_count", 0)) + 1
                existing["last_recurred_at"] = _iso(_utcnow())
                self._save()
                logger.info(f"Incident {incident_id} recurred; reset to {OPEN}")
                return existing, "reset"

            self._save()
            return existing, "updated"

    @staticmethod
    def _snapshot(attention: Dict[str, Any], item: Dict[str, Any], correlation_key: str, attention_class: str) -> Dict[str, Any]:
        """The evidence fields an incident carries from its attention condition."""
        return {
            "correlation_key": correlation_key,
            "attention_class": attention_class,
            "attention_level": attention.get("attention_level"),
            "severity": attention.get("severity"),
            "reason": attention.get("reason"),
            "evidence": list(attention.get("evidence") or []),
            "evidence_boundary": attention.get("evidence_boundary"),
            "confidence_basis": attention.get("confidence_basis"),
            "state": attention.get("state") or item.get("current_state"),
            "missing_transition": attention.get("missing_transition"),
            "stage_freshness": attention.get("stage_freshness"),
            "evidence_freshness": attention.get("evidence_freshness"),
            "stage_duration_seconds": attention.get("stage_duration_seconds"),
            "evidence_age_seconds": attention.get("evidence_age_seconds"),
            "title": item.get("title"),
            "media_type": item.get("media_type"),
            "last_evidence_at": item.get("last_event_at"),
        }

    def touch_sync(self) -> None:
        with self._lock:
            self._last_sync_at = _iso(_utcnow())
            self._save()

    # -- queries ------------------------------------------------------------

    def list(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """All incidents, optionally filtered by status.

        Ordered with the work still to do first: OPEN, ACKNOWLEDGED, RESOLVED,
        ARCHIVED; within a status, oldest first-observed first (longest-waiting
        work rises to the top of its group).
        """
        with self._lock:
            rows = [dict(v) for v in self._incidents.values()]
        if status:
            wanted = status.upper()
            rows = [r for r in rows if r.get("status") == wanted]
        order = {OPEN: 0, ACKNOWLEDGED: 1, RESOLVED: 2, ARCHIVED: 3}
        rows.sort(key=lambda r: (order.get(r.get("status"), 9), r.get("first_observed_at") or ""))
        return rows

    def get(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._incidents.get(incident_id)
            return dict(row) if row else None

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            counts = {s: 0 for s in ALL_STATUSES}
            for incident in self._incidents.values():
                s = incident.get("status", OPEN)
                if s in counts:
                    counts[s] += 1
            return {
                "total": len(self._incidents),
                "open": counts[OPEN],
                "acknowledged": counts[ACKNOWLEDGED],
                "resolved": counts[RESOLVED],
                "archived": counts[ARCHIVED],
                "active": counts[OPEN] + counts[ACKNOWLEDGED],
                "last_sync_at": self._last_sync_at,
            }

    # -- lifecycle transitions ---------------------------------------------

    def _transition(self, incident_id: str, target: str) -> Dict[str, Any]:
        with self._lock:
            incident = self._incidents.get(incident_id)
            if incident is None:
                raise KeyError(incident_id)
            current = str(incident.get("status") or OPEN)
            if ALLOWED_TRANSITIONS.get(current) != target:
                raise IncidentTransitionError(incident_id, current, target)
            incident["status"] = target
            if target == ACKNOWLEDGED:
                incident["acknowledged_at"] = _iso(_utcnow())
            elif target == RESOLVED:
                incident["resolved_at"] = _iso(_utcnow())
            elif target == ARCHIVED:
                incident["archived_at"] = _iso(_utcnow())
            self._save()
            logger.info(f"Incident {incident_id}: {current} -> {target}")
            return dict(incident)

    def acknowledge(self, incident_id: str) -> Dict[str, Any]:
        return self._transition(incident_id, ACKNOWLEDGED)

    def resolve(self, incident_id: str) -> Dict[str, Any]:
        return self._transition(incident_id, RESOLVED)

    def archive(self, incident_id: str) -> Dict[str, Any]:
        return self._transition(incident_id, ARCHIVED)

    # -- maintenance ---------------------------------------------------------

    def cleanup(self, stale_after: timedelta = STALE_RESOLVED_AFTER) -> int:
        """Archive RESOLVED incidents that have been resolved longer than
        `stale_after`. Returns the number of incidents archived.

        This is the only housekeeping mutation; it never touches OPEN or
        ACKNOWLEDGED incidents, and it never deletes anything (archived rows
        stay queryable as history).
        """
        cutoff = _utcnow() - stale_after
        archived = 0
        with self._lock:
            for incident in self._incidents.values():
                if incident.get("status") != RESOLVED:
                    continue
                resolved_at = _parse_ts(incident.get("resolved_at"))
                if resolved_at is not None and resolved_at < cutoff:
                    incident["status"] = ARCHIVED
                    incident["archived_at"] = _iso(_utcnow())
                    archived += 1
            if archived:
                self._save()
                logger.info(f"Incident cleanup archived {archived} stale resolved incident(s)")
        return archived


# ---------------------------------------------------------------------------
# Module-level store (path resolution: env override > production volume > CWD)
# ---------------------------------------------------------------------------

_store: Optional[IncidentStore] = None
_store_lock = threading.Lock()


def _default_store_path() -> str:
    override = os.environ.get("INCIDENT_STORE_PATH")
    if override:
        return override
    # Production deployments keep persistent state on a mounted volume.
    # The live Saltbox compose runs the database at sqlite:////config/arrnexus.db,
    # so /config is the persistent directory in the current image layout;
    # /data is the compose file shipped in this repository. Prefer whichever
    # exists, so the incident store survives container recreation.
    for candidate in ("/config/incidents.json", "/data/incidents.json"):
        if os.path.isdir(os.path.dirname(candidate)):
            return candidate
    # Development default: beside the default development database
    # (/tmp/arrnexus/arrnexus.db, see core/config.py) so a plain
    # `uvicorn` run in a checkout never drops state files into the repo.
    return "/tmp/arrnexus/incidents.json"


def get_incident_store() -> IncidentStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = IncidentStore(_default_store_path())
        return _store


def reset_incident_store() -> None:
    """Test hook: drop the module-level store so the next access re-resolves."""
    global _store
    with _store_lock:
        _store = None


def sync_incidents(db: Any) -> Dict[str, Any]:
    """Evaluate current attention and upsert it into the incident store.

        Attention (a condition now) -> Incident (a tracked problem).
        The store gains one row per (correlation_key, attention_class);
        re-observation refreshes the snapshot; post-resolution recurrence
        resets the incident to OPEN.

    Returns counters: created / updated / reset / total / last_sync_at.
    """
    from backend.services.events import EventService
    from backend.services.orchestrator import get_orchestrator

    store = get_incident_store()
    event_service = EventService(db)
    items = event_service.get_all_items()

    orchestrator = get_orchestrator()
    # include_blocked: a blocked diagnosis is still a tracked operational
    # problem. exclude_synthetic keeps test data out of the incident log.
    attention_items = orchestrator.get_attention_items(
        items, include_blocked=True, exclude_synthetic=True
    )

    counters = {"created": 0, "updated": 0, "reset": 0, "skipped": 0}
    for attention in attention_items:
        _, outcome = store.upsert_from_attention(attention)
        counters[outcome] = counters.get(outcome, 0) + 1

    store.touch_sync()
    counters["total"] = len(attention_items)
    counters["last_sync_at"] = store.stats()["last_sync_at"]
    return counters

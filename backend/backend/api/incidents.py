"""Incident lifecycle API endpoints (Phase 4D).

Incidents are *tracked operational problems*: an attention condition that has
been promoted to a lifecycle (OPEN -> ACKNOWLEDGED -> RESOLVED -> ARCHIVED).

    GET  /api/incidents               list incidents (filter by status)
    GET  /api/incidents/stats         lifecycle counts
    GET  /api/incidents/{id}          single incident
    POST /api/incidents/{id}/acknowledge   OPEN -> ACKNOWLEDGED
    POST /api/incidents/{id}/resolve      ACKNOWLEDGED -> RESOLVED
    POST /api/incidents/{id}/archive      RESOLVED -> ARCHIVED
    POST /api/incidents/sync            evaluate attention, upsert incidents
    POST /api/incidents/cleanup         archive stale resolved incidents

Attention remains the *current-condition* surface (see /api/attention);
incidents are the *tracked-problem* surface. A condition and a tracked problem
are deliberately different concepts even when they describe the same item.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.base import get_db
from backend.services.incidents import (
    ALL_STATUSES,
    IncidentStore,
    get_incident_store,
    sync_incidents,
)

logger = logging.getLogger("arrnexus.api.incidents")

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return None
    value = status.upper()
    if value not in ALL_STATUSES:
        valid = ", ".join(s.lower() for s in ALL_STATUSES)
        raise HTTPException(status_code=400, detail=f"Unknown status '{status}'. Valid values: {valid}")
    return value


@router.get("", response_model=List[dict], summary="List incidents")
def list_incidents(
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None, description="Filter by lifecycle state: open, acknowledged, resolved, archived"),
):
    """List tracked incidents, optionally filtered by lifecycle state.

    The list is the *tracked* view of operational problems; it intentionally
    includes acknowledged and resolved work so an operator can see what is in
    flight, not only what is unhandled.
    """
    incident_status = _normalize_status(status)
    store = get_incident_store()
    return store.list(status=incident_status)


@router.get("/stats", response_model=dict, summary="Incident lifecycle statistics")
def incident_stats(db: Session = Depends(get_db)):
    """Lifecycle counts. `active` is the work that still needs an operator."""
    store = get_incident_store()
    return store.stats()


@router.get("/{incident_id}", response_model=dict, summary="Get one incident")
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    """Fetch a single incident by its deterministic id."""
    store = get_incident_store()
    incident = store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return incident


def _transition(incident_id: str, action: str, target: str, store: IncidentStore) -> dict:
    from backend.services.incidents import IncidentTransitionError

    try:
        return store._transition(incident_id, target)  # noqa: SLF001 — single validation path
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    except IncidentTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{incident_id}/acknowledge", response_model=dict, summary="Acknowledge an OPEN incident")
def acknowledge_incident(incident_id: str, db: Session = Depends(get_db)):
    """Mark OPEN work as seen by an operator. The first step of the lifecycle."""
    from backend.services.incidents import ACKNOWLEDGED

    return _transition(incident_id, "acknowledge", ACKNOWLEDGED, get_incident_store())


@router.post("/{incident_id}/resolve", response_model=dict, summary="Resolve an ACKNOWLEDGED incident")
def resolve_incident(incident_id: str, db: Session = Depends(get_db)):
    """Close an acknowledged problem. Resolution is only valid after the
    operator has explicitly acknowledged the incident."""
    from backend.services.incidents import RESOLVED

    return _transition(incident_id, "resolve", RESOLVED, get_incident_store())


@router.post("/{incident_id}/archive", response_model=dict, summary="Archive a RESOLVED incident")
def archive_incident(incident_id: str, db: Session = Depends(get_db)):
    """Move a resolved incident into history. Archived incidents stay queryable."""
    from backend.services.incidents import ARCHIVED

    return _transition(incident_id, "archive", ARCHIVED, get_incident_store())


@router.post("/sync", response_model=dict, summary="Sync incidents from current attention")
def sync_from_attention(db: Session = Depends(get_db)):
    """Run one attention evaluation and upsert the results into the store.

    The polling service calls this after each cycle so incidents track live
    conditions; operators may also trigger it on demand. Returns counters for
    what changed: created / updated / reset (recurrence) / total.
    """
    return sync_incidents(db)


@router.post("/cleanup", response_model=dict, summary="Archive stale resolved incidents")
def cleanup_incidents(db: Session = Depends(get_db)):
    """Archive RESOLVED incidents older than the retention window (90 days).

    Maintenance only: never mutates OPEN/ACKNOWLEDGED work and never deletes
    history.
    """
    from backend.services.incidents import STALE_RESOLVED_AFTER

    store = get_incident_store()
    archived = store.cleanup()
    return {"archived": archived, "retention_days": int(STALE_RESOLVED_AFTER.days)}

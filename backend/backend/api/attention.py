"""Attention/Operational Intelligence API endpoints."""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database.base import get_db
from backend.services.events import EventService
from backend.services.orchestrator import get_orchestrator, AttentionLevel

logger = logging.getLogger("arrnexus.api.attention")

router = APIRouter(prefix="/api/attention", tags=["attention"])


@router.get("", response_model=List[dict], summary="Get items requiring attention")
def get_attention_items(
    db: Session = Depends(get_db),
    include_blocked: bool = Query(False, description="Include BLOCKED items in results"),
    exclude_synthetic: bool = Query(True, description="Exclude SYNTHETIC/test data"),
    limit: int = Query(50, ge=1, le=200, description="Maximum items to return"),
):
    """Get media items requiring operational attention, sorted by priority.

    Returns items classified as:
    - REQUIRED: Known failures, stuck downloads, or confirmed issues
    - POSSIBLE: Suspicious activity (stalled downloads, long waits)
    - BLOCKED: Items where evidence is unavailable
    """
    event_service = EventService(db)
    items = event_service.get_all_items()

    orchestrator = get_orchestrator()
    attention_items = orchestrator.get_attention_items(
        items,
        include_blocked=include_blocked,
        exclude_synthetic=exclude_synthetic,
    )

    # Apply limit
    return attention_items[:limit]


@router.get("/stats", response_model=dict, summary="Get attention statistics")
def get_attention_stats(
    db: Session = Depends(get_db),
    exclude_synthetic: bool = Query(True, description="Exclude SYNTHETIC/test data"),
):
    """Get summary statistics for attention items."""
    event_service = EventService(db)
    items = event_service.get_all_items()

    orchestrator = get_orchestrator()
    all_attention = orchestrator.get_attention_items(
        items,
        include_blocked=True,
        exclude_synthetic=exclude_synthetic,
    )

    # Count by level
    required = len([a for a in all_attention if a["attention_level"] == AttentionLevel.REQUIRED])
    possible = len([a for a in all_attention if a["attention_level"] == AttentionLevel.POSSIBLE])
    blocked = len([a for a in all_attention if a["attention_level"] == AttentionLevel.BLOCKED])

    # Count by severity
    high = len([a for a in all_attention if a.get("severity") == "high"])
    medium = len([a for a in all_attention if a.get("severity") == "medium"])

    return {
        "total_attention": len(all_attention),
        "required": required,
        "possible": possible,
        "blocked": blocked,
        "high_severity": high,
        "medium_severity": medium,
        "items_count": len(items),
    }

"""Services API endpoints."""
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database.base import get_db
from backend.services.events import EventService
from backend.adapters import get_all_adapters
from backend.adapters.base import ServiceAdapter

logger = logging.getLogger("arrnexus.api.services")

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("", response_model=List[dict], summary="List service statuses")
def list_services():
    """Get health status of all configured services."""
    adapters = get_all_adapters()
    
    results = []
    for adapter in adapters:
        try:
            health = adapter.health()
            results.append({
                "service": health.service.value,
                "status": health.status,
                "version": health.version,
                "error": health.error_message,
            })
        except Exception as e:
            results.append({
                "service": adapter.service_name.value,
                "status": "error",
                "version": None,
                "error": str(e),
            })
    
    return results


@router.get("/{service}", response_model=dict, summary="Get service status")
def get_service_status(service: str):
    """Get detailed status of a specific service.

    Unknown service names are a client error (404); a health check that
    itself fails is a server-side unavailability signal (503) with the
    reason in the detail, so clients can distinguish the two.
    """
    adapters = get_all_adapters()

    for adapter in adapters:
        if adapter.service_name.value == service:
            try:
                health = adapter.health()
                return {
                    "service": health.service.value,
                    "status": health.status,
                    "version": health.version,
                    "error": health.error_message,
                    "details": health.details
                }
            except Exception as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Health check for {service} failed: {e}",
                )

    raise HTTPException(status_code=404, detail=f"Service not found: {service}")


@router.post("/poll", response_model=Dict[str, int], summary="Force poll all services")
def force_poll(db: Session = Depends(get_db)):
    """Force immediate polling of all services."""
    from backend.services.polling import PollingService
    service = PollingService()
    return service.force_poll()
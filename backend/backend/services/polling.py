"""Polling service for fetching events from ARR services."""
import logging
import json
import time
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from queue import Queue
from threading import Thread, Lock

from backend.adapters.base import ServiceAdapter, SourceService, RawEvent
from backend.adapters import get_all_adapters
from backend.services.events import EventService
from backend.database.base import SessionLocal
from backend.api.sse import sse_tracker

logger = logging.getLogger("arr-control.services.polling")


class PollingService:
    """Service for polling ARR services and ingesting events."""
    
    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self.adapters = get_all_adapters()
        self.running = False
        self.poll_thread: Optional[Thread] = None
        self.last_poll_times: Dict[SourceService, datetime] = {}
        self._lock = Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def start(self) -> None:
        """Start the polling loop."""
        if self.running:
            return
        
        # Capture the event loop for cross-thread async operations
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        
        self.running = True
        self.poll_thread = Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()
        logger.info(f"Polling service started with interval {self.poll_interval}s")
    
    def stop(self) -> None:
        """Stop the polling loop."""
        self.running = False
        if self.poll_thread:
            self.poll_thread.join(timeout=10)
        logger.info("Polling service stopped")
    
    def _poll_loop(self) -> None:
        """Main polling loop."""
        while self.running:
            try:
                for adapter in self.adapters:
                    try:
                        self._poll_adapter(adapter)
                    except Exception as e:
                        logger.error(f"Error polling {adapter.service_name}: {e}")
                
                time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                time.sleep(self.poll_interval)
    
    def _poll_adapter(self, adapter: ServiceAdapter) -> None:
        """Poll a single adapter for new events."""
        with self._lock:
            last_poll = self.last_poll_times.get(adapter.service_name)
        
        # Only fetch events since last poll
        events = adapter.fetch_events(since=last_poll)
        
        if events:
            # Store events in database
            db = SessionLocal()
            try:
                event_service = EventService(db)
                new_events = event_service.ingest_events(events)
                logger.debug(f"Processed {len(new_events)} events from {adapter.service_name.value}")
                
                # Broadcast new events via SSE
                self._broadcast_events(new_events)
            finally:
                db.close()
            
            # Update last poll time
            with self._lock:
                self.last_poll_times[adapter.service_name] = datetime.utcnow()
    
    def _broadcast_events(self, events: List[Any]) -> None:
        """Broadcast newly ingested events to SSE clients."""
        if not sse_tracker.get_count():
            return
        
        for event in events:
            if hasattr(event, 'to_sse_data'):
                sse_data = event.to_sse_data()
            else:
                # Fallback serialization
                sse_data = {
                    "event_id": getattr(event, 'id', None),
                    "event_type": getattr(event, 'event_type', None),
                    "media_id": getattr(event, 'correlation_key', None),
                    "timestamp": getattr(event, 'timestamp', None).isoformat() if getattr(event, 'timestamp', None) else None,
                    "provenance": "polling"
                }
            
            # Send via asyncio loop
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    sse_tracker.broadcast(sse_data),
                    self._loop
                )
    
    def force_poll(self) -> Dict[str, int]:
        """Force immediate polling of all services.
        
        Returns dict of service_name -> events_processed
        """
        results = {}
        
        for adapter in self.adapters:
            try:
                events = adapter.fetch_events()
                
                if events:
                    db = SessionLocal()
                    try:
                        event_service = EventService(db)
                        event_service.ingest_events(events)
                        results[adapter.service_name.value] = len(events)
                        logger.debug(f"Forced poll: {len(events)} events from {adapter.service_name.value}")
                    finally:
                        db.close()
                
                # Update last poll time
                with self._lock:
                    self.last_poll_times[adapter.service_name] = datetime.utcnow()
            except Exception as e:
                logger.error(f"Error forcing poll for {adapter.service_name}: {e}")
                results[adapter.service_name.value] = 0
        
        return results
    
    def get_status(self) -> Dict[str, Any]:
        """Get polling service status."""
        return {
            "running": self.running,
            "poll_interval_seconds": self.poll_interval,
            "adapters_count": len(self.adapters),
            "last_poll_times": {
                service.value: time.isoformat() if isinstance(time := ts, datetime) else str(ts)
                for service, ts in self.last_poll_times.items()
            }
        }
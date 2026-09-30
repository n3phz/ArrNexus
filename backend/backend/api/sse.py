"""SSE event streaming endpoints."""
import logging
import asyncio
from typing import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import StreamingResponse

logger = logging.getLogger("arrnexus.api.sse")

router = APIRouter(prefix="/api/events", tags=["events"])


class SSEClientTracker:
    """Track connected SSE clients for broadcast."""
    
    def __init__(self):
        self.clients: dict[str, asyncio.Queue] = {}
        self._counter = 0
    
    def register(self, client_id: str, queue: asyncio.Queue) -> None:
        """Register a new client."""
        self.clients[client_id] = queue
        logger.info(f"SSE client registered: {client_id} (total: {len(self.clients)})")
    
    def unregister(self, client_id: str) -> None:
        """Unregister a client."""
        if client_id in self.clients:
            del self.clients[client_id]
            logger.info(f"SSE client unregistered: {client_id} (total: {len(self.clients)})")
    
    def get_count(self) -> int:
        """Get current client count."""
        return len(self.clients)
    
    async def broadcast(self, event_data: dict) -> None:
        """Broadcast event to all connected clients."""
        if not self.clients:
            return
        
        # Serialize event data
        import json
        data = json.dumps(event_data)
        
        # Send to all clients with timeout
        tasks = []
        for client_id, queue in self.clients.items():
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                # Client too slow, remove it
                tasks.append(client_id)
        
        # Clean up slow clients
        for client_id in tasks:
            self.unregister(client_id)


# Global SSE client tracker
sse_tracker = SSEClientTracker()


@router.get("/stream")
async def stream_events(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Stream events via Server-Sent Events."""
    
    # Generate unique client ID
    client_id = f"client_{datetime.utcnow().isoformat()}"
    
    # Create queue for this client
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    sse_tracker.register(client_id, queue)
    
    # Clean up on disconnect
    async def cleanup() -> None:
        sse_tracker.unregister(client_id)
    
    background_tasks.add_task(cleanup)
    
    async def generate() -> AsyncGenerator[str, None]:
        """Generate SSE events."""
        try:
            # Send initial connection message
            yield "event: connected\ndata: {"
            yield f'"client_id": "{client_id}"'
            yield "}\n\n"
            
            # Poll for events
            while True:
                # Wait for event or heartbeat
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: media_event\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield f": heartbeat\n\n"
        finally:
            sse_tracker.unregister(client_id)
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def send_sse_event(event_data: dict) -> None:
    """Send event to all SSE clients."""
    await sse_tracker.broadcast(event_data)
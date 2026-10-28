"""Main FastAPI application."""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.health import router as health_router
from backend.api.items import router as items_router
from backend.api.events import router as events_router
from backend.api.sse import router as sse_router
from backend.api.services import router as services_router
from backend.api.webhooks import router as webhooks_router
from backend.api.backfill import router as backfill_router
from backend.api.activity import router as activity_router
from backend.api.attention import router as attention_router
from backend.api.incidents import router as incidents_router
from backend.database.base import Base, engine, init_db
from backend.core.config import get_settings
from backend.services.polling import PollingService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("arrnexus")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # Referrer policy - only send origin on cross-origin requests
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions policy - disable unused features
        response.headers["Permissions-Policy"] = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
        
        # Note: HSTS is handled by Traefik at the edge (TLS termination)
        # Note: CSP is intentionally not set here to avoid breaking the SPA;
        # Traefik or a dedicated CSP middleware can add it if needed.
        
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Initialize database
    init_db()
    logger.info("Database initialized")
    
    # Start polling service
    settings = get_settings()
    app.state.polling_service = PollingService(poll_interval=settings.poll_interval_seconds)
    app.state.polling_service.start()
    logger.info("Polling service started")
    
    yield
    
    # Cleanup
    app.state.polling_service.stop()
    logger.info("Polling service stopped")


def create_application() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title=settings.app_name,
        description="Media Automation Control Plane - Observability and correlation layer for ARR stack",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )
    
    # CORS middleware - use explicit origins from config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(health_router)
    app.include_router(items_router)
    app.include_router(events_router)
    app.include_router(sse_router)
    app.include_router(services_router)
    app.include_router(webhooks_router)
    app.include_router(backfill_router)
    app.include_router(activity_router)
    app.include_router(attention_router)
    app.include_router(incidents_router)

    # Serve the React SPA static assets from the bundled dist directory
    # Support both Docker (/app/frontend/dist) and local development paths
    import pathlib
    possible_paths = [
        "/app/frontend/dist",  # Docker
        str(pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"),  # Local dev
    ]
    frontend_path = next((p for p in possible_paths if os.path.isdir(p)), None)
    if frontend_path:
        app.mount("/", StaticFiles(directory=frontend_path, html=False), name="frontend")

    # SPA fallback middleware: serve index.html for non-API, non-asset routes
    # that return 404 from the static files mount
    class SPAFallbackMiddleware(BaseHTTPMiddleware):
        def __init__(self, app, frontend_path: str):
            super().__init__(app)
            self.frontend_path = frontend_path
            self.index_file = os.path.join(frontend_path, "index.html") if frontend_path else None

        async def dispatch(self, request: Request, call_next):
            # Let the request go through normal routing (API, StaticFiles)
            response = await call_next(request)
            
            # If we got a 404 and it's not an API or static asset path,
            # serve the SPA index.html
            if response.status_code == 404 and self.index_file and os.path.isfile(self.index_file):
                path = request.url.path.lstrip("/")
                # Exclude API routes
                if path.startswith("api/"):
                    return response
                # Exclude static assets
                if path.startswith("assets/"):
                    return response
                # Exclude favicon and other root-level static files
                if path in ("favicon.ico", "robots.txt", "vite.svg"):
                    return response
                # Serve index.html for SPA routes
                return FileResponse(self.index_file)
            
            return response

    if frontend_path:
        app.add_middleware(SPAFallbackMiddleware, frontend_path=frontend_path)

    # Security headers middleware (added LAST so it's the innermost wrapper,
    # ensuring headers are applied to ALL responses including FileResponse from SPA fallback)
    app.add_middleware(SecurityHeadersMiddleware)

    return app


application = create_application()


def main() -> None:
    """Entry point for running the application."""
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "backend.main:application",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info"
    )


if __name__ == "__main__":
    main()
"""Tests for API endpoints."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime

from backend.main import application
from backend.database.base import init_db
from backend.adapters.base import SourceService, MediaType, EventType, EventStatus, RawEvent, ActiveItem, ServiceHealth
from backend.correlation.engine import CorrelationResult


@pytest.fixture
def client():
    """Create test client.

    The client is deliberately not used as a context manager: entering the
    context runs the application lifespan, which starts the live polling
    service. `init_db()` runs the same table-creation step the lifespan
    performs, so the events endpoints can query their tables hermetically.
    """
    init_db()
    return TestClient(application)


@pytest.fixture
def mock_adapter():
    """Mock adapter for testing.

    Patches the *consumer* binding (services.py imported the function by
    name at module level), so the endpoint under test actually sees the
    mock instead of making real network calls to the *Arr services.
    """
    with patch('backend.api.services.get_all_adapters') as mock:
        adapter = MagicMock()
        adapter.service_name = SourceService.RADARR
        adapter.health.return_value = ServiceHealth(
            service=SourceService.RADARR,
            status="healthy",
            version="4.0.0"
        )
        adapter.fetch_events.return_value = []
        adapter.fetch_active_items.return_value = []
        mock.return_value = [adapter]
        yield adapter


class TestHealthEndpoints:
    """Tests for health endpoints."""
    
    def test_health_check(self, client):
        """Test health endpoint."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "arrnexus"
    
    def test_ready_check(self, client):
        """Test readiness endpoint."""
        response = client.get("/api/ready")
        assert response.status_code == 200
        data = response.json()
        assert "ready" in data
    
    def test_health_check_v2(self, client):
        """Test alternative health endpoint."""
        response = client.get("/api/health")
        assert response.status_code == 200


class TestServicesEndpoints:
    """Tests for services endpoints."""
    
    def test_list_services(self, client, mock_adapter):
        """Test listing all services."""
        response = client.get("/api/services")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
    
    def test_get_service_status(self, client, mock_adapter):
        """Test getting specific service status."""
        response = client.get("/api/services/radarr")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "radarr"
        assert data["status"] == "healthy"
    
    def test_get_unknown_service(self, client):
        """Test getting unknown service.

        Unknown service names are a client error: the endpoint returns 404
        with a machine-usable detail (previously a bare Exception -> 500).
        """
        response = client.get("/api/services/unknown")
        assert response.status_code == 404
        assert "Service not found: unknown" in response.json()["detail"]


class TestItemsEndpoints:
    """Tests for items endpoints."""
    
    def test_list_items_empty(self, client):
        """Test listing items when none exist."""
        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
    
    def test_list_items_with_filter(self, client):
        """Test filtering items."""
        response = client.get("/api/items?state=available&media_type=movie")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    def test_get_item_not_found(self, client):
        """Test getting non-existent item."""
        response = client.get("/api/items/nonexistent")
        assert response.status_code == 404
    
    def test_get_item_timeline_not_found(self, client):
        """Test getting timeline for non-existent item."""
        response = client.get("/api/items/nonexistent/timeline")
        assert response.status_code == 404
    
    def test_get_item_explanation_not_found(self, client):
        """Test getting explanation for non-existent item."""
        response = client.get("/api/items/nonexistent/explain")
        assert response.status_code == 404


class TestEventsEndpoints:
    """Tests for events endpoints."""
    
    def test_list_events_empty(self, client):
        """Test listing events when none exist."""
        response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    def test_list_events_with_filters(self, client):
        """Test filtering events."""
        response = client.get("/api/events?source_service=radarr&event_type=wanted")
        assert response.status_code == 200
    
    def test_list_events_pagination(self, client):
        """Test event pagination."""
        response = client.get("/api/events?limit=10&offset=0")
        assert response.status_code == 200


class TestForcePoll:
    """Tests for force poll endpoint."""
    
    def test_force_poll(self, client, mock_adapter):
        """Test forcing a poll."""
        response = client.post("/api/services/poll")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestSPADeepLinks:
    """Regression tests for SPA deep-link fix.

    Direct navigation to SPA routes (/incidents, /attention, /media, /activity)
    must serve index.html, not API 404 JSON. API routes and static assets
    must continue to work correctly.
    """

    def test_spa_root_serves_html(self, client):
        """Root path serves the SPA index.html."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "ArrNexus" in response.text

    def test_spa_incidents_serves_html(self, client):
        """Direct /incidents navigation serves the SPA index.html."""
        response = client.get("/incidents")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "ArrNexus" in response.text

    def test_spa_attention_serves_html(self, client):
        """Direct /attention navigation serves the SPA index.html."""
        response = client.get("/attention")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "ArrNexus" in response.text

    def test_spa_media_serves_html(self, client):
        """Direct /media navigation serves the SPA index.html."""
        response = client.get("/media")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "ArrNexus" in response.text

    def test_spa_activity_serves_html(self, client):
        """Direct /activity navigation serves the SPA index.html."""
        response = client.get("/activity")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "ArrNexus" in response.text

    def test_api_routes_return_json_not_html(self, client):
        """API routes return JSON 404, not HTML."""
        response = client.get("/api/unknown")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
        assert response.json()["detail"] == "Not Found"

    def test_api_incidents_works(self, client):
        """API incidents endpoint works correctly."""
        response = client.get("/api/incidents")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
        assert isinstance(response.json(), list)

    def test_static_assets_served(self, client):
        """Static assets are served by StaticFiles (404 if not present in test env)."""
        # In test environment, frontend/dist may not exist, so we just verify
        # the path is not caught by SPA fallback (would return HTML)
        response = client.get("/assets/nonexistent.js")
        # Should be 404, but with either JSON or plain text - NOT HTML
        assert response.status_code == 404
        content_type = response.headers.get("content-type", "")
        assert "text/html" not in content_type

    def test_favicon_excluded_from_spa(self, client):
        """Favicon requests return 404, not HTML."""
        response = client.get("/favicon.ico")
        assert response.status_code == 404
        content_type = response.headers.get("content-type", "")
        assert "text/html" not in content_type


class TestSecurityHeaders:
    """Tests for security headers."""

    def test_security_headers_present(self, client):
        """Security headers are present on responses."""
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in response.headers

    def test_security_headers_on_spa_routes(self, client):
        """Security headers are present on SPA routes."""
        response = client.get("/incidents")
        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_security_headers_on_api_error(self, client):
        """Security headers are present on API error responses."""
        response = client.get("/api/unknown")
        assert response.status_code == 404
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"


class TestCORS:
    """Tests for CORS configuration."""

    def test_allowed_origin_works(self, client):
        """Allowed origin can access the API."""
        # Production origin
        response = client.get(
            "/api/health",
            headers={"Origin": "https://arrnexus.neph.ovh"}
        )
        assert response.status_code == 200
        assert response.headers.get("Access-Control-Allow-Origin") == "https://arrnexus.neph.ovh"
        assert response.headers.get("Access-Control-Allow-Credentials") == "true"

    def test_localhost_origin_works(self, client):
        """Localhost dev origins can access the API."""
        for origin in ["http://localhost:5173", "http://localhost:3000"]:
            response = client.get(
                "/api/health",
                headers={"Origin": origin}
            )
            assert response.status_code == 200
            assert response.headers.get("Access-Control-Allow-Origin") == origin
            assert response.headers.get("Access-Control-Allow-Credentials") == "true"

    def test_disallowed_origin_rejected(self, client):
        """Disallowed origins are not granted CORS access."""
        response = client.get(
            "/api/health",
            headers={"Origin": "https://evil.com"}
        )
        # The request succeeds but CORS headers should not include the evil origin
        assert response.status_code == 200
        # With allow_credentials=True, the origin must match exactly
        # FastAPI/Starlette will not echo back disallowed origins
        allow_origin = response.headers.get("Access-Control-Allow-Origin")
        assert allow_origin != "https://evil.com"

    def test_preflight_allowed_origin(self, client):
        """Preflight requests work for allowed origins."""
        response = client.options(
            "/api/health",
            headers={
                "Origin": "https://arrnexus.neph.ovh",
                "Access-Control-Request-Method": "GET",
            }
        )
        assert response.status_code == 200
        assert response.headers.get("Access-Control-Allow-Origin") == "https://arrnexus.neph.ovh"
        assert response.headers.get("Access-Control-Allow-Credentials") == "true"

    def test_preflight_disallowed_origin(self, client):
        """Preflight requests fail for disallowed origins."""
        response = client.options(
            "/api/health",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "GET",
            }
        )
        # Preflight may succeed but without CORS headers for disallowed origin
        allow_origin = response.headers.get("Access-Control-Allow-Origin")
        assert allow_origin != "https://evil.com"


class TestDebugMode:
    """Tests for debug mode configuration."""

    def test_debug_false_by_default(self):
        """Debug mode is disabled by default."""
        from backend.core.config import Settings
        # Settings with no env file should have debug=False
        settings = Settings()
        assert settings.debug is False

    def test_debug_can_be_enabled_explicitly(self, monkeypatch):
        """Debug mode can be enabled via environment variable."""
        monkeypatch.setenv("DEBUG", "true")
        from backend.core.config import Settings
        settings = Settings()
        assert settings.debug is True
        monkeypatch.delenv("DEBUG")

    def test_debug_not_enabled_by_missing_env(self):
        """Missing DEBUG env var does not enable debug mode."""
        from backend.core.config import Settings
        # Ensure DEBUG is not set
        import os
        if "DEBUG" in os.environ:
            del os.environ["DEBUG"]
        settings = Settings()
        assert settings.debug is False


class TestDefaultCredentials:
    """Tests for default credentials handling."""

    def test_qbittorrent_no_insecure_defaults(self):
        """qBittorrent credentials default to empty strings, not insecure defaults."""
        from backend.core.config import Settings
        settings = Settings()
        assert settings.qbittorrent_username == ""
        assert settings.qbittorrent_password == ""
        # Specifically NOT the insecure defaults
        assert settings.qbittorrent_username != "admin"
        assert settings.qbittorrent_password != "adminadmin"

    def test_qbittorrent_credentials_from_env(self, monkeypatch):
        """qBittorrent credentials can be set via environment."""
        monkeypatch.setenv("QBITTORRENT_USERNAME", "myuser")
        monkeypatch.setenv("QBITTORRENT_PASSWORD", "mypassword")
        from backend.core.config import Settings
        settings = Settings()
        assert settings.qbittorrent_username == "myuser"
        assert settings.qbittorrent_password == "mypassword"
        monkeypatch.delenv("QBITTORRENT_USERNAME")
        monkeypatch.delenv("QBITTORRENT_PASSWORD")
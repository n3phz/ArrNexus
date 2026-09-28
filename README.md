# Media Control Plane

**Evidence-based control and observability for automated media pipelines.**

Media Control Plane (MCP) is a specialized control plane for media automation stacks built around **Sonarr**, **Radarr**, **qBittorrent**, **Prowlarr**, and **Guardarr**.

It is **not**:
- A replacement for Sonarr/Radarr
- A generic dashboard like Homarr
- A media player or streaming server

Instead, it is the **evidence-first control layer** that observes, correlates, and explains events across these systems.

## Core Concept

```
WANTED
  ↓
SEARCH / INDEXER ACTIVITY
  ↓
GRAB / DOWNLOAD / IMPORT
  ↓
MEDIA ITEM
  ↓
STATE VISUALIZATION
  ↓
WHY EXPLANATION
```

Every step in this pipeline is grounded in **actual observed data**, never speculation or inference.

## Key Features

| Feature | Description |
|---------|-------------|
| **Evidence Chain** | Full correlation from indexer query → grab → download → import → available |
| **Timeline View** | Interactive media timelines showing all events |
| **WHY Engine** | Explanations tied to concrete evidence, never assumptions |
| **Failure Analysis** | Groups real problems: stalled downloads, failed imports, unhealthy indexers |
| **Storage Visibility** | Dashboard shows media/download storage usage |
| **Historical Backfill** | Idempotent import of past events |
| **Production Deployment** | Docker Compose for clean installation |
| **SSE Event Streaming** | Real-time event delivery to frontend clients |

## Current State

| Component | Status |
|-----------|--------|
| **Sonarr Integration** | ✅ Real-time webhook support |
| **Radarr Integration** | ✅ Webhook support |
| **qBittorrent Integration** | ✅ Polling-based event ingestion |
| **Prowlarr Integration** | ✅ Observes indexer activity |
| **Guardarr Integration** | ✅ Observes storage admission events |
| **Backend Tests** | 50 passed (unit/integration) |
| **Frontend Build** | Successful |
| **Docker Deployment** | Configuration provided |
| **SSE Event Streaming** | ✅ Implemented and tested (Phase 3B) |
| **Live Guardarr Correlation** | ⚠ Not verified (environment constraints) |

## Live Validation Status

The system has been validated against a live test deployment with 50 core tests passing. However, **real-time Guardarr correlation has not yet been observed** due to environment constraints (qBittorrent API IP ban, synthetic Guardarr reservations with null `torrent_metadata_hash`). The implementation remains ready for production deployment.

**Phase 3B Status:**
- **SSE Event Streaming:** Implemented and tested
- **Guardarr Live Correlation:** Not verified (environment-blocked)
- **qBittorrent API Access:** Currently blocked (IP ban from failed auth)
- **Sonarr/Radarr API Access:** Currently returns 403

**The complete Guardarr → qBittorrent → Sonarr/Radarr correlation chain remains unverified** until live Saltbox environment constraints are resolved.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/n3phz/media-control-plane.git
cd media-control-plane

# Build and run with Docker Compose
docker compose up --build
```

Environment variables are configured via `.env` with placeholders. Replace with actual values for production.

## Architecture

![Architecture Diagram](docs/architecture.svg)

*(Rendered Mermaid diagram showing event flow from data sources → correlation engine → UI)*

## Evidence Boundaries

Media Control Plane adheres to strict evidence-based principles:

- **Never invent causal relationships** without observable proof
- **Preserve source metadata** for all events
- **Distinguish certainty levels**: KNOWN | ESTIMATED | UNKNOWN
- **Explicitly document limitations** (e.g., Guardarr live correlation not yet verified)
- **SSE provides real-time DELIVERY** of events discovered by Media Control Plane — it does NOT make the external integrations themselves real-time.

## Component Architecture

```
                    ┌─────────────────────┐
                    │  qBittorrent API    │
                    └─────────┬─────────┘
                              │ Polling (30s interval)
                    └───────▼─────────┘
                    ┌─────────────────────┐
                    │ Media Control Plane │
                    │  Backend (FastAPI)  │
                    │  • Correlation Engine│
                    │  • Polling Service  │
                    │  • SSE Endpoint     │
                    │  • SQLite Persistence│
                    └───────┬─────────────┘
                              │ SSE (text/event-stream)
                    ┌───────▼─────────┐
                    │   Frontend      │
                    │  • React + Vite  │
                    │  • SSE Client    │
                    │  • Timeline UI   │
                    └─────────────────┘
```

## Installation Guide

### Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.20+
- Access to Sonarr/Radarr/QBittorrent services

### Configuration

Create `.env` from `.env.example` and populate with your service URLs and API keys.

### Deployment

```bash
docker compose up -d
```

Health checks available at `/api/health`.

### API Documentation

Visit `/api/docs` for interactive Swagger UI.

### SSE Endpoint

```
GET /api/events/stream
```

Connect via `EventSource` to receive real-time event updates.

## Evidence Boundaries

### Verified Live

- 50 backend unit/integration tests pass
- 16 Guardarr adapter tests pass
- Docker deployment configuration works
- 30-second polling interval functional
- SSE event streaming operational

### Implemented But Not Live-Verified

- Guardarr live correlation (hash chain validation)
- qBittorrent hash ↔ Sonarr/Radarr downloadId correlation
- Sonarr/Radarr real-time webhook events
- Complete end-to-end media lifecycle with real Guardarr data

### Not Available From Source

- Prowlarr search-result → grab causal chain
- Guardarr webhook events (API currently returns synthetic test data only)

For Guardarr specifically, **do not claim hash correlation is verified** unless a real non-null `torrent_metadata_hash` has been observed and matched in the live Saltbox deployment.

## API Endpoints

### Health

- `GET /api/health` - Basic health check
- `GET /api/ready` - Readiness check

### Items

- `GET /api/items` - List media items
- `GET /api/items/{id}` - Get item details
- `GET /api/items/{id}/timeline` - Get timeline
- `GET /api/items/{id}/explain` - Get explanation
- `GET /api/items/orphans` - Get unmatched torrents

### Services

- `GET /api/services` - List service statuses
- `GET /api/services/{service}` - Get service status
- `POST /api/services/poll` - Force poll

### Events

- `GET /api/events` - List events
- `GET /api/events/stream` - **SSE event stream** (Phase 3B)
- `POST /api/backfill` - Start historical backfill
- `GET /api/backfill/status` - Get backfill status

### Webhooks

- `POST /api/webhook/sonarr` - Receive Sonarr webhooks
- `POST /api/webhook/radarr` - Receive Radarr webhooks
- `POST /api/webhook/prowlarr` - Receive Prowlarr webhooks

### Backfill

- `POST /api/backfill` - Start historical backfill
- `GET /api/backfill/status` - Get backfill status
- `POST /api/backfill/cancel` - Cancel backfill

### Activity

- `GET /api/activity` - List activity events
- `GET /api/activity/stats` - Get activity statistics
- `GET /api/activity/failures` - Get failures
- `GET /api/activity/stalled` - Get stalled downloads

## Future Improvements

- [x] Add SSE event streaming (Phase 3B)
- [ ] Add Prometheus metrics
- [ ] Add authentication/authorization
- [ ] Support PostgreSQL
- [ ] Add real-time Guardarr webhooks
- [ ] Add Lidarr/Readarr integration
- [ ] Add Bazarr integration
- [ ] Add Overseerr/Jellyseerr integration
- [ ] Guardarr live correlation validation

---

*Media Control Plane is currently in active development. Features are progressively rolling out. Contributions welcome.*
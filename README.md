# ArrNexus

**The operational control plane for the Arr stack.**

ArrNexus is a specialized control plane for media automation stacks built around **Sonarr**, **Radarr**, **qBittorrent**, **Prowlarr**, and **Guardarr**.

It is **not**:
- A replacement for Sonarr/Radarr
- A generic dashboard like Homarr
- A media player or streaming server

Instead, it is the **evidence-first control layer** that observes, correlates, and explains events across these systems.

**Public URL:** [https://arrnexus.neph.ovh](https://arrnexus.neph.ovh)

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
| **Evidence Boundary Model** | ✅ Implemented and tested (Phase 4A) |
| **Live Guardarr Correlation** | ⚠ Not verified (environment constraints) |

## Live Validation Status

The system has been validated against a live test deployment with 50 core tests passing. However, **real-time Guardarr correlation has not yet been observed** due to environment constraints (qBittorrent API IP ban, synthetic Guardarr reservations with null `torrent_metadata_hash`). The implementation remains ready for production deployment.

**Phase 3B Status:**
- **SSE Event Streaming:** Implemented and tested

**Phase 4A Status:**
- **Evidence Boundary Model:** Implemented and tested (OBSERVED/CORRELATED/INFERRED/UNKNOWN/BLOCKED/SYNTHETIC)
- **WHY Engine:** Evidence-grounded wording
- **Guardarr Synthetic Classification:** Implemented
- **Prowlarr Causal Boundary:** Implemented

**Live Environment Status:**
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

ArrNexus adheres to a strict, explicit evidence model. Every event and
correlation is classified by an `evidence_boundary` so the system never presents
missing evidence as a confident causal explanation.

### Evidence Boundary Values

| Value | Meaning | Example |
|-------|---------|---------|
| **OBSERVED** | Directly observed from an external service | A Sonarr `grab` event with its original payload |
| **CORRELATED** | Relationship established from observed evidence | Exact hash match between *Arr and qBittorrent |
| **INFERRED** | Derived conclusion based on available evidence | Category/tag correlation without an exact hash match |
| **UNKNOWN** | Insufficient evidence to establish a conclusion | No events explain why an import failed |
| **BLOCKED** | Required evidence could not be obtained (external dependency unavailable) | Cannot verify a torrent hash because qBittorrent API is down |
| **SYNTHETIC** | Test/synthetic data, not from the live environment | A Guardarr reservation with a `test-` content id and null hash |

### Confidence Levels

Confidence reflects how strongly events are correlated, and each level carries an
explicit basis exposed by the API (`confidence_basis`):

**Public URL:** [https://arrnexus.neph.ovh](https://arrnexus.neph.ovh)

- **HIGH** — exact hash match between *Arr `source_download_id` and qBittorrent `hash`.
- **MEDIUM** — category/tag correlation **and** at least one *Arr event (no hash match).
- **LOW** — only qBittorrent events (no *Arr history) or only heuristic/title matching.

### UNKNOWN vs BLOCKED

These are **not** interchangeable:

- **UNKNOWN** — the system has insufficient evidence (e.g. *"Unable to determine why this torrent was not imported*).
- **BLOCKED** — the evidence source itself was unavailable (e.g. *"Unable to verify torrent hash because qBittorrent API is unavailable"*).

The system never reports UNKNOWN when the real issue is BLOCKED, and never reports
a correlation as OBSERVED when it is only CORRELATED or INFERRED.

### The WHY Engine

Explanations are grounded in evidence rather than asserting causality. For example:

- *"Import completed according to observed Sonarr history."* (not "Media is available")
- *"Download is stalled according to qBittorrent state."* (observed)
- *"Required evidence could not be obtained because an external service was unavailable."* (blocked)
- *"The available evidence is insufficient to establish a conclusion."* (unknown)

**Public URL:** [https://arrnexus.neph.ovh](https://arrnexus.neph.ovh)

### Guardarr Synthetic Data

Guardarr reservations that are clearly test/synthetic (e.g. `test-` markers in
`content_id` / `idempotency_key`, or a null `torrent_metadata_hash` associated with
a test reservation) are classified **SYNTHETIC** and surfaced distinctly in the
UI rather than being presented as live observations. This is driven by actual
reservation metadata, not a hard-coded blanket rule.

### Prowlarr Causal Boundary

A Prowlarr search/query observation and a later Sonarr/Radarr grab are recorded as
two independent **OBSERVED** events. Media Control Plane does **not** claim that
Prowlarr caused the grab; no causal link is inferred from the available evidence.

### SSE Delivery vs External Real-Time

SSE provides real-time **delivery** of events discovered by Media Control Plane. It
does **not** make the external integrations themselves real-time:

```
Guardarr
  ↓ polling
Media Control Plane
  ↓ SSE
Browser
```

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

## Evidence Boundary Status

### Verified (Tested)

- Evidence model: OBSERVED / CORRELATED / INFERRED / UNKNOWN / BLOCKED / SYNTHETIC
- Guardarr synthetic classification (unit-tested)
- Prowlarr causal-boundary handling (unit-tested)
- Confidence basis (HIGH/MEDIUM/LOW) exposed via API
- SSE event streaming operational

### Implemented But Not Live-Verified

- Guardarr live correlation (hash chain validation)
- qBittorrent hash ↔ Sonarr/Radarr downloadId correlation
- Sonarr/Radarr real-time webhook events
- Complete end-to-end media lifecycle with real Guardarr data

### Not Available From Source / BLOCKED

- Prowlarr search-result → grab causal chain (not inferred by this system)
- Guardarr webhook events (API currently returns synthetic test data only)
- qBittorrent API access (currently blocked by authentication/IP-ban)
- Sonarr/Radarr API access (currently returning 403)

For Guardarr specifically, **do not claim hash correlation is verified** unless a
real non-null `torrent_metadata_hash` has been observed and matched in the live
Saltbox deployment.

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
- [x] Evidence boundary hardening (Phase 4A)
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
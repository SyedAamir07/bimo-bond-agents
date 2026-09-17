# Camera Agent

Manage camera functions, improve image quality, and control approved
in-app camera features (project doc requirement **AG-01**).

> **Product gate (AG-01a):** the project doc requires supported
> features and target devices to be defined and approved *before*
> AG-01 is considered complete (blocking decision **PD-02**). This
> agent enforces that approval at runtime by treating the backend's
> Camera Studio catalog as the sole source of truth — it does not
> decide approval itself, and code alone does not satisfy AG-01a until
> product has actually approved what's in that catalog.

Deterministic workflow, not an LLM agent — approval is a lookup
against product-approved data, not a model decision.

## Agent contract

| Field | Value |
|---|---|
| Objective | Manage camera functions, improve image quality, and control approved in-app camera features. |
| Inputs / sources | `camera.settings.requested`, `camera.feature.toggled` (dispatched by the Orchestration Agent); `GET /camera-studio/catalog` (backend, source of truth for approval) |
| Outputs | `camera.feature.applied`, `camera.feature.rejected`, `camera.settings.reported` |
| Tools available | `camera_studio_catalog` client (backend HTTP, cached) |
| Permission scopes | `device.camera.read`, `device.camera.write` |
| Subscribed topics | `camera.settings.requested`, `camera.feature.toggled` |
| Published topics | `camera.feature.applied`, `camera.feature.rejected`, `camera.settings.reported` |
| Failure cases | `unapproved_feature`, `catalog_unavailable` (fails closed — see below) |
| Accountable owner | platform-media |
| Acceptance criteria | Feature toggle latency measurable; never applies a feature absent from the backend catalog; a catalog outage does not silently approve everything |
| Automatic actions | Apply a catalog-approved feature, report available filters/effects |
| Human review required | Approving new filters/effects — done through the Camera Studio **admin dashboard** (`camera-studio.controller.ts` admin routes), not by this agent |

## How it works

- **No hardcoded feature list.** The backend's Camera Studio module
  already implements feature approval: filters and AR effects are
  created and activated through the admin dashboard, and
  `GET /camera-studio/catalog` returns only the currently-active
  (`isActive: true`) ones, each with a stable `slug`. This agent
  fetches and caches that catalog (`catalog_client.py`) instead of
  maintaining its own list that could silently drift out of sync with
  what product actually approved.
- **Fails closed, not open.** If the catalog has never been
  successfully fetched (backend unreachable on first request), nothing
  is approved — every `camera.feature.toggled` is rejected until a
  catalog fetch succeeds. If a catalog *was* previously fetched and a
  later refresh fails (transient outage), the last-known-good catalog
  keeps being served rather than clearing to "reject everything" —
  either extreme (approve-all on outage, or drop previously-approved
  features on a blip) would be wrong; see `catalog_client.py`'s
  `fetch()` docstring for the exact policy.
- **`camera.settings.reported`** lists the currently cached approved
  filters and effects — useful for a client/dashboard to show "what's
  available right now" without querying the backend directly.
- **Device-level toggles** (grid overlay, HDR, low-light boost, ...)
  that aren't part of the visual filter/effect catalog are out of
  scope until product defines and exposes an equivalent approved list
  for them — this agent does not invent one.
- **Actual camera rendering** (native CameraX/AVFoundation, frame
  processing) lives in the mobile app
  (`bimobond-app/lib/app/camera_engine`, `ar_camera`). This agent
  coordinates approval + event flow between the orchestrator and the
  device; it does not render frames itself.

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis the NestJS backend uses (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | **Required for this agent** (not optional) — the approval catalog comes from here. e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge. |
| `CATALOG_CACHE_TTL_SECONDS` | Local tuning | How long a fetched catalog is trusted before re-fetching (default 60s). |

The backend also needs `AGENT_EVENTS_ENABLED=true` for the Orchestration
Agent's dispatched `camera.feature.toggled` events to reach this agent
over the shared Redis Stream.

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.camera_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8081): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```

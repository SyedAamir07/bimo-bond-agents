"""
Approved camera feature catalog -- fetched from the backend, not
hardcoded in the agent.

Per the project doc: "Define and approve supported features and target
devices before implementation" (AG-01a, a blocking product gate --
PD-02 in the "Product decisions required" section). The backend's
Camera Studio module (`GET /camera-studio/catalog`) is the actual,
already-built approval mechanism: filters and AR effects are created
and activated through the admin dashboard
(`camera-studio.controller.ts` admin routes), and only `isActive`
entries appear in the public catalog. This agent treats that catalog
as the single source of truth for "is this feature approved" instead
of maintaining its own list that could drift out of sync.

Device-level camera toggles (grid overlay, HDR, ...) that aren't part
of the visual filter/effect catalog are out of scope until product
defines and adds them to a similar approved list (see README).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundation import BackendClient

logger = logging.getLogger(__name__)


@dataclass
class CameraCatalog:
    """Snapshot of approved filters/effects, keyed by their stable slug."""

    version: str | None = None
    filter_slugs: frozenset[str] = field(default_factory=frozenset)
    effect_slugs: frozenset[str] = field(default_factory=frozenset)
    fetched_at: float = field(default_factory=time.time)

    @property
    def all_approved_slugs(self) -> frozenset[str]:
        return self.filter_slugs | self.effect_slugs

    def is_approved(self, feature_slug: str) -> bool:
        return feature_slug in self.all_approved_slugs

    @classmethod
    def empty(cls) -> "CameraCatalog":
        return cls()

    @classmethod
    def from_api_response(cls, data: dict) -> "CameraCatalog":
        filter_slugs: set[str] = set()
        for category in data.get("colorFilterCategories", []) or []:
            for item in category.get("filters", []) or category.get("items", []) or []:
                slug = item.get("slug")
                if slug:
                    filter_slugs.add(str(slug))

        effect_slugs: set[str] = set()
        for category in data.get("effectCategories", []) or []:
            for item in category.get("effects", []) or category.get("items", []) or []:
                slug = item.get("slug")
                if slug:
                    effect_slugs.add(str(slug))

        return cls(
            version=data.get("version"),
            filter_slugs=frozenset(filter_slugs),
            effect_slugs=frozenset(effect_slugs),
        )


class CatalogClient:
    """
    Fetches and caches the approved camera catalog from the backend.

    A failed fetch keeps serving the last-known-good catalog (fail-safe
    for a transient backend outage) rather than approving everything or
    rejecting everything -- see fetch() docstring for the exact policy.
    """

    def __init__(self, backend: "BackendClient", *, ttl_seconds: float) -> None:
        self._backend = backend
        self._ttl_seconds = ttl_seconds
        self._cached: CameraCatalog = CameraCatalog.empty()
        self._last_fetch_ok = False

    def get(self, *, force_refresh: bool = False) -> CameraCatalog:
        stale = (time.time() - self._cached.fetched_at) > self._ttl_seconds
        if force_refresh or stale or not self._last_fetch_ok:
            self.fetch()
        return self._cached

    def fetch(self) -> CameraCatalog:
        """
        Refresh the cached catalog from GET /camera-studio/catalog.

        On failure (backend disabled/unreachable), keeps the previous
        cached catalog rather than clearing it -- a transient outage
        must not suddenly make every previously-approved feature look
        unapproved. If there was never a successful fetch, the cache
        stays empty (fail-closed: nothing is approved until a catalog
        has actually been fetched).
        """
        if not self._backend.enabled:
            logger.debug("BackendClient disabled -- camera catalog stays at last-known state")
            return self._cached

        try:
            data = self._backend.get("/camera-studio/catalog")
        except Exception:  # noqa: BLE001 - a fetch failure must not crash the agent
            logger.exception("Failed to fetch camera-studio catalog -- keeping cached catalog")
            self._last_fetch_ok = False
            return self._cached

        if not isinstance(data, dict):
            logger.warning("Unexpected camera-studio catalog response type=%s", type(data))
            self._last_fetch_ok = False
            return self._cached

        self._cached = CameraCatalog.from_api_response(data)
        self._last_fetch_ok = True
        logger.info(
            "Refreshed camera catalog version=%s filters=%s effects=%s",
            self._cached.version, len(self._cached.filter_slugs), len(self._cached.effect_slugs),
        )
        return self._cached

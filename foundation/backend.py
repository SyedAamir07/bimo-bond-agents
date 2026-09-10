"""
HTTP bridge from agents to the NestJS Bimo Bond backend.

Uses stdlib urllib so foundation stays dependency-light. When
BACKEND_BASE_URL is unset, the client is disabled (local smoke tests).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class BackendClientDisabled(Exception):
    """Raised when BackendClient is used without BACKEND_BASE_URL."""


class BackendClient:
    """Minimal authenticated HTTP client for NestJS APIs."""

    def __init__(
        self,
        base_url: str = "",
        api_token: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_token = api_token or ""
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def get(
        self,
        path: str,
        *,
        correlation_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        return self._request("GET", path, correlation_id=correlation_id, headers=headers)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        return self._request(
            "POST",
            path,
            body=body,
            correlation_id=correlation_id,
            headers=headers,
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        if not self.enabled:
            raise BackendClientDisabled(
                "BACKEND_BASE_URL is not set — BackendClient is disabled"
            )

        url = urljoin(self.base_url + "/", path.lstrip("/"))
        req_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_token:
            req_headers["Authorization"] = f"Bearer {self.api_token}"
        if correlation_id:
            req_headers["X-Correlation-Id"] = correlation_id
        if headers:
            req_headers.update(headers)

        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.error(
                "Backend %s %s failed status=%s detail=%s",
                method,
                path,
                exc.code,
                detail[:500],
            )
            raise
        except urllib.error.URLError as exc:
            logger.error("Backend %s %s unreachable: %s", method, path, exc.reason)
            raise

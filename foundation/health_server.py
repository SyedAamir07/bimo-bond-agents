"""
HTTP health / contract / metrics server shared by every agent.

Listens on AgentSettings.health_port so Compose port mappings work.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .contracts import AgentContract
    from .health import HealthStatus
    from .metrics import AgentMetrics

logger = logging.getLogger(__name__)


class HealthServer:
    """Background ThreadingHTTPServer exposing /health, /contract, /metrics."""

    def __init__(
        self,
        port: int,
        *,
        health_provider: Callable[[], HealthStatus],
        contract_provider: Callable[[], AgentContract | None],
        metrics_provider: Callable[[], AgentMetrics],
    ) -> None:
        self.port = port
        self._health_provider = health_provider
        self._contract_provider = contract_provider
        self._metrics_provider = metrics_provider
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003
                logger.debug("health_server: " + format, *args)

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    body = outer._health_provider().to_dict()
                    self._json(200, body)
                elif path == "/contract":
                    contract = outer._contract_provider()
                    if contract is None:
                        self._json(404, {"error": "no contract registered"})
                    else:
                        self._json(200, contract.to_dict())
                elif path == "/metrics":
                    text = outer._metrics_provider().to_prometheus()
                    raw = text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                else:
                    self._json(404, {"error": "not found"})

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"health-server-{self.port}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Health server listening on port=%s", self.port)

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None
        logger.info("Health server stopped port=%s", self.port)

"""
Standardized structured logging.

Every agent logs in the same shape (agent name always present, level
controlled by config) so centralized log aggregation (the
"observability baseline") can filter/correlate across agents without
per-agent parsing rules.
"""
from __future__ import annotations

import logging
import sys


def configure_logging(agent_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt=f"%(asctime)s | level=%(levelname)s | agent={agent_name} | %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

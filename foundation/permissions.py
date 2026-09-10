"""
Least-privilege permission checks shared by every agent.

Scopes are declared on AgentSettings.permission_scopes. This module
enforces them so agents cannot invent access at runtime.
"""
from __future__ import annotations

from .config import AgentSettings


class PermissionDenied(Exception):
    """Raised when an action requires a scope the agent does not hold."""

    def __init__(self, required_scope: str, agent_name: str) -> None:
        self.required_scope = required_scope
        self.agent_name = agent_name
        super().__init__(
            f"Agent={agent_name} lacks required_scope={required_scope}"
        )


def has_scope(settings: AgentSettings, required_scope: str | None) -> bool:
    """Return True if no scope is required, or the agent holds it."""
    if not required_scope:
        return True
    return required_scope in settings.permission_scopes


def assert_scope(settings: AgentSettings, required_scope: str | None) -> None:
    """Raise PermissionDenied when the agent lacks required_scope."""
    if not has_scope(settings, required_scope):
        assert required_scope is not None
        raise PermissionDenied(required_scope, settings.agent_name)

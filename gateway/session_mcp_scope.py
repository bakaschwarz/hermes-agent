"""Profile MCP servers marked ``per_session`` get one connection per gateway session.

A profile's MCP connection is shared by every concurrent session the gateway serves for that
profile. That is right for stateless servers, and wrong for a stateful one whose state *is*
the workspace it was started in: Serena keeps one active project per process, so two runs in
different worktrees shared one child and one active project, and a later
``activate_project`` moved the earlier run's reads — and, before PER-792's containment, its
writes — into the sibling tree.

A server declared ``per_session: true`` in ``mcp_servers`` is instead connected under the
session's own tool scope (the registry's ``session_tool_scope`` overlay), with the session's
workspace as the stdio child's cwd. Two sessions then run two children in two trees and can
never observe each other's active project. The scope is unique per (profile, session id), so
a second session never adopts the first one's connection.

The connection is spawned lazily (the config keeps its own ``lazy`` setting, and the per-turn
registration reads the profile's schema cache), so the cost is paid by the session that uses
the server, not at gateway start. It is closed when the turn that opened it ends: a later
turn of the same session opens its own in the same workspace. Holding it between turns is a
possible optimisation, not a correctness requirement.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import logging

logger = logging.getLogger(__name__)

SCOPE_PREFIX = 'session-mcp:'


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def per_session_servers(config) -> dict:
    """The ``mcp_servers`` entries this gateway must not share between sessions."""
    servers = (config or {}).get('mcp_servers') or {}
    return {name: cfg for name, cfg in servers.items()
            if isinstance(cfg, dict) and _truthy(cfg.get('per_session'))
            and cfg.get('enabled', True) is not False}


def session_scope(authority, session_id) -> str:
    """A session's own ledger scope: never shared with another session or profile."""
    profile = getattr(authority, 'profile_id', None) or ''
    return SCOPE_PREFIX + hashlib.sha256(f'{profile}:{session_id}'.encode()).hexdigest()


def session_configs(servers: dict, cwd: str) -> dict:
    """``per_session`` configs pinned to the session's workspace.

    The workspace becomes the child's cwd unless the server pins its own ``cwd`` explicitly
    (then that path is the operator's choice and is left alone).
    """
    return {name: {**cfg, 'cwd': cfg.get('cwd') or cwd} for name, cfg in servers.items()}


@contextmanager
def session_mcp_scope(policy, authority=None, session_id=None):
    """Run *policy*'s turn inside its own MCP overlay with its ``per_session`` servers connected.

    A no-op for a profile without such servers, a single-process CLI (no gateway session id)
    and every single-profile process: the connection there is already the run's own.
    """
    if policy is None or not session_id:
        yield
        return
    servers = per_session_servers(policy.config(authority))
    if not servers:
        yield
        return
    from tools.registry import session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers

    scope = session_scope(authority, session_id)
    with session_tool_scope(scope):
        try:
            register_mcp_servers(session_configs(servers, policy.cwd))
            yield
        finally:
            # Per-turn lifetime: no process outlives the run that needed it, and a committed
            # workspace move can never leave a child pointed at the old tree.
            shutdown_mcp_servers(scope=scope)


def release_session_mcp(authority, session_ids) -> None:
    """Close the per-session connections of retired sessions (idempotent)."""
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    for session_id in session_ids:
        try:
            shutdown_mcp_servers(scope=session_scope(authority, session_id))
        except Exception:  # a teardown failure must not block retirement
            logger.warning('Could not close per-session MCP connections for %s', session_id, exc_info=True)

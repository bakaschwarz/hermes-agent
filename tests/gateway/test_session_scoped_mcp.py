"""Two sessions of one profile, each in its own worktree, must not share a stateful MCP child.

PER-794: Serena keeps one active project per process. A profile-scoped connection served every
concurrent session of that profile, so a run in worktree B moved a run in worktree A's active
project. ``per_session`` servers now get one connection per session, in the session's workspace.
"""
import concurrent.futures
import json
import sys


SERVER = '''import json, os, sys
for line in sys.stdin:
    r = json.loads(line); method = r.get("method"); ident = r.get("id")
    if ident is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": r["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "stateful", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "where", "description": "the process cwd",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": os.getcwd()}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
'''


class _Policy:
    def __init__(self, cwd):
        self.cwd = cwd

    def config(self, authority=None, *, secrets=True):
        return {'mcp_servers': {'stateful': {
            'command': sys.executable, 'args': [str(SCRIPT)], 'connect_timeout': 10,
            'per_session': True, 'lazy': False}}}


class _Authority:
    profile_id = 'profile-under-test'


def _run_session(worktree, session_id):
    from gateway.session_mcp_scope import session_mcp_scope, session_scope
    from tools.registry import registry

    authority = _Authority()
    with session_mcp_scope(_Policy(str(worktree)), authority, session_id):
        names = [n for n in registry.get_all_tool_names() if n.startswith('mcp__stateful__')]
        assert names == ['mcp__stateful__where'], names
        return session_scope(authority, session_id), registry.dispatch(names[0], {})


def test_two_sessions_each_see_their_own_workspace(tmp_path):
    global SCRIPT
    SCRIPT = tmp_path / 'stateful.py'
    SCRIPT.write_text(SERVER)
    a_tree = tmp_path / 'worktree-a'
    b_tree = tmp_path / 'worktree-b'
    a_tree.mkdir()
    b_tree.mkdir()

    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(_run_session, a_tree, 'session-a')
            b = pool.submit(_run_session, b_tree, 'session-b')
            a_scope, a_out = a.result(timeout=60)
            b_scope, b_out = b.result(timeout=60)
        assert a_scope != b_scope
        # Activating B never moves A: each session's child lives in its own tree.
        assert str(a_tree) in a_out and str(b_tree) not in a_out, a_out
        assert str(b_tree) in b_out and str(a_tree) not in b_out, b_out
    finally:
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers
        for scope in locals().get('a_scope'), locals().get('b_scope'):
            if scope:
                shutdown_mcp_servers(scope=scope)


def test_profile_discovery_skips_per_session_servers(monkeypatch):
    """A multiplexing process must not open the profile-shared child at all."""
    from tools import mcp_tool_discovery

    config = {'stateful': {'command': 'x', 'per_session': True},
              'shared': {'command': 'y'}}
    monkeypatch.setattr(mcp_tool_discovery._config, '_load_mcp_config', lambda: dict(config))
    monkeypatch.setattr('agent.secret_scope.serves_routed_profile', lambda: True)
    seen = []
    monkeypatch.setattr(mcp_tool_discovery, 'register_mcp_servers',
                        lambda servers: seen.append(dict(servers)) or [])
    assert mcp_tool_discovery.discover_mcp_tools() == []
    assert seen == [] or all(name != 'stateful' for name in seen[0])


def test_per_session_servers_are_selected_and_pinned_to_the_session_cwd(tmp_path):
    from gateway.session_mcp_scope import per_session_servers, session_configs

    config = {'mcp_servers': {
        'stateful': {'command': 'x', 'per_session': True},
        'shared': {'command': 'y'},
        'pinned': {'command': 'z', 'per_session': 'true', 'cwd': '/opt/fixed'},
        'off': {'command': 'w', 'per_session': True, 'enabled': False},
    }}
    servers = per_session_servers(config)
    assert set(servers) == {'stateful', 'pinned'}
    configs = session_configs(servers, str(tmp_path))
    assert configs['stateful']['cwd'] == str(tmp_path)   # the session's workspace wins
    assert configs['pinned']['cwd'] == '/opt/fixed'      # an explicit operator cwd is kept

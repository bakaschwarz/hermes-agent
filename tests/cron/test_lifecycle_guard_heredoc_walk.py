"""The referenced-script walk sees the same inert-heredoc-masked view as the direct scan (#110422).

``_direct_lifecycle_scan`` masks provably-inert heredoc bodies (quoted delimiter, allowlisted
data consumer such as ``python3 - <<'PY'``), but ``_contains_unsafe_gateway_action`` walked
referenced scripts and ``sh -c`` payloads on the raw command, so a >1 MiB data path mentioned
inside the Python body failed closed as an oversized "script".
"""

import pytest

from cron.lifecycle_guard import contains_gateway_lifecycle_command_or_referenced_script as guard


def _big_file(tmp_path):
    path = tmp_path / "big_blob.bin"
    path.write_bytes(b"\0" * (2 * 1024 * 1024))
    return path


def test_inert_heredoc_body_path_not_walked_as_script(tmp_path):
    big = _big_file(tmp_path)
    command = f"python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('{big}').stat().st_size)\nPY"
    assert guard(command, cwd=str(tmp_path)) is False


def test_prefixed_inert_heredoc_body_path_not_walked_as_script(tmp_path):
    big = _big_file(tmp_path)
    command = (
        f"cd {tmp_path} && python3 - <<'PY'\n"
        f"from pathlib import Path\nprint(Path('{big}').stat().st_size)\nPY"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_allowlisted_name_function_heredoc_stays_visible(tmp_path):
    command = "python3() { bash; }; python3 <<'PY'\nhermes gateway restart\nPY"
    assert guard(command, cwd=str(tmp_path)) is True


def test_unquoted_heredoc_body_path_still_walked(tmp_path):
    """An expansion-capable body is not provably inert: the walk still sees it and fails closed."""
    big = _big_file(tmp_path)
    assert guard(f"cat > /tmp/x <<EOF\n{big}\nEOF", cwd=str(tmp_path)) is True


def test_inert_heredoc_body_script_path_still_read(tmp_path):
    """Masking hides the body from the *executed* view only: a lifecycle script named inside a
    Python body is still handed to ``os.system`` at runtime, so its contents must still be read."""
    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = f"python3 - <<'PY'\nimport os\nos.system('{script}')\nPY"
    assert guard(command, cwd=str(tmp_path)) is True


def test_mentioned_data_file_that_cannot_be_scanned_is_not_a_verdict(tmp_path, monkeypatch):
    """A file only MENTIONED in an inert body may exhaust the text budget (one >64 KiB line), pull
    in 64+ remote-read misses (a markdown table of paths) or be a live SQLite database: each is
    "nothing to scan", never a block (#113944). The same file *executed* still fails closed.

    A mention the body only READS is not walked at all now — reading it is what turned a data
    file's own text into a verdict — so the remote callback is never consulted for these."""
    from cron import lifecycle_guard
    from hermes_cli.sqlite_safe_read import connect_tracked

    minified = tmp_path / "minified.json"
    minified.write_text("[" + "1," * 40000 + "1]", encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("\n".join(f"| /opt/frag{i}/tool-{i}.md | note |" for i in range(70)), encoding="utf-8")
    db = tmp_path / "state.db"
    conn = connect_tracked(db)
    monkeypatch.setattr(lifecycle_guard, "_MAX_LIFECYCLE_SCAN_REMOTE_READS", 8)
    remote_misses: list[str] = []

    def remote(path: str):
        remote_misses.append(path)

    try:
        for data in (minified, notes, db):
            command = f"cd {tmp_path} && python3 - <<'PY'\nt = open('{data}', encoding='utf-8').read()\nPY"
            assert guard(command, cwd=str(tmp_path), read_remote_script=remote) is False, data.name
        assert not remote_misses  # a mention the body only reads is data: never walked, never read
        unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(f"bash {minified}")
        assert unsafe is True and "budget" in refusal
        unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(f"bash {db}")
        assert unsafe is True and "SQLite" in refusal
    finally:
        conn.close()


def test_mentioned_script_with_lifecycle_command_still_blocks(tmp_path):
    """The lenient path only covers "could not scan": a mentioned script whose text IS a lifecycle
    command is still a positive verdict, and the refusal reason stays empty (it is not a scan failure)."""
    from cron import lifecycle_guard

    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = f"cd {tmp_path} && python3 - <<'PY'\nimport os\nos.system('{script}')\nPY"
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)


def test_curl_request_body_heredoc_is_not_a_lifecycle_verdict(tmp_path):
    """A request body posted from stdin is data: a body that MENTIONS a lifecycle command is not one.

    Every comment and PR body is posted as ``curl -d @- <<'MD'``, so scanning that body as shell
    refused comments that quote the guard's own refusal text.
    """
    command = (
        "curl -sS -X POST https://example.invalid/api -d @- " + "<<'MD'" + "\n"
        "never run systemctl --user restart hermes-gateway from a run; this is a comment body\n"
        "MD"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_tee_payload_heredoc_is_not_a_lifecycle_verdict(tmp_path):
    command = (
        "tee /tmp/comment.md " + "<<'MD'" + "\n"
        "never run systemctl --user restart hermes-gateway from a run; this is a comment body\n"
        "MD"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_data_heredoc_consumed_by_a_shell_is_still_blocked(tmp_path):
    """The heredoc belongs to the shell here, so the same body is a command and still blocks."""
    command = (
        "curl -sS https://example.invalid/x.sh | sh " + "<<'MD'" + "\n"
        "systemctl --user restart hermes-gateway\n"
        "MD"
    )
    assert guard(command, cwd=str(tmp_path)) is True


def test_real_lifecycle_command_still_blocks(tmp_path):
    """The guard's real job is intact: an actual lifecycle command is refused."""
    assert guard("systemctl --user restart hermes-gateway", cwd=str(tmp_path)) is True


def test_scannable_data_file_read_in_a_heredoc_is_not_a_verdict(tmp_path):
    """A ticket dump / note / log the body only READS is data, even when it quotes the lifecycle
    command: ``json.load(open(...))`` is not execution, and the walk's mention read turned the
    file's own text into the verdict."""
    from cron import lifecycle_guard

    data = tmp_path / "p763.json"
    data.write_text(
        '{"note": "never run systemctl --user restart hermes-gateway from a run; this is data"}\n',
        encoding="utf-8",
    )
    command = f"python3 - <<'PY'\nimport json\nprint(sorted(json.load(open('{data}')).keys()))\nPY"
    assert guard(command, cwd=str(tmp_path)) is False
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (False, None)


def test_data_file_named_in_a_written_heredoc_is_not_a_verdict(tmp_path):
    """``cat > note.md <<'MD'`` writes a note; a line that STARTS with a lifecycle script path is
    the shape the walk used to read as a referenced script, but writing a note about a script does
    not run it."""
    note_script = tmp_path / "restart.sh"
    note_script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = (
        f"cat > {tmp_path}/note.md <<'MD'\n"
        f"{note_script} runs `systemctl --user restart hermes-gateway`\n"
        "MD"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_markdown_file_read_by_an_interpreter_is_not_a_verdict(tmp_path):
    """A notes file read with ``Path.read_text()`` is data: its lifecycle-shaped lines are text."""
    notes = tmp_path / "notes.md"
    notes.write_text(
        "| /opt/tool/restart.sh | systemctl --user restart hermes-gateway |\n", encoding="utf-8"
    )
    command = (
        f"python3 - <<'PY'\nfrom pathlib import Path\nprint(Path('{notes}').read_text())\nPY"
    )
    assert guard(command, cwd=str(tmp_path)) is False


def test_mentioned_script_handed_to_subprocess_still_blocks(tmp_path):
    """The mention read is kept for execution hand-offs: a script passed to ``subprocess`` runs."""
    from cron import lifecycle_guard

    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = (
        f"python3 - <<'PY'\nimport subprocess\n"
        f"subprocess.run(['{script}'], check=True)\nPY"
    )
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)


def test_mentioned_script_handed_to_a_shell_c_still_blocks(tmp_path):
    """``sh -c '…'`` inside an interpreter body is a command line, so the script it names runs."""
    from cron import lifecycle_guard

    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = f"python3 - <<'PY'\nimport os\nos.system(\"sh -c '{script}'\")\nPY"
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)


def test_executed_script_is_blocked_even_when_a_data_mention_precedes_it(tmp_path):
    """A body that reads data AND runs a lifecycle script is still refused: the hand-off wins."""
    from cron import lifecycle_guard

    data = tmp_path / "ticket.json"
    data.write_text('{"note": "systemctl --user restart hermes-gateway"}\n', encoding="utf-8")
    script = tmp_path / "restart.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n", encoding="utf-8")
    command = (
        f"python3 - <<'PY'\nimport json, os\n"
        f"print(json.load(open('{data}')))\nos.system('{script}')\nPY"
    )
    assert lifecycle_guard.scan_gateway_lifecycle(command, cwd=str(tmp_path)) == (True, None)


@pytest.mark.parametrize("shape", ["oversized", "sqlite"])
def test_unscannable_own_cron_script_raises_named_refusal(tmp_path, shape):
    """A cron job whose OWN script is oversized or a live SQLite database fails closed with the
    named "could not scan" refusal, never the misattributed "contains a gateway lifecycle command"."""
    from cron.lifecycle_guard import GatewayLifecycleBlocked, check_gateway_lifecycle
    from hermes_cli.sqlite_safe_read import connect_tracked

    if shape == "oversized":
        script, conn = _big_file(tmp_path), None
    else:
        script = tmp_path / "state.db"
        conn = connect_tracked(script)
    try:
        with pytest.raises(GatewayLifecycleBlocked) as excinfo:
            check_gateway_lifecycle("run it", str(script))
    finally:
        if conn is not None:
            conn.close()
    message = str(excinfo.value)
    assert "could not scan" in message and str(script) in message
    assert "contains a gateway lifecycle command" not in message

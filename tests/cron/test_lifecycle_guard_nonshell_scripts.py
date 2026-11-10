"""A referenced script that is not a POSIX shell script is scanned, not walked.

The referenced-script walk models shell semantics: a path-shaped token in a shell script is a
command. A normal build command reaches a *node* shim (``node_modules/.bin/tsc``,
``node_modules/.bin/<cli>``) whose own tokens are module resolution — ``require("./_tsc.js")``
resolves the 6 MB TypeScript compiler bundle, a bundled CLI resolves ~100 sibling chunks — and
following them ran the walk out of budget and REFUSED the build for a lifecycle command it never
found (``the scan budget was exhausted (remote reads at depth 3)`` / ``_tsc.js is larger than the
scan cap``).

The fix keeps the walk's real job: a non-shell script's *text* is still scanned, so a literal
``hermes gateway restart`` in it is still caught. Only the recursion into its path-shaped tokens is
skipped, and an oversized non-shell script is skipped like a compiled binary instead of failing
closed. Oversized files that a shell could actually run (shell shebang, or no shebang at all)
still fail closed.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from cron import lifecycle_guard

guard = lifecycle_guard.contains_gateway_lifecycle_command_or_referenced_script

_OVERSIZED = 1024 * 1024 + 1


def _write(path: Path, text: str, *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


# --- shebang classification -------------------------------------------------


def test_shebang_program_reads_both_spellings():
    assert lifecycle_guard._shebang_program("#!/usr/bin/env node\nx") == "node"
    # `env -S` carries the interpreter's own arguments.
    assert lifecycle_guard._shebang_program("#!/usr/bin/env -S bash -e\nx") == "bash"
    assert lifecycle_guard._shebang_program("#!/bin/sh\nx") == "sh"
    assert lifecycle_guard._shebang_program("#!/usr/bin/python3\nx") == "python3"
    assert lifecycle_guard._shebang_program("echo hello\n") is None
    assert lifecycle_guard._shebang_program("\ufeff#!/usr/bin/env node\nx") == "node"


def test_only_shell_interpreter_scripts_are_walked():
    assert lifecycle_guard._is_posix_shell_script("#!/bin/bash\necho x\n") is True
    assert lifecycle_guard._is_posix_shell_script("echo x\n") is True  # handed to `bash script`
    assert lifecycle_guard._is_posix_shell_script("#!/usr/bin/env node\nrequire('x')\n") is False
    assert lifecycle_guard._is_posix_shell_script("#!/usr/bin/env python3\nprint(1)\n") is False


# --- the reported build shapes are allowed ----------------------------------


def test_node_shim_reaching_an_oversized_bundle_is_allowed(tmp_path):
    """`node_modules/.bin/tsc` -> `lib/tsc.js` -> 6 MB `lib/_tsc.js`."""
    bundle = _write(tmp_path / "lib" / "_tsc.js", '#!/usr/bin/env node\n' + "x" * _OVERSIZED)
    assert bundle.stat().st_size > 1024 * 1024
    _write(tmp_path / "lib" / "tsc.js", 'module.exports = require("./_tsc.js");\n')
    shim = _write(tmp_path / "bin" / "tsc", "#!/usr/bin/env node\nrequire('../lib/tsc.js')\n",
                  executable=True)

    assert guard(f"{shlex.quote(str(shim))} --version") is False


def test_oversized_node_script_executed_directly_is_skipped(tmp_path):
    """`node_modules/.bin/<cli>` is itself one multi-megabyte bundle."""
    bundle = _write(tmp_path / "cli.js", "#!/usr/bin/env node\n" + "x" * _OVERSIZED, executable=True)
    assert guard(shlex.quote(str(bundle))) is False


def test_node_script_path_tokens_do_not_consume_remote_reads(tmp_path):
    """The `remote reads at depth N` refusal: a JS file with many `require("./…")` tokens."""
    shim = _write(
        tmp_path / "cli.js",
        "#!/usr/bin/env node\n" + "\n".join(f'require("./chunk{i}.js");' for i in range(100)) + "\n",
        executable=True,
    )
    reads: list[str] = []

    def remote(path: str) -> str | None:
        reads.append(path)
        return None

    assert guard(shlex.quote(str(shim)), read_remote_script=remote) is False
    assert reads == []


def test_nested_node_chunks_are_not_walked_either(tmp_path):
    """shim -> lib/cli.js -> dist/cli.js with 100 chunks (the depth-3 shape)."""
    _write(tmp_path / "lib" / "cli.js", 'module.exports = require("./dist/cli.js");\n')
    _write(
        tmp_path / "lib" / "dist" / "cli.js",
        "\n".join(f'require("./chunk{i}.js");' for i in range(100)) + "\n",
    )
    shim = _write(tmp_path / "bin" / "cli", "#!/usr/bin/env node\nrequire('../lib/cli.js')\n",
                  executable=True)
    reads: list[str] = []
    assert guard(shlex.quote(str(shim)), read_remote_script=lambda p: reads.append(p)) is False
    assert reads == []


# --- the guard's real job survives ------------------------------------------


def test_a_lifecycle_command_inside_a_node_script_is_still_blocked(tmp_path):
    shim = _write(
        tmp_path / "restart.js",
        '#!/usr/bin/env node\nrequire("child_process").execSync("hermes gateway restart");\n',
        executable=True,
    )
    unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(shlex.quote(str(shim)))
    assert unsafe is True
    assert refusal is None  # a real lifecycle verdict, not a scan refusal


def test_an_oversized_shell_script_still_fails_closed(tmp_path):
    script = _write(tmp_path / "big.sh", "#!/bin/bash\n" + "# pad\n" * (1024 * 1024 // 6 + 1),
                    executable=True)
    assert script.stat().st_size > 1024 * 1024
    unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(shlex.quote(str(script)))
    assert unsafe is True
    assert refusal is not None and "larger than the scan cap" in refusal


def test_an_oversized_script_without_a_shebang_still_fails_closed(tmp_path):
    """No shebang: nothing vouches for the interpreter, so it stays fail-closed."""
    script = _write(tmp_path / "big", "echo hi\n" + "x" * _OVERSIZED, executable=True)
    unsafe, refusal = lifecycle_guard.scan_gateway_lifecycle(shlex.quote(str(script)))
    assert unsafe is True
    assert refusal is not None and "larger than the scan cap" in refusal


def test_a_shell_script_that_runs_a_dangerous_script_is_still_blocked(tmp_path):
    evil = _write(tmp_path / "evil.sh", "#!/bin/bash\nhermes gateway restart\n")
    hub = _write(tmp_path / "hub.sh", f"#!/bin/bash\nbash {shlex.quote(str(evil))}\n",
                 executable=True)
    assert guard(shlex.quote(str(hub))) is True

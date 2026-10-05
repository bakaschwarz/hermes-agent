"""Optional packages the default install carries, and the user's opt-outs.

A package the ``source`` rules of pm/selection.json select joins the
installers' PM stage, a bare ``hermes pm install`` and ``hermes update`` on
every target it builds for. The user can decline one (``install.sh --skip-browser`` /
``--skip-computer-use``, ``install.ps1 -SkipBrowser`` / ``-SkipComputerUse``,
``hermes pm install --without NAME``). The choice
is recorded per installation beside PM's other install state, so a later
update or bare install never re-adds it. An explicit
``hermes pm install NAME`` clears it.

Stdlib-only apart from PM's own modules: the installers run this before any
application dependency exists.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

DECLINED_FILENAME = "declined-packages.json"


def declined_path(project_root: Path | None = None) -> Path:
    from pm.environments import install_state_dir
    from pm.paths import repo_root

    return install_state_dir(repo_root() if project_root is None else Path(project_root)) / DECLINED_FILENAME


def declined(project_root: Path | None = None) -> frozenset[str]:
    try:
        data = json.loads(declined_path(project_root).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, ValueError) as exc:
        # Silently treating a damaged record as "nothing declined" would
        # re-add a package the user explicitly refused.
        from pm.package import InstallError

        raise InstallError("defaults", f"cannot read {declined_path(project_root)}: {exc}",
                           "fix or delete the file, then retry") from exc
    names = data.get("declined") if isinstance(data, dict) else None
    return frozenset(name for name in names or () if isinstance(name, str))


def record_declined(*, add: Iterable[str] = (), remove: Iterable[str] = (),
                    project_root: Path | None = None) -> frozenset[str]:
    """Update the recorded opt-outs; returns the new set. Writes only on change."""
    from pm.filesystem import durable_write_bytes

    current = declined(project_root)
    updated = (current | set(add)) - set(remove)
    if updated != current:
        body = json.dumps({"schema": 1, "declined": sorted(updated)}, indent=2) + "\n"
        durable_write_bytes(declined_path(project_root), body.encode("utf-8"))
    return frozenset(updated)


def default_package_names() -> list[str]:
    """Every package that may be declined: the source defaults, any target."""
    from pm.selection import select
    from pm.store import ALL_TARGETS

    return sorted({name for target in ALL_TARGETS for name in select("source", target).tools})


def default_packages(names: list[str], *, target: str | None = None,
                     declined_names: frozenset[str] | None = None) -> list[str]:
    """The optional defaults among ``names`` this install should carry.

    Excludes targets the package has no build for and packages the user
    declined. ``names`` is the lockfile's package list.
    """
    from pm.selection import select
    from pm.store import current_target

    target = current_target() if target is None else target
    refused = declined() if declined_names is None else declined_names
    # Lockfile/definition skew mid-update: a selected name the lock lacks is skipped.
    return [name for name in select("source", target).tools if name in names and name not in refused]

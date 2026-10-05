"""Default selection per install kind and target, read from pm/selection.json.

Stdlib-only apart from PM's own modules: the installers run this before any
application dependency exists. pm/selection.schema.json owns the file's shape
(checked by the test suite); this module checks the names the schema cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

INSTALL_KINDS = ("source", "bundle", "docker")


@dataclass(frozen=True)
class Selection:
    tools: tuple[str, ...]
    extras: tuple[str, ...]


def selection_path() -> Path:
    return Path(__file__).resolve().parent / "selection.json"


def select(install: str, target: str, *, path: Path | None = None,
           repo_dir: Path | None = None) -> Selection:
    """The optional tools and extras an ``install`` kind carries on ``target``."""
    from pm.features import declared_extras, opt_in_extras
    from pm.paths import repo_root
    from pm.registry import all_packages, get_package

    if install not in INSTALL_KINDS:
        raise ValueError(f"unknown install kind {install!r}; expected one of {', '.join(INSTALL_KINDS)}")
    repo = repo_root() if repo_dir is None else repo_dir
    every_tool = [name for name in all_packages()
                  if get_package(name).optional and not get_package(name).internal]
    declared = declared_extras(repo)
    every_extra = sorted(set(declared) - set(opt_in_extras(repo)))
    tools: dict[str, None] = {}
    extras: dict[str, None] = {}
    for rule in _rules(selection_path() if path is None else path):
        if install in rule["install"] and any(fnmatchcase(target, glob) for glob in rule["targets"]):
            _apply(tools, rule.get("tools", []), every_tool, "tool")
            _apply(extras, rule.get("extras", []), declared, "extra", star=every_extra)
    available = tuple(name for name in tools if get_package(name).missing_reason(target) is None)
    return Selection(tools=available, extras=tuple(extras))


def _rules(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError(f"{path}: unsupported schema {data.get('schema')!r}")
    return data["rules"]


def _apply(selected: dict[str, None], entries: list[str], known: list[str], kind: str,
           *, star: list[str] | None = None) -> None:
    for entry in entries:
        if entry == "*":
            selected.update(dict.fromkeys(known if star is None else star))
            continue
        name = entry.removeprefix("-")
        if name not in known:
            raise ValueError(f"selection names unknown or non-optional {kind} {name!r}")
        if entry.startswith("-"):
            selected.pop(name, None)
        else:
            selected[name] = None

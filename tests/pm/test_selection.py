"""pm/selection.json: what each install kind selects by default."""

import json

import pytest

from pm.selection import INSTALL_KINDS, select, selection_path
from pm.store import ALL_TARGETS


def test_shipped_selection_matches_its_schema_and_resolves_for_every_install():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(selection_path().with_name("selection.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(json.loads(selection_path().read_text(encoding="utf-8")))
    for install in INSTALL_KINDS:
        for target in ALL_TARGETS:
            select(install, target)  # every name exists and is optional


def test_rules_apply_in_order_by_install_and_target_glob(tmp_path):
    path = tmp_path / "selection.json"
    path.write_text(json.dumps({"schema": 1, "rules": [
        {"install": ["bundle"], "targets": ["*"], "tools": ["*", "-whispercpp-cpu"], "extras": ["*", "-web"],
         "why": "everything"},
        {"install": ["bundle"], "targets": ["win32-*"], "tools": ["whispercpp-cpu"], "why": "windows"},
        {"install": ["source"], "targets": ["*"], "tools": ["gh"], "extras": ["kittentts"], "why": "source"},
    ]}), encoding="utf-8")

    windows, linux = select("bundle", "win32-arm64", path=path), select("bundle", "linux-x64", path=path)
    assert "whispercpp-cpu" in windows.tools and "whispercpp-cpu" not in linux.tools
    assert "llamacpp-metal" not in linux.tools  # no build for the target
    assert "web" not in linux.extras and "acp" in linux.extras
    assert "kittentts" not in linux.extras  # "*" leaves opt-in extras out; naming one selects it
    assert select("source", "linux-x64", path=path) == type(linux)(tools=("gh",), extras=("kittentts",))
    assert select("docker", "linux-x64", path=path).tools == ()

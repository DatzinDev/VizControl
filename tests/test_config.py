from pathlib import Path

import pytest

from vizcontrol.config import Config, load_config


def test_pyproject_section_and_overrides(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.vizcontrol]\nlevel = "state"\ninclude = ["app.*"]\n'
        "[tool.vizcontrol.limits]\nmax_depth = 7\n"
        '[tool.vizcontrol.stages]\nAPI = ["app.api.*"]\n'
    )
    sub = tmp_path / "pkg"
    sub.mkdir()
    cfg = load_config(cwd=sub, level="calls")
    assert cfg.level == "calls"  # override wins
    assert cfg.include == ("app.*",)
    assert cfg.limits.max_depth == 7
    assert cfg.limits.max_calls == Config().limits.max_calls
    assert cfg.stages == {"API": ("app.api.*",)}


def test_unknown_key_is_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.vizcontrol.values]\nmax_dept = 3\n")
    with pytest.raises(ValueError, match="values.'max_dept'"):
        load_config(cwd=tmp_path)


def test_bad_level():
    with pytest.raises(ValueError):
        Config(level="everything")


def test_documented_example_is_valid(tmp_path):
    doc = (Path(__file__).parents[1] / "docs" / "components" / "cli.md").read_text()
    example = doc.split("```toml\n", 1)[1].split("```", 1)[0]
    (tmp_path / "pyproject.toml").write_text(example)
    cfg = load_config(cwd=tmp_path)
    assert cfg.values.max_nodes == 100 and cfg.redact.names == ("ssn", "iban")

"""Capture configuration: defaults < [tool.vizcontrol] in pyproject.toml < --config file < explicit overrides."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEVELS = ("calls", "values", "state")


@dataclass(frozen=True)
class Limits:
    max_calls: int = 1_000_000
    max_depth: int = 200
    max_values_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True)
class Values:
    max_depth: int = 3
    max_items: int = 20
    max_str: int = 200
    max_value_bytes: int = 64 * 1024
    max_nodes: int = 100  # per encoded value: bounds the cost of every binding
    max_per_function: int = 1000  # calls of one function with values recorded; later ones keep structure only
    safe_repr_types: tuple[str, ...] = ()
    serializers: dict[str, str] = field(default_factory=dict)  # "pkg.Type" -> "pkg.module:function"


@dataclass(frozen=True)
class Redact:
    names: tuple[str, ...] = ()  # extra regexes for binding/key/attribute names
    values: tuple[str, ...] = ()  # regexes over string content
    defaults: bool = True
    hook: str | None = None  # "pkg.module:function" taking (name, obj) -> bool


@dataclass(frozen=True)
class Config:
    level: str = "values"
    include: tuple[str, ...] = ()  # module globs or paths; empty = project root
    exclude: tuple[str, ...] = ()
    opaque_calls: bool = True
    threads: bool = True
    store_source: bool = True
    out_dir: str = ".vizcontrol/runs"
    stage_depth: int = 2
    stages: dict[str, tuple[str, ...]] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    values: Values = field(default_factory=Values)
    redact: Redact = field(default_factory=Redact)

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {self.level!r}")


def _build(cls, data: dict[str, Any], where: str):
    """Build a (nested) config dataclass from a TOML table, rejecting unknown keys."""
    known = {f.name: f for f in dataclasses.fields(cls)}
    kwargs = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key {where}{key!r}")
        sub = known[key].default_factory if known[key].default_factory is not dataclasses.MISSING else None
        if dataclasses.is_dataclass(sub):
            kwargs[key] = _build(sub, value, f"{where}{key}.")
        elif isinstance(value, list):
            kwargs[key] = tuple(value)
        elif key == "stages":
            kwargs[key] = {k: tuple(v) for k, v in value.items()}
        else:
            kwargs[key] = value
    return cls(**kwargs)


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def find_pyproject(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / "pyproject.toml").is_file():
            return d / "pyproject.toml"
    return None


def load_config(config_file: str | Path | None = None, cwd: str | Path | None = None, **overrides) -> Config:
    """Load the effective config. `overrides` are top-level keys (nested sections as dicts)."""
    data: dict = {}
    pyproject = find_pyproject(Path(cwd or Path.cwd()).resolve())
    if pyproject:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f).get("tool", {}).get("vizcontrol", {})
    if config_file:
        with open(config_file, "rb") as f:
            loaded = tomllib.load(f)
        data = _merge(data, loaded.get("tool", {}).get("vizcontrol", loaded))
    data = _merge(data, {k: v for k, v in overrides.items() if v is not None})
    return _build(Config, data, "")

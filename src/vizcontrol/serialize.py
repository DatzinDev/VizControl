"""Side-effect-free, bounded, redacted encoding of live objects. See docs/components/serialization.md."""

from __future__ import annotations

import base64
import datetime
import decimal
import enum
import fractions
import importlib
import io
import itertools
import json
import math
import pathlib
import re
import types
import uuid
import weakref
from typing import Any, Callable

from vizcontrol.config import Config

DEFAULT_REDACT_NAMES = (
    r"pass(word|wd|phrase)?", r"secret", r"token", r"api[_-]?key", r"auth(orization)?", r"cookie",
    r"private[_-]?key", r"credential",
)

# Types whose repr is implemented by the stdlib and has no side effects.
SAFE_REPR = {
    datetime.date, datetime.datetime, datetime.time, datetime.timedelta, datetime.timezone, decimal.Decimal,
    uuid.UUID, fractions.Fraction, complex, pathlib.PurePosixPath, pathlib.PureWindowsPath, pathlib.PosixPath,
    pathlib.WindowsPath, range, slice,
}

# Never looked into: reading them is meaningless or could have effects.
OPAQUE = (
    types.FunctionType, types.BuiltinFunctionType, types.MethodType, types.ModuleType, type, types.GeneratorType,
    types.CoroutineType, types.AsyncGeneratorType, types.FrameType, types.CodeType, types.TracebackType,
    types.MethodDescriptorType, types.WrapperDescriptorType, types.MethodWrapperType, weakref.ref, io.IOBase,
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_registry: dict[type, Callable[[Any], Any]] = {}


def register(type_: type, fn: Callable[[Any], Any]) -> None:
    """Register a serializer for `type_`. `fn(obj)` must return something JSON-encodable."""
    _registry[type_] = fn


def load_callable(spec: str) -> Callable:
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def type_name(t: type) -> str:
    module = t.__module__
    return t.__qualname__ if module == "builtins" else f"{module}.{t.__qualname__}"


class Encoded:
    __slots__ = ("data", "object_id", "type", "truncated", "redacted")

    def __init__(self, data, object_id, type_, truncated, redacted):
        self.data, self.object_id, self.type, self.truncated, self.redacted = data, object_id, type_, truncated, redacted


class Encoder:
    def __init__(self, config: Config):
        v, r = config.values, config.redact
        self.max_depth, self.max_items, self.max_str, self.max_bytes = v.max_depth, v.max_items, v.max_str, v.max_value_bytes
        self.safe_names = set(v.safe_repr_types)
        names = (DEFAULT_REDACT_NAMES if r.defaults else ()) + tuple(r.names)
        # Rules match whole name segments: "access_token" and "X-Api-Key" match, "author" and "tokens" don't.
        alts = "|".join(f"(?:{n})" for n in names)
        self._name_re = re.compile(f"(?:^|[_\\-. ])(?:{alts})(?:$|[_\\-. ])", re.I) if names else None
        self._value_re = re.compile("|".join(f"(?:{n})" for n in r.values)) if r.values else None
        self._hook = load_callable(r.hook) if r.hook else None
        self._by_name = {name: load_callable(spec) for name, spec in v.serializers.items()}
        self._oids: dict[int, tuple[int, Any]] = {}
        self._next_oid = itertools.count(1).__next__

    # -- identity ---------------------------------------------------------------------------------------------
    def object_id(self, obj) -> int:
        """Per-run identity. Weak-referenceable objects are tracked exactly; others by (id, type)."""
        key = id(obj)
        hit = self._oids.get(key)
        if hit is not None:
            oid, ref = hit
            if (ref() is obj) if isinstance(ref, weakref.ref) else ref is type(obj):
                return oid
        try:
            ref = weakref.ref(obj)
        except TypeError:
            ref = type(obj)  # ponytail: same-type id reuse after free is undetectable; analysis marks it inferred
        oid = self._next_oid()
        self._oids[key] = (oid, ref)
        return oid

    # -- redaction --------------------------------------------------------------------------------------------
    def redact_name(self, name: str | None, obj=None) -> bool:
        if name is None:
            return False
        if self._name_re is not None and self._name_re.search(_CAMEL.sub("_", name)):
            return True
        return bool(self._hook and self._hook(name, obj))

    def scrub(self, text: str) -> tuple[str, bool]:
        if self._value_re is None:
            return text, False
        out = self._value_re.sub("‹redacted›", text)
        return out, out != text

    # -- encoding ---------------------------------------------------------------------------------------------
    def encode(self, obj, name: str | None = None) -> Encoded:
        self._truncated = self._redacted = False
        seen: dict[int, int] = {}
        try:
            data = self._enc(obj, 0, name, seen)
        except Exception as e:  # a broken object must never break the traced program
            data = {"t": "opaque", "type": type_name(type(obj)), "error": type(e).__name__}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text) > self.max_bytes:
            data = {"t": "trunc", "type": type_name(type(obj))}
            text = json.dumps(data)
            self._truncated = True
        oid = data.get("oid") if isinstance(data, dict) else None
        return Encoded(text, oid, type_name(type(obj)), self._truncated, self._redacted)

    def _enc(self, obj, depth: int, name: str | None, seen: dict[int, int]) -> dict:
        if name is not None and self.redact_name(name, obj):
            self._redacted = True
            return {"t": "redacted"}
        t = type(obj)
        if obj is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": obj}
        if t is int:
            return {"t": "int", "v": obj if abs(obj) < 2**53 else str(obj)}
        if t is float:
            return {"t": "float", "v": obj if math.isfinite(obj) else str(obj)}
        if isinstance(obj, str):
            return self._str(str.__getitem__(obj, slice(None)) if t is not str else obj)
        if isinstance(obj, (bytes, bytearray)):
            n = len(obj)
            out = {"t": "bytes", "v": base64.b64encode(bytes(obj[: self.max_str])).decode()}
            if t is bytearray:
                out["oid"] = self.object_id(obj)
            if n > self.max_str:
                out["len"] = n
                self._truncated = True
            return out
        fn = _registry.get(t) or (self._by_name.get(type_name(t)) if self._by_name else None)
        if fn is not None:
            return {"t": "custom", "type": type_name(t), "v": fn(obj)}
        if t in SAFE_REPR or (self.safe_names and type_name(t) in self.safe_names):
            return {"t": "repr", "type": type_name(t), "v": repr(obj)}
        if isinstance(obj, enum.Enum):
            return {"t": "repr", "type": type_name(t), "v": f"{t.__qualname__}.{object.__getattribute__(obj, '_name_')}"}
        if isinstance(obj, OPAQUE):
            return self._opaque(obj)

        mutable = not isinstance(obj, (tuple, frozenset))
        if mutable:
            oid = self.object_id(obj)
            if id(obj) in seen:
                return {"t": "ref", "oid": oid}
            seen[id(obj)] = oid
        if depth >= self.max_depth:
            self._truncated = True
            return {"t": "trunc", "type": type_name(t), **({"oid": oid} if mutable else {})}

        if isinstance(obj, dict):
            out = {"t": "dict", "items": [
                [self._enc(k, depth + 1, None, seen), self._enc(v, depth + 1, k if isinstance(k, str) else None, seen)]
                for k, v in itertools.islice(dict.items(obj), self.max_items)]}
            n = dict.__len__(obj)
        elif isinstance(obj, (list, tuple, set, frozenset)):
            base = list if isinstance(obj, list) else tuple if isinstance(obj, tuple) else set if isinstance(obj, set) else frozenset
            out = {"t": base.__name__, "items": [
                self._enc(v, depth + 1, None, seen) for v in itertools.islice(base.__iter__(obj), self.max_items)]}
            n = base.__len__(obj)
        else:
            return self._object(obj, t, depth, seen)
        if t not in (dict, list, tuple, set, frozenset):
            out["type"] = type_name(t)
        if mutable:
            out["oid"] = oid
        if n > self.max_items:
            out["len"] = n
            self._truncated = True
        return out

    def _str(self, s: str) -> dict:
        out = {"t": "str", "v": s[: self.max_str]}
        if len(s) > self.max_str:
            out["len"] = len(s)
            self._truncated = True
        out["v"], hit = self.scrub(out["v"])
        self._redacted |= hit
        return out

    def _opaque(self, obj) -> dict:
        out = {"t": "opaque", "type": type_name(type(obj))}
        if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, type)):
            out["label"] = f"{obj.__module__}.{obj.__qualname__}"
        elif isinstance(obj, types.ModuleType):
            out["label"] = obj.__name__
        try:
            out["oid"] = self.object_id(obj)
        except TypeError:
            pass
        return out

    def _object(self, obj, t: type, depth: int, seen: dict[int, int]) -> dict:
        """Read state from __dict__ and __slots__ only: no properties, __getattr__ or __repr__ are triggered."""
        attrs: dict[str, Any] = {}
        if isinstance(obj, BaseException):
            attrs["args"] = BaseException.args.__get__(obj)
        try:
            d = object.__getattribute__(obj, "__dict__")
        except AttributeError:
            d = None
        if isinstance(d, dict):
            for k, v in itertools.islice(dict.items(d), self.max_items):
                attrs[k] = v
        has_slots = False
        for cls in t.__mro__:
            slots = cls.__dict__.get("__slots__", ())
            for slot in (slots,) if isinstance(slots, str) else slots:
                has_slots = True
                desc = cls.__dict__.get(slot)
                if isinstance(desc, types.MemberDescriptorType) and slot not in attrs:
                    try:
                        attrs[slot] = desc.__get__(obj, t)
                    except AttributeError:
                        pass
        if d is None and not has_slots and not attrs:
            return {"t": "opaque", "type": type_name(t), "oid": seen[id(obj)]}
        return {"t": "obj", "type": type_name(t), "oid": seen[id(obj)],
                "attrs": {k: self._enc(v, depth + 1, k, seen) for k, v in attrs.items()}}


def preview(data: dict, limit: int = 120) -> str:
    """One-line human preview of an encoded value tree."""
    def p(d, budget):
        t = d.get("t")
        if t in ("none", "bool", "int", "float"):
            return "None" if t == "none" else str(d["v"])
        if t == "str":
            return repr(d["v"]) + ("…" if "len" in d else "")
        if t in ("repr", "custom"):
            return str(d["v"])
        if t in ("list", "tuple", "set", "frozenset"):
            inner = ", ".join(p(i, budget // 2) for i in d["items"][:5])
            more = ", …" if len(d["items"]) > 5 or "len" in d else ""
            return {"list": "[%s]", "tuple": "(%s)"}.get(t, "{%s}") % (inner + more)
        if t == "dict":
            inner = ", ".join(f"{p(k, 20)}: {p(v, budget // 2)}" for k, v in d["items"][:5])
            return "{" + inner + (", …" if len(d["items"]) > 5 or "len" in d else "") + "}"
        if t == "obj":
            name = d["type"].rsplit(".", 1)[-1]
            inner = ", ".join(f"{k}: {p(v, budget // 2)}" for k, v in list(d["attrs"].items())[:4])
            return f"{name}{{{inner}{', …' if len(d['attrs']) > 4 else ''}}}"
        if t == "redacted":
            return "‹redacted›"
        if t == "ref":
            return f"#{d['oid']}"
        return f"<{d.get('label') or d.get('type', t)}>"
    s = p(data, limit)
    return s if len(s) <= limit else s[: limit - 1] + "…"

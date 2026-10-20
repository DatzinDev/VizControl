# Serialization

`src/vizcontrol/serialize.py`

## Responsibility

Convert live Python objects into bounded, redacted, JSON-encodable [value trees](../03-trace-format.md#value-encoding)
without side effects, and give every mutable object a per-run identity so data flow can be followed.

## Requirements covered

FR-VAL-1..5, FR-CAP-4/5, NFR-SAFE-1/2, NFR-OBS.

## Interface

```python
class Encoder:
    def __init__(self, config: Config): ...
    def encode(self, obj: object, name: str | None = None) -> Encoded
        # Encoded = (data: dict, object_id: int | None, truncated: bool, redacted: bool)

def register(type_: type, fn: Callable[[object], object]) -> None   # public, FR-VAL-5
```

`name` is the binding name (argument, local, attribute or dict key) and is used by redaction rules.

## Encoding rules (FR-VAL-1/2)

The encoder walks the object breadth-limited and depth-limited and **never calls user-defined code**:

| Object | How it's read |
|---|---|
| `None`, `bool`, `int`, `float`, `complex` | Directly. |
| `str`, `bytes`, `bytearray` | Sliced to `max_str` chars/bytes; original length kept. |
| `list`, `tuple`, `set`, `frozenset`, `dict` (and subclasses) | Through the **base type's** methods (`list.__iter__`, `dict.items`), never the subclass overrides. At most `max_items` entries. |
| Allowlisted stdlib types (`datetime`, `date`, `Decimal`, `UUID`, `Path`, `Enum`, `re.Pattern`, ...) | Their `repr` (known to be side-effect-free). |
| Types with a serializer | Through the type's MRO, first match: a `__vizcontrol__()` method on the class, `register(type, fn)`, the config's `serializers`, then the built-in views. The result is encoded like any value (bounded, redacted) and marked `view`: a dict becomes the object's fields and keeps its oid, so it can be followed and diffed; anything else stands for its value. A view adds no nesting level. It runs inside the monitoring callback, where CPython reports no events, so it is never traced; an exception produces `opaque`. |
| Other objects | Attributes from `object.__getattribute__(obj, "__dict__")` plus `__slots__` read through the member descriptors, except dunder slots (`__pydantic_fields_set__` and similar machinery). Properties, `__getattr__` and `__repr__` are never triggered. Dataclasses and pydantic models come out as their fields this way. |
| Iterators, generators, files, sockets, locks, modules, functions, classes | `opaque` with type and, where cheap and safe, a label (function qualname, module name, file name). Never consumed. |

Limits: `max_depth` (default 3), `max_items` (default 20), `max_str` (default 200), `max_nodes` (default 100
container/object nodes per value) and `max_value_bytes` (default 64 KiB per encoded value). The encoder stops at
the limit and marks the node (`trunc`, `len`). Within one encoded value, an object met twice becomes a `ref`
node, so cycles terminate. How each type is encoded is decided once per type and cached.

## Object identity (FR-VAL-3)

`id()` alone is unsafe because ids are reused after an object is freed. The registry:

- Weak-referenceable objects: `id(obj) → (oid, weakref)`. If the weakref is dead, the entry is stale and a new
  oid is issued. Identity is **observed**.
- Non-weak-referenceable objects (`list`, `dict`, `set`, ...): `id(obj) → (oid, type)`. A type change issues a new
  oid. A same-type reuse after free cannot be detected, so links built on these oids are marked **inferred**
  (NFR-OBS).
- Immutable scalars and strings get no oid. Their "identity" is their value.

The registry holds no strong references (NFR-SAFE-1) and is dropped at the end of the run.

## Redaction (FR-VAL-4, NFR-SAFE-2)

Applied during encoding, in the traced process, before anything is queued for writing:

- **Name rules**: case-insensitive regexes matched against whole segments of the binding name, dict keys and
  attribute names (segments split on `_ - . space` and camelCase). `access_token`, `X-Api-Key` and `accessToken`
  match `token`/`api[_-]?key`, while `author` and `tokens` don't. Defaults: `pass(word|wd|phrase)?`, `secret`,
  `token`, `api[_-]?key`, `auth(orization)?`, `cookie`, `private[_-]?key`, `credential`. A match replaces the
  value with `{"t":"redacted"}`.
- **Value rules** (opt-in): regexes over string content, e.g. bearer tokens or card numbers. A match replaces only
  the string.
- **Hook**: `redact = "pkg.module:function"` taking `(name, obj) -> bool` for anything rules can't express.
- Exception messages and `argv` go through the value rules too.

Redaction can be strengthened but not disabled by flags. Clearing the default rules requires an explicit
`redact_defaults = false` in config.

## Config knobs

`max_depth`, `max_items`, `max_str`, `max_nodes`, `max_value_bytes`, `safe_repr_types`, `redact_names`, `redact_values`,
`redact_defaults`, `redact`, `serializers` (dotted paths registered at start).

## Limits and failure modes

- Built-in views, found by type name so nothing is imported: pandas `DataFrame` (shape, column dtypes, first 5 rows),
  `Series`, `Timestamp`, numpy `ndarray` (shape, dtype, first 5 per axis) and scalars (their Python value). They call
  the library's read-only methods (`head`, `dtypes`, `tolist`), unlike the plain object walk.
- Other objects whose state lives in C (DB connections, sockets) are `opaque` unless a serializer is registered.
- Reading `__dict__` of objects mutated concurrently by another thread may see a torn state. This is accepted
  and documented.
- Encoding cost grows with `max_items × max_depth`. The defaults are tuned with the benchmark suite.

## Open questions

- Should `pydantic` / `dataclasses` models get first-class encoders (field order, declared types) in v1?

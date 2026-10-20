import json
from decimal import Decimal

from vizcontrol.config import Config, Redact, Values
from vizcontrol.serialize import Encoder, _registry, preview, register


def enc(obj, name=None, **values):
    e = Encoder(Config(values=Values(**values)))
    r = e.encode(obj, name)
    return json.loads(r.data), r


class Trap:
    """Any user hook being called would blow up the test."""

    def __init__(self):
        self.x = 1

    def __repr__(self):
        raise AssertionError("__repr__ called")

    def __getattr__(self, name):
        raise AssertionError("__getattr__ called")

    @property
    def boom(self):
        raise AssertionError("property called")


class Slotted:
    __slots__ = ("a", "b")

    def __init__(self):
        self.a = 1  # b left unset


class SneakyDict(dict):
    def items(self):
        raise AssertionError("subclass method called")


def test_no_user_code_is_called():
    data, _ = enc(Trap())
    assert data["t"] == "obj" and data["attrs"] == {"x": {"t": "int", "v": 1}}
    data, _ = enc(SneakyDict(a=1))
    assert data["t"] == "dict" and data["type"].endswith("SneakyDict")


def test_slots_cycles_and_identity():
    data, _ = enc(Slotted())
    assert data["attrs"] == {"a": {"t": "int", "v": 1}}
    loop = []
    loop.append(loop)
    data, _ = enc(loop)
    assert data["items"][0] == {"t": "ref", "oid": data["oid"]}
    e = Encoder(Config())
    obj = Trap()
    assert e.encode(obj).object_id == e.encode(obj).object_id
    assert e.encode(Trap()).object_id != e.encode(obj).object_id


def test_limits_are_explicit():
    data, r = enc(list(range(10)), max_items=3)
    assert len(data["items"]) == 3 and data["len"] == 10 and r.truncated
    data, r = enc("x" * 50, max_str=5)
    assert data == {"t": "str", "v": "xxxxx", "len": 50}
    data, r = enc([[[1]]], max_depth=2)
    assert data["items"][0]["items"][0]["t"] == "trunc" and r.truncated


def test_redaction():
    data, r = enc({"password": "hunter2", "author": "ana", "accessToken": "t", "X-Api-Key": "k"})
    by_key = {k["v"]: v for k, v in data["items"]}
    assert by_key["password"] == {"t": "redacted"} and r.redacted
    assert by_key["accessToken"] == {"t": "redacted"} and by_key["X-Api-Key"] == {"t": "redacted"}
    assert by_key["author"] == {"t": "str", "v": "ana"}
    e = Encoder(Config(redact=Redact(values=(r"Bearer \S+",))))
    assert json.loads(e.encode("Authorization: Bearer abc").data)["v"] == "Authorization: ‹redacted›"


def test_safe_repr_custom_and_exceptions():
    assert enc(Decimal("1.5"))[0] == {"t": "repr", "type": "decimal.Decimal", "v": "Decimal('1.5')"}
    register(complex, lambda c: [c.real, c.imag])
    try:
        assert enc(2j)[0]["t"] == "custom"
    finally:
        _registry.pop(complex)
    data, _ = enc(ValueError("bad", 3))
    assert data["attrs"]["args"]["items"][0] == {"t": "str", "v": "bad"}


def test_preview():
    data, _ = enc({"id": 501, "status": "paid", "items": [1, 2, 3]})
    assert preview(data) == "{'id': 501, 'status': 'paid', 'items': [1, 2, 3]}"

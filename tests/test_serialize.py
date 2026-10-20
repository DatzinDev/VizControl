import json
from decimal import Decimal

import pytest

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
        assert enc(2j)[0] == {"t": "list", "items": [{"t": "float", "v": 0.0}, {"t": "float", "v": 2.0}],
                              "type": "complex", "view": True}
    finally:
        _registry.pop(complex)
    data, _ = enc(ValueError("bad", 3))
    assert data["attrs"]["args"]["items"][0] == {"t": "str", "v": "bad"}


def test_preview():
    data, _ = enc({"id": 501, "status": "paid", "items": [1, 2, 3]})
    assert preview(data) == "{'id': 501, 'status': 'paid', 'items': [1, 2, 3]}"


def test_node_budget():
    data, r = enc([[i] for i in range(40)], max_nodes=10, max_items=50)
    assert sum(1 for i in data["items"] if i["t"] == "trunc") == 31 and r.truncated


class Money:
    def __init__(self, cents, currency, password):
        self.cents, self.currency, self.password = cents, currency, password

    def __vizcontrol__(self):
        return {"amount": self.cents / 100, "currency": self.currency, "password": self.password}


class Rich(Money):
    pass


class Broken:
    def __vizcontrol__(self):
        raise RuntimeError("nope")


def test_views_are_objects_with_identity_and_still_redacted():
    m = Rich(1250, "MXN", "hunter2")
    e = Encoder(Config())
    a, b = json.loads(e.encode(m).data), json.loads(e.encode([m, m]).data)
    assert a["t"] == "obj" and a["view"] and a["type"] == "test_serialize.Rich"  # subclasses use the hook too
    assert a["attrs"]["amount"] == {"t": "float", "v": 12.5} and a["attrs"]["password"] == {"t": "redacted"}
    assert b["items"][0]["oid"] == a["oid"] and b["items"][1] == {"t": "ref", "oid": a["oid"]}
    assert preview(a) == "Rich{amount: 12.5, currency: 'MXN', password: ‹redacted›}"
    assert enc(Broken())[0] == {"t": "opaque", "type": "test_serialize.Broken", "error": "RuntimeError"}


def test_library_views():
    import dataclasses

    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pydantic = pytest.importorskip("pydantic")

    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    d, _ = enc(df)
    assert d["type"].endswith("DataFrame") and d["view"] and "oid" in d
    assert d["attrs"]["shape"]["items"] == [{"t": "int", "v": 3}, {"t": "int", "v": 2}]
    assert preview(d["attrs"]["columns"]).startswith("{'id': 'int64', 'name': ")  # object or str by version
    assert enc(pd.Timestamp("2026-01-02"))[0]["v"] == "2026-01-02T00:00:00"
    assert preview(d["attrs"]["head"]["items"][0]) == "{'id': 1, 'name': 'a'}"  # numpy scalars as plain values
    assert enc(np.int64(7))[0] == {"t": "int", "v": 7, "type": "numpy.int64", "view": True}
    arr, _ = enc(np.zeros((100, 100)))
    assert arr["attrs"]["shape"]["items"][0]["v"] == 100 and len(arr["attrs"]["head"]["items"]) == 5

    class User(pydantic.BaseModel):
        name: str
        age: int

    @dataclasses.dataclass(slots=True)
    class Point:
        x: int
        y: int

    assert set(enc(User(name="ana", age=3))[0]["attrs"]) == {"name", "age"}  # no __pydantic_*__ machinery
    assert set(enc(Point(1, 2))[0]["attrs"]) == {"x", "y"}

import pytest

from vizcontrol import analysis as A
from vizcontrol.store import Reader

APP = '''
def price(qty, unit):
    return qty * unit


def discount(total, pct):
    return total - total * pct / 100
'''

TESTS = '''
import pytest
from app import discount, price


@pytest.fixture
def cart():
    return {"qty": 3, "unit": 10}


def test_price(cart):
    assert price(cart["qty"], cart["unit"]) == 30


def test_discount_is_wrong():
    total = price(2, 10)
    assert discount(total, 120) >= 0


def test_crash():
    price(None, 3)


def test_skipped():
    pytest.skip("not today")
'''


@pytest.fixture
def project(pytester):
    pytester.makepyfile(app=APP, test_shop=TESTS)
    return pytester


def runs_by_test(directory):
    out = {}
    for f in sorted(directory.glob("*.vizc")):
        r = Reader(f)
        out[r.run()["target"].split("::")[-1]] = r
    return out


def test_one_run_per_test_with_outcomes(project):
    out = project.path / "traces"
    result = project.runpytest_subprocess("--vizcontrol", "--vizcontrol-dir", str(out))
    result.assert_outcomes(passed=1, failed=2, skipped=1)
    result.stdout.fnmatch_lines(["*4 traced tests (2 failed)*", f"*vizcontrol serve {out}*"])
    runs = runs_by_test(out)
    assert {k: r.run()["outcome"] for k, r in runs.items()} == {
        "test_price": "ok", "test_discount_is_wrong": "assertion_failed", "test_crash": "raised",
        "test_skipped": "skipped"}
    assert all(r.run()["kind"] == "pytest" for r in runs.values())

    # the test is the root and the code under test sits below it; fixtures are not traced by default
    ok = runs["test_price"]
    [root] = ok.children(None)
    assert root["qualname"] == "test_price" and [c["qualname"] for c in ok.children(root["id"])] == ["price"]

    # a failed assert is the failure point, with the values that made it fail
    ep = A.error_path(runs["test_discount_is_wrong"])
    assert ep["exception"]["type"] == "AssertionError" and ep["origin"]["qualname"] == "test_discount_is_wrong"
    assert {b["name"]: b["preview"] for b in ep["locals"]}["total"] == "20"
    assert ep["traceback"][0][2] == "test_discount_is_wrong"  # no pytest/pluggy frames above the test

    crash = A.error_path(runs["test_crash"])
    assert (crash["exception"]["type"], crash["origin"]["qualname"]) == ("TypeError", "price")


def test_fixtures_option_traces_setup(project):
    out = project.path / "traces"
    project.runpytest_subprocess("--vizcontrol", "--vizcontrol-fixtures", "--vizcontrol-dir", str(out),
                                 "-k", "test_price")
    [run] = runs_by_test(out).values()
    assert {c["qualname"] for c in run.children(None)} >= {"cart", "test_price"}


def test_inactive_without_flag(project):
    project.runpytest_subprocess().assert_outcomes(passed=1, failed=2, skipped=1)
    assert not list(project.path.rglob("*.vizc"))

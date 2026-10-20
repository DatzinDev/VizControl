"""Tracing overhead (NFR-PERF-1). Run: python benchmarks/overhead.py

- micro: thousands of tiny traced functions, the worst case (cost is all per-call bookkeeping)
- app:   traced code orchestrating stdlib work (json, re, sorting), closer to a real program
"""

import json
import re
import tempfile
import time
from operator import itemgetter
from pathlib import Path

import vizcontrol


class Node:
    def __init__(self, value):
        self.value = value
        self.children = []


def build(depth, width):
    node = Node(depth)
    if depth:
        node.children = [build(depth - 1, width) for _ in range(width)]
    return node


def total(node):
    return node.value + sum(total(c) for c in node.children)


def micro():
    total(build(7, 4))


def load_rows(n):
    return json.loads(json.dumps([{"id": i, "name": f"item{i}", "price": i * 1.5} for i in range(n)]))


def keep_valid(rows):
    pattern = re.compile(r"item\d+$")
    return [r for r in rows if pattern.match(r["name"])]


def summarize(rows):
    return {"count": len(rows), "total": sum(map(itemgetter("price"), rows)),
            "top": sorted(rows, key=itemgetter("price"))[-5:]}


def app():
    for _ in range(20):
        summarize(keep_valid(load_rows(5000)))


def best(fn, n=3) -> float:
    times = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return min(times)


def main():
    with tempfile.TemporaryDirectory() as d:
        for workload in (micro, app):
            base = best(workload)
            print(f"{workload.__name__:<6} untraced {base * 1000:8.1f} ms")
            for level in ("calls", "values", "state"):
                out = Path(d) / f"{workload.__name__}-{level}.vizc"
                out.unlink(missing_ok=True)
                start = time.perf_counter()
                with vizcontrol.trace(workload.__name__, output=out, level=level) as t:
                    workload()
                elapsed = time.perf_counter() - start
                from vizcontrol.store import Reader
                calls = Reader(t.path).counts()["call"]
                per_call = (elapsed - base) / calls * 1e6
                print(f"       {level:<8} {elapsed * 1000:8.1f} ms {elapsed / base:6.1f}x  "
                      f"{calls:>7} calls  {per_call:5.1f} µs/call")


if __name__ == "__main__":
    main()

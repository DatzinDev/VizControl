"""A small data pipeline in one file: read records, clean them, turn them into sales, summarize by city.

    vizcontrol run examples/pipeline.py          # ok
    vizcontrol run examples/pipeline.py --bad    # one record breaks it
    vizcontrol serve
"""

import csv
import io
import json
import sys
from dataclasses import dataclass

RAW = """id,name,city,amount
1,ana,cdmx,120
2,,gdl,80
3,luis,mty,-5
4,eva,cdmx,200
"""
BAD = RAW + "5,max,qro,lots\n"


@dataclass
class Sale:
    id: int
    name: str
    city: str
    amount: int


def read(text):
    return list(csv.DictReader(io.StringIO(text)))


def is_valid(row):
    return bool(row["name"]) and int(row["amount"]) > 0  # "lots" is not a number


def clean(rows):
    return [r for r in rows if is_valid(r)]


def to_sale(row):
    return Sale(int(row["id"]), row["name"].title(), row["city"].upper(), int(row["amount"]))


def enrich(rows):
    return [to_sale(r) for r in rows]


def summarize(sales):
    by_city = {}
    for s in sales:
        by_city[s.city] = by_city.get(s.city, 0) + s.amount
    return by_city


def main(argv):
    rows = read(BAD if "--bad" in argv else RAW)
    sales = enrich(clean(rows))
    print(json.dumps(summarize(sales)))


if __name__ == "__main__":
    main(sys.argv[1:])

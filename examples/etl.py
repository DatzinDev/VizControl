"""A simple ETL: extract orders from a CSV, transform them (clean, convert to USD), load them into SQLite, report.

    vizcontrol run --serve examples/etl.py          # watch it step by step in Replay
    vizcontrol run --serve examples/etl.py --bad    # one order has a currency the ETL doesn't know
"""

import csv
import io
import sqlite3
import sys

ORDERS = """id,customer,amount,currency
1, Ana ,120.50,MXN
2,Luis,80,USD
3,,15,USD
4,Marta,2500,MXN
"""
BAD = ORDERS + "5,Eva,99,GBP\n"

RATES = {"USD": 1.0, "MXN": 0.055}


def extract(text):
    return list(csv.DictReader(io.StringIO(text)))


def clean(row):
    name = row["customer"].strip()
    if not name:
        return None  # no customer: skipped
    return {"id": int(row["id"]), "customer": name, "amount": float(row["amount"]), "currency": row["currency"]}


def to_usd(order):
    rate = RATES[order["currency"]]  # KeyError for a currency without a rate
    return {**order, "usd": round(order["amount"] * rate, 2)}


def transform(rows):
    orders = []
    for row in rows:
        order = clean(row)
        if order is not None:
            orders.append(to_usd(order))
    return orders


def load(orders, db):
    db.execute("CREATE TABLE orders (id INTEGER, customer TEXT, usd REAL)")
    db.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(o["id"], o["customer"], o["usd"]) for o in orders])
    return len(orders)


def report(db):
    total, count = db.execute("SELECT SUM(usd), COUNT(*) FROM orders").fetchone()
    return {"orders": count, "total_usd": round(total, 2)}


def main(text):
    db = sqlite3.connect(":memory:")
    rows = extract(text)
    orders = transform(rows)
    load(orders, db)
    summary = report(db)
    print(summary)
    return summary


if __name__ == "__main__":
    main(BAD if "--bad" in sys.argv else ORDERS)

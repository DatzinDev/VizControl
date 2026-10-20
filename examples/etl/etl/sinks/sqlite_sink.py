"""Load: keep the orders in SQLite."""

import sqlite3


class OrderRepository:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE orders (id INTEGER, customer TEXT, usd REAL)")

    def save(self, orders):
        self.db.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(o["id"], o["customer"], o["usd"]) for o in orders])
        return orders

    def totals(self):
        return self.db.execute("SELECT customer, SUM(usd) FROM orders GROUP BY customer ORDER BY 2 DESC").fetchall()

"""Extract: read the orders file."""

import csv


def read_orders(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

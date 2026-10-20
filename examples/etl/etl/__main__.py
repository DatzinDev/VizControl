"""python -m etl [orders.csv]: wire the pieces together and run the pipeline (the composition root)."""

import sys

from etl.events import EventBus
from etl.pipeline import Pipeline
from etl.report import Progress, summarize
from etl.sinks.sqlite_sink import OrderRepository
from etl.sources.csv_source import read_orders
from etl.transforms.clean import clean_rows
from etl.transforms.currency import RateTable, make_to_usd

RATES = {"USD": 1.0, "MXN": 0.055, "EUR": 1.08}


def main(path):
    bus = EventBus()
    progress = Progress()
    bus.on("step_done", progress.on_step_done)
    repo = OrderRepository()
    pipeline = Pipeline([read_orders, clean_rows, make_to_usd(RateTable(RATES)), repo.save], bus)
    pipeline.run(path)
    summarize(repo)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/orders.csv")

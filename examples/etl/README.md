# Example: a layered ETL

Orders come in as a CSV, get cleaned and converted to US dollars, are saved in SQLite, and a summary is printed.
The code is split the way a real project would be, so the replay shows the flow between layers:

```
etl/__main__.py              composition root: builds the pieces, wires them together, runs the pipeline
etl/pipeline.py              Pipeline.run(): calls the steps it was given, in order, and emits events
etl/events.py                EventBus: handlers register with on(), emit() calls them
etl/sources/csv_source.py    extract: read_orders()
etl/transforms/clean.py      transform: clean_rows(), with map() and sorted(key=...)
etl/transforms/currency.py   transform: to_usd(), built by make_to_usd() around a RateTable
etl/sinks/sqlite_sink.py     load: OrderRepository.save()
etl/report.py                a progress handler for the bus, and the summary
data/orders.csv              five orders, one without a customer
data/orders_bad.csv          the same plus one in GBP, a currency without a rate
```

## Run it

```
cd examples/etl
vizcontrol run --serve -m etl data/orders.csv        # works
vizcontrol run --serve -m etl data/orders_bad.csv    # fails: KeyError 'GBP'
```

## What to look at

- **Who calls whom, and when.** The run opens on the program's entry, then `main()`; the imports are left out.
  Press play, or step with ← and →. Each module is a box on the graph; the call stack on the right says how the
  program got to the current step.
- **Callbacks.** `Pipeline.run` never names `read_orders`, `clean_rows`, `to_usd` or `save`: it runs `step(data)`
  for each step it was given. Those calls show as `↩` in the transcript, with "via self.steps[1]", and the panel says
  where the function came from (passed to `Pipeline.__init__` by `main`). The same for the bus: `EventBus.emit`
  runs `handler(data)`, and the handler is `Progress.on_step_done`, registered with `EventBus.on`. On the graph,
  callback arrows are dashed. `map(clean_row, rows)` and `sorted(key=order_id)` are callbacks too: the panel
  shows the line that handed the function over.
- **The data.** Click an argument to open it, or watch a variable by name (`row`, `orders`, `currency`) and follow
  its value step by step.
- **The failure.** `orders_bad.csv` opens on the step where `RateTable.rate` raised `KeyError: 'GBP'`, with the
  arguments that got it there (`currency = 'GBP'`) and the path back up through `to_usd`, `Pipeline.run` and `main`
  in red.

# Understanding a project you didn't write

VizControl answers "what does this code do?" by running it and recording what happened. That works the same
for a script, a package, a test suite or a service, and for code you wrote, inherited or had an AI generate.
Nothing here is guessed from the source: every node and arrow is something that ran (or is marked inferred).

## 1. Run it

```
vizcontrol run main.py --its --args          # a script
vizcontrol run -m package.cli serve          # a module
pytest --vizcontrol                          # every test, one run each
```

Runs land in `.vizcontrol/runs/`. A web app gets one run per request with `vizcontrol.web.ASGIMiddleware`
(FastAPI, Starlette) or `WSGIMiddleware` (Flask, Django). Inside your own code, `with vizcontrol.trace("job"):`
records one unit of work (a job, a message); `scoped=True` keeps out what the rest of the process does meanwhile. Serve one directory per project: stage names are decided over the runs served
together.

## 2. Watch it run: Replay

A run opens as a recording. Press play, or step with ← and →: the call graph lights up as the program goes, the
transcript says who called whom with what and what came back, and the call stack shows how it got there. Type a
variable name in Watch to follow its value step by step (arguments, results and the locals where an exception was
raised are recorded; trace with `--level state` to also get each function's locals when it returns). A failed
run opens on the step where it raised. Try it on `examples/etl.py` and `examples/etl.py --bad`.

## 3. Get the shape: Map and Level 1

```
vizcontrol serve
```

- **Map** joins every run: each stage with how many runs went through it. With a test suite this is the map of
  the whole project, one route per test. Pick a run on the left to see its route.
- **Level 1** is one run's stages and the calls between them, the main path in accent. A package is staged by
  module (`app.api → app.services → app.repo`); a project of one or two files by the functions it fans out to
  (`read → clean → enrich → summarize`). `[tool.vizcontrol.stages]` names your own.
- **Copy as Mermaid** (or `vizcontrol diagram run.vizc [--stage NAME]`) puts the diagram in a README or a PR.

## 4. Go down

Double-click a stage (Level 2: modules, classes, functions with call counts and time), click a function (Level 3:
the call tree, its arguments, return value and source), click a value (Level 4: its fields and history).
⌘K searches functions, variables and values. Back and forward walk the levels.

## 5. Follow the data

**Data Flow** follows one object through every call that saw it, with the fields that changed between steps.
When a function builds a new object from it, "Became" (and "Made from", going back) is the next hop. Libraries'
values show their contents: a DataFrame its shape, columns and first rows; a class can define `__vizcontrol__()`.

## 6. When it fails

A failed run opens on the call that raised, the error path in red, with the locals at the raise and their last
changes. Back on Level 1 the failing stage says "stopped here", and the stages a passing run of the same code
reached are drawn in grey as "not reached".

## 7. Compare two runs

**Diff** (or `vizcontrol diff a.vizc b.vizc`) shows where two runs split, which branch decided and the argument
that made the difference, and, when they took the same path, which calls returned something else, in the order
they did.

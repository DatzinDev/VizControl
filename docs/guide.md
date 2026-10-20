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

## 2. Watch it run

```
vizcontrol serve        # or trace with: vizcontrol run --serve main.py
```

A run opens as a recording, where its work begins. Press play, or step with ← and →:

- the **call graph** lights up as the program goes, one box per module, so you see which layer calls which;
- the **transcript** says who called whom with what, and what came back;
- the **call stack** shows how the program got to this step;
- **Watch** follows a variable by name, step by step (arguments, results and the locals where an exception was
  raised are recorded; `--level state` adds each function's locals when it returns).

Indirect calls are marked: when a function runs a step it was given, a handler registered on it, or a key it handed
to `sorted()`, the call shows as `↩ callback` with the line that ran it, where the function was (`via
self.steps[1]`) and where it was first seen. That is how a pipeline, a plugin system or an event bus reads.

## 3. When it fails

A failed run opens on the step where it raised, the path back up in red, the arguments that got there in the panel.
Step back to see what led to it.

## 4. From the terminal

```
vizcontrol diagram run.vizc        # the run's stages as a Mermaid flowchart, for a README or a PR
vizcontrol diff a.vizc b.vizc      # where two runs split, and which calls returned something else
```

Try it all on [`examples/etl`](https://github.com/DatzinDev/VizControl/tree/main/examples/etl).

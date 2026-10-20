# CLI

`src/vizcontrol/cli.py` (`argparse`), console script `vizcontrol`. Also `python -m vizcontrol`.

## Responsibility

The user's entry point. It parses commands, merges configuration and calls [runners](runners.md) and the
[server](server.md). It has no logic of its own beyond that.

## Requirements covered

FR-RUN-1/2/6, NFR-PKG-1.

## Commands

```
vizcontrol run [options] script.py [args...]      # FR-RUN-1
vizcontrol run [options] -m module [args...]      # FR-RUN-2
    -o, --output FILE        .vizc path
    --out-dir DIR            default .vizcontrol/runs
    --level {calls,values,state}
    --include GLOB  --exclude GLOB   (repeatable)
    --open                           serve and open the viewer when the run ends (with `serve`)
    --config FILE                    explicit config file

vizcontrol serve [PATH]     # file or directory, default .vizcontrol/runs
    --port N  --host H  --no-browser  --dev

vizcontrol ls [DIR]         # table of runs: time, target, outcome, duration, calls
vizcontrol info FILE        # run metadata, config, truncation, counts per table
vizcontrol diagram FILE [--stage NAME]   # Level 1 (or one stage, Level 2) as a Mermaid flowchart on stdout
```

Everything after the script path (or `-m module`) belongs to the target program, the same as with `python`.

## Exit codes

`run` exits with the traced program's exit code (see [runners](runners.md#script-and-module-fr-run-126)).
VizControl's own failures (bad config, unwritable output, no free monitoring tool id) exit with **2** before the
program starts and print the reason to stderr. Other commands use 0 for success, 1 for a missing file and 2 for
usage errors.

## Configuration

Precedence: CLI flags > `trace()` kwargs > `--config` file > `[tool.vizcontrol]` in the nearest `pyproject.toml`
(searched upward from cwd) > defaults. Parsed with `tomllib`.

```toml
[tool.vizcontrol]
level = "values"                # calls | values | state
include = ["app.*"]             # module globs or paths; default: project root
exclude = ["app.migrations.*"]
opaque_calls = true
threads = true
store_source = true
out_dir = ".vizcontrol/runs"
stage_depth = 2

[tool.vizcontrol.limits]
max_calls = 1_000_000
max_depth = 200
max_values_bytes = 268_435_456  # 256 MiB of encoded values per run

[tool.vizcontrol.values]
max_depth = 3
max_items = 20
max_str = 200
max_value_bytes = 65_536
max_nodes = 100                 # per value: bounds the cost of each binding
safe_repr_types = ["decimal.Decimal", "uuid.UUID"]
serializers = { "numpy.ndarray" = "mypkg.viz:ndarray" }

[tool.vizcontrol.redact]
names = ["ssn", "iban"]         # added to defaults
values = ['Bearer\s+\S+']
defaults = true
hook = "mypkg.viz:redact"

[tool.vizcontrol.stages]
API = ["app.api.*"]
Service = ["app.services.*"]
Persistence = ["app.repo.*", "sqlalchemy.*"]
Load = ["etl:load_*"]           # module:function, a stage per matching function and the calls below it
```

`Config` is one frozen dataclass. Unknown keys are an error that names the key, which catches typos early.

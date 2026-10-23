# Contributing to VizControl

Thanks for helping. Issues, fixes, docs and ideas are all welcome. By taking part you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## The most useful report

VizControl has to read any Python project, so the best bug report is one where it got something wrong on real code:
a run that failed to record, a callback that wasn't marked, a value that came out wrong, a graph that doesn't match
what the program did. Please include:

- a **minimal script** that reproduces it (or the public project and the command you ran),
- what you expected and what the viewer or CLI showed,
- your VizControl version (`python -c "import vizcontrol; print(vizcontrol.__version__)"`), Python version and OS.

Traces (`.vizc` files) hold values from your program: don't attach one unless you know what is in it.

Vulnerabilities go through [SECURITY.md](SECURITY.md), never a public issue. Questions and ideas are welcome in
[Discussions](https://github.com/DatzinDev/VizControl/discussions).

## Setting up

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/); for the viewer, Node 22; for its end-to-end test,
Google Chrome (`CHROME_PATH` points elsewhere).

```
uv sync                                   # the package (editable) and the dev tools
uv run pytest                             # Python tests
uv run ruff check .                       # lint
cd frontend && npm ci && npm run build    # the viewer, into src/vizcontrol/viewer/static
cd frontend && npm run dev                # or hot reload, with `vizcontrol serve` on its default port 7171
cd frontend && npm run e2e                # traces the ETL example and drives the built viewer in headless Chrome
uv run python benchmarks/overhead.py      # tracing overhead, if you touched capture
```

The [docs](docs/README.md) explain how it fits together: the requirements are numbered (`FR-*`, `NFR-*`), the
[architecture](docs/02-architecture.md) lists the components, and each component has its own page under
`docs/components/`.

## Changing code

- **No runtime dependencies.** The package uses the standard library only (NFR-PKG-1), and that is a feature:
  `pip install vizcontrol` must work anywhere. Dev and test dependencies are fine. The viewer's npm dependencies
  are pinned to exact versions.
- **Match the code around you**: its naming, comments and idioms. Prefer the smallest change that solves the
  problem where it starts.
- **Leave a test behind** for any logic you add or fix: a pytest test for Python, a check in
  `frontend/e2e/smoke.mjs` for the viewer.
- **Keep the docs true.** If the change alters what a component does, its page in `docs/` changes in the same PR.
- **Safety stays on.** Redaction, the loopback-only server and its Host and Origin checks are there on purpose. A
  change that touches them needs a test showing they still hold.

## Commits and pull requests

- Work on a branch named `type/short-name` (`fix/callback-in-lambda`, `docs/guide-flask`).
- One logical change per commit, with a one-line [Conventional Commits](https://www.conventionalcommits.org/)
  message: `type(scope): what changed` (`feat(viewer): …`, `fix(capture): …`, `docs(readme): …`). No body: if a
  change needs a paragraph to explain, split it. English or Spanish are both fine.
- Open the pull request against `main`. CI has to be green: lint and tests on Python 3.12 and 3.13, the viewer's
  typecheck, build and e2e, and a clean install of the built wheel.

## License

VizControl is licensed under the [GNU AGPL v3.0 or later](LICENSE). By contributing, you agree that your
contribution is licensed under the same terms.

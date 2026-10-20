# Demo: a tiny notes-to-site builder

A small project to see what VizControl shows. `notes` reads a folder of text notes, finds the `[[links]]`
between them, and renders one HTML page per note plus an index:

```
notes/__main__.py   build(): read -> link -> render
notes/reader.py     read_folder(): .txt files -> Note objects
notes/links.py      link_notes(): fills each note's links and backlinks; a link to a missing note raises BrokenLink
notes/render.py     render_site(): Note -> HTML page, plus an index ranked by backlinks
notes/model.py      Note, Site (dataclasses)
data/ok/            four notes that link to each other
data/broken/        the same, but tea.txt links to [[kettle]], which does not exist
tests/              four tests
```

## Run it

```
pip install vizcontrol pytest     # or, inside this repository: uv run examples/demo/demo.sh
./demo.sh
```

`demo.sh` traces the project three ways and opens the viewer on all of them:

```
vizcontrol run -m notes data/ok            # a build that works
vizcontrol run -m notes data/broken        # a build that fails
pytest --vizcontrol                        # one run per test
vizcontrol serve
```

## What to look at

1. **How it works.** Open `notes data/ok`. Level 1 shows the stages the run went through (`notes.__main__` calls
   `notes.reader`, `notes.links` and `notes.render`), with the main path in accent. Double-click `notes.links` to
   see its functions and the calls between them (Level 2), click `link_notes` to open its call (Level 3), and click
   an argument to see the value (Level 4). "Copy as Mermaid" gives the same diagram as text.
2. **How the data changes.** In Data Flow, follow a `Note`: it comes out of `read_note`, passes through
   `link_notes`, where its `links` and `backlinks` fill up (marked Δ at each `resolve`), and ends in
   `render_page` and `render_index`.
3. **Where it stopped, and why.** Open `notes data/broken`. It opens on `resolve`, where `BrokenLink` was raised,
   with the error path in red and the locals at the raise (`note` is tea, `target` is `kettle`). Back on Level 1,
   `notes.links` says "stopped here", and "Compare with notes data/ok" shows where the two runs split.
4. **The whole project.** The Map tab joins the six runs: every stage any run went through, how many runs did, and
   the selected run's route.

From the terminal, without the viewer:

```
vizcontrol ls                                 # the runs
vizcontrol diagram .vizcontrol/runs/<run>.vizc   # Level 1 as a Mermaid flowchart
vizcontrol diff <ok>.vizc <broken>.vizc         # where the two builds split: tea.txt's body differs
```

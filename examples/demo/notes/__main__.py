"""python -m notes FOLDER [--out DIR]: build a small site from a folder of notes."""

import sys
from pathlib import Path

from notes.links import link_notes
from notes.reader import read_folder
from notes.render import render_site


def build(folder: str):
    return render_site(link_notes(read_folder(folder)))


def main(argv: list[str]) -> int:
    folder = argv[0] if argv else "data/ok"
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    site = build(folder)
    for slug, page in site.pages.items():
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{slug}.html").write_text(page)
    print(f"built {len(site.pages)} pages from {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Read a folder of `.txt` notes: the first line is the title, the rest is the body."""

from pathlib import Path

from notes.model import Note


def read_folder(folder: str) -> list[Note]:
    return [read_note(path) for path in sorted(Path(folder).glob("*.txt"))]


def read_note(path: Path) -> Note:
    title, _, body = path.read_text().partition("\n")
    return Note(slug=path.stem, title=title.strip(), body=body.strip())

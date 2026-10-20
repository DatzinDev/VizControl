"""Find the [[links]] between notes and check that every one points to a note that exists."""

import re

from notes.model import Note, Site

LINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")


class BrokenLink(Exception):
    pass


def link_notes(notes: list[Note]) -> Site:
    site = Site(notes={n.slug: n for n in notes})
    for note in notes:
        note.links = find_links(note.body)
    for note in notes:
        for target in note.links:
            resolve(site, note, target).backlinks.append(note.slug)
    return site


def find_links(body: str) -> list[str]:
    return LINK.findall(body)


def resolve(site: Site, note: Note, target: str) -> Note:
    if target not in site.notes:
        raise BrokenLink(f"{note.slug} links to [[{target}]], which does not exist")
    return site.notes[target]

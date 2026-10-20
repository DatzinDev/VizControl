"""Turn each note into an HTML page, and add an index of all of them."""

import html

from notes.links import LINK
from notes.model import Note, Site


def render_site(site: Site) -> Site:
    for note in site.notes.values():
        site.pages[note.slug] = render_page(note)
    site.pages["index"] = render_index(site)
    return site


def render_page(note: Note) -> str:
    body = LINK.sub(lambda m: f'<a href="{m[1]}.html">{m[1]}</a>', html.escape(note.body))
    back = "".join(f'<li><a href="{s}.html">{s}</a></li>' for s in note.backlinks)
    return f"<h1>{html.escape(note.title)}</h1>\n<p>{body}</p>\n<ul>{back}</ul>\n"


def render_index(site: Site) -> str:
    ranked = sorted(site.notes.values(), key=lambda n: (-len(n.backlinks), n.slug))
    items = "".join(f'<li><a href="{n.slug}.html">{html.escape(n.title)}</a> ({len(n.backlinks)})</li>' for n in ranked)
    return f"<h1>Index</h1>\n<ol>{items}</ol>\n"

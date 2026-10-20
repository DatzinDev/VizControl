from dataclasses import dataclass, field


@dataclass
class Note:
    slug: str
    title: str
    body: str
    links: list[str] = field(default_factory=list)  # slugs this note links to
    backlinks: list[str] = field(default_factory=list)  # slugs that link here


@dataclass
class Site:
    notes: dict[str, Note]
    pages: dict[str, str] = field(default_factory=dict)  # slug -> html

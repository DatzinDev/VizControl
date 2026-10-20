import pytest
from notes.__main__ import build
from notes.links import BrokenLink, find_links


def test_finds_links():
    assert find_links("see [[beans]] and [[grinder]]") == ["beans", "grinder"]


def test_backlinks():
    site = build("data/ok")
    assert sorted(site.notes["beans"].backlinks) == ["coffee", "grinder"]


def test_index_ranks_by_backlinks():
    assert build("data/ok").pages["index"].index("Grinder") < build("data/ok").pages["index"].index("Tea")


def test_broken_link():
    with pytest.raises(BrokenLink):
        build("data/broken")

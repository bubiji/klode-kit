"""A minimal DOM built with the standard library ``html.parser`` (no third-party parser), for structural
assertions on generated HTML: elements, attributes, text, and semantic lookups by tag / class / data-*."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterator

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)
    parent: "Node | None" = None

    def iter(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def find_all(self, pred: Callable[["Node"], bool] | str) -> list["Node"]:
        if isinstance(pred, str):
            tag = pred
            pred = lambda n: n.tag == tag  # noqa: E731
        return [n for n in self.iter() if n is not self and pred(n)]

    def by_class(self, cls: str) -> list["Node"]:
        return self.find_all(lambda n: cls in (n.attrs.get("class") or "").split())

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)

    def get(self, name: str) -> str | None:
        return self.attrs.get(name)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), [], self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, dict(attrs), [], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str | bytes) -> Node:
    if isinstance(html, bytes):
        html = html.decode("utf-8")
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root

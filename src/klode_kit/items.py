"""WI-07b: ``items.yaml`` loader - item slugs and their aliases (design 02 r2.2-C 项目归一, r2.2-D item_label).

``load(path=None)`` reads a table (``None``: the packaged ``klode_kit/config/items.yaml``); ``default()`` is the
cached packaged table. The file is a strict YAML subset, parsed here so the runtime needs no YAML package::

    # comment
    <slug>:                         # [a-z0-9][a-z0-9_-]*
      category: <text>              # optional, may be "double-quoted"
      aliases: [<a>, "<b>", ...]    # one-line flow list, optional

Any other line is a ``ConfigError`` (exit 40) naming ``<file>:<line>``.

Matching (``Items.resolve``) compares NFKC + casefold + whitespace-removed forms; an item's slug is one of its
aliases. One normalised alias under two items (or equal to another item's slug) is a ``ConfigError``. A label no
item knows resolves to ``slugify(label)`` (NFKC, casefold, every run of non-word characters -> ``-``) and is
reported by ``Items.new_items``. Values and units are never normalised here (r2.2-C).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Iterable

from klode_kit.config import ConfigError

ITEMS_FILE = "items.yaml"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+")
_TOP = re.compile(r"^(?P<slug>\S[^:]*):$")
_SUB = re.compile(r"^\s+(?P<key>[A-Za-z_]+):\s*(?P<value>.*)$")


@dataclass(frozen=True)
class Item:
    slug: str
    category: str | None
    aliases: tuple[str, ...]


def _key(label: str) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", label)).casefold()


def slugify(label: str) -> str:
    """``Other Index`` -> ``other-index``; ``某某 指数`` -> ``某某-指数``; ``ＡＬＴ（谷丙）`` -> ``alt-谷丙``."""
    slug = _NON_WORD.sub("-", unicodedata.normalize("NFKC", label).casefold()).strip("-")
    if not slug:
        raise ValueError(f"no slug for label {label!r}")
    return slug


class Items:
    """An item table: ``resolve`` labels to slugs, list ``new_items``."""

    def __init__(self, items: Iterable[Item], *, where: dict[str, str] | None = None):
        self.items = tuple(items)
        self._by_key: dict[str, str] = {}
        self._category = {it.slug: it.category for it in self.items}
        for it in self.items:
            for alias in (it.slug, *it.aliases):
                key = _key(alias)
                owner = self._by_key.setdefault(key, it.slug)
                if owner != it.slug:
                    loc = (where or {}).get(it.slug, "items")
                    raise ConfigError(f"{loc}: alias {alias!r} of {it.slug!r} already names {owner!r}", key="items")

    def resolve(self, label: str) -> tuple[str, bool]:
        """``(slug, known)``; an unknown label gets ``slugify(label)``. Empty labels are a ``ValueError``."""
        key = _key(label)
        if not key:
            raise ValueError("empty item label")
        slug = self._by_key.get(key)
        return (slug, True) if slug is not None else (slugify(label), False)

    def category(self, slug: str) -> str | None:
        return self._category.get(slug)

    def new_items(self, labels: Iterable[str]) -> list[str]:
        """Slugs of the unknown labels, first-seen order, no repeats."""
        out: list[str] = []
        for label in labels:
            slug, known = self.resolve(label)
            if not known and slug not in out:
                out.append(slug)
        return out


def _strip_comment(line: str) -> str:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def _flow_list(text: str, loc: str) -> tuple[str, ...]:
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ConfigError(f"{loc}: aliases must be a one-line list [a, b, ...]", key="items")
    inner = text[1:-1].strip()
    if not inner:
        return ()
    values = tuple(_scalar(v) for v in inner.split(","))
    if any(not v for v in values):
        raise ConfigError(f"{loc}: empty alias", key="items")
    return values


def parse(text: str, source: str) -> Items:
    """Parse the ``items.yaml`` subset (see the module docstring); ``source`` names the file in errors."""
    order: list[str] = []
    fields: dict[str, dict] = {}
    where: dict[str, str] = {}
    current: str | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        loc = f"{source}:{n}"
        top = _TOP.match(line)
        if top:
            slug = top.group("slug").strip()
            if not _SLUG.match(slug):
                raise ConfigError(f"{loc}: item slug {slug!r} must match {_SLUG.pattern}", key="items")
            if slug in fields:
                raise ConfigError(f"{loc}: item {slug!r} defined twice", key="items")
            order.append(slug)
            fields[slug], where[slug], current = {}, loc, slug
            continue
        sub = _SUB.match(line)
        if sub is None or current is None or sub.group("key") not in ("category", "aliases"):
            raise ConfigError(f"{loc}: expected '<slug>:', '  category: <text>' or '  aliases: [..]'", key="items")
        key = sub.group("key")
        if key in fields[current]:
            raise ConfigError(f"{loc}: {key} given twice for {current!r}", key="items")
        fields[current][key] = _flow_list(sub.group("value"), loc) if key == "aliases" else _scalar(sub.group("value"))
    items = [Item(s, fields[s].get("category") or None, tuple(dict.fromkeys(fields[s].get("aliases", ()))))
             for s in order]
    return Items(items, where=where)


def load(path: str | os.PathLike | None = None) -> Items:
    """The item table at ``path`` (``None``: the packaged ``config/items.yaml``)."""
    if path is None:
        return parse((resources.files("klode_kit.config") / ITEMS_FILE).read_text(encoding="utf-8"), ITEMS_FILE)
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read items table: {exc}", key="items") from exc
    return parse(text, str(p))


@lru_cache(maxsize=1)
def default() -> Items:
    """The packaged item table (loaded once)."""
    return load()

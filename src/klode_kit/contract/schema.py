"""Contract schemas: loading and validation (WI-00b1).

The schemas are JSON Schema draft 2020-12 files shipped inside the package
(``klode_kit/schemas/<name>.json``), so an installed plugin carries them.

Two rules cannot be said in plain JSON Schema and are custom keywords here:

- ``x-uniqueBy: "<key>"`` on an array: no two object items share the value of ``<key>``; the
  second occurrence is reported at ``<array>[j].<key>`` (choices, F17).
- ``x-mirror: {"target": t, "sources": [path, ...]}`` on an object: ``instance[t]`` equals the
  value at the first source path that resolves (paths are key / index lists, negative indexes
  count from the end); reported at ``t`` (record status, R4-01).

Plain ``jsonschema`` validators ignore these keywords; always validate through this module.

Every issue carries a dotted ``path`` that names the offending field itself: a missing required
property or an unexpected extra property is reported at that property, not at its parent object.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Iterable

from jsonschema import Draft202012Validator, ValidationError, validators

NAMES: tuple[str, ...] = (
    "build_receipt",
    "cells",
    "check_receipt",
    "choices",
    "derived",
    "export_map",
    "fixes",
    "isolation_receipt",
    "kb_version",
    "lane_receipt",
    "manifest",
    "ocr_line",
    "publish_log",
    "receipt",
    "record",
    "writer_receipt",
)


@dataclass(frozen=True)
class Issue:
    path: str
    message: str
    keyword: str


class ContractError(ValueError):
    """An instance violates its contract; ``issues`` lists every violation."""

    def __init__(self, name: str, issues: Iterable[Issue]):
        self.name = name
        self.issues = list(issues)
        detail = "; ".join(f"{i.path or '<root>'}: {i.message}" for i in self.issues)
        super().__init__(f"{name}: {detail}")


class UnknownSchema(LookupError):
    """No contract of that name."""


# --------------------------------------------------------------------------- custom keywords


def _unique_by(validator, key, instance, schema):
    if not validator.is_type(instance, "array"):
        return
    first: dict[str, int] = {}
    for idx, item in enumerate(instance):
        if not isinstance(item, dict) or key not in item:
            continue
        marker = json.dumps(item[key], sort_keys=True)
        if marker in first:
            yield ValidationError(f"{key} {item[key]!r} repeats item {first[marker]}", path=[idx, key])
        else:
            first[marker] = idx


_MISSING = object()


def _resolve(instance: Any, path: list) -> Any:
    cur = instance
    for step in path:
        if isinstance(step, int):
            if not isinstance(cur, list) or not -len(cur) <= step < len(cur):
                return _MISSING
        elif not isinstance(cur, dict) or step not in cur:
            return _MISSING
        cur = cur[step]
    return cur


def _mirror(validator, spec, instance, schema):
    if not validator.is_type(instance, "object"):
        return
    target = spec["target"]
    if target not in instance:
        return  # `required` reports it
    for source in spec["sources"]:
        expected = _resolve(instance, source)
        if expected is _MISSING:
            continue
        if instance[target] != expected:
            where = ".".join(str(s) for s in source)
            yield ValidationError(f"{target} {instance[target]!r} must equal {where} ({expected!r})", path=[target])
        return


_Validator = validators.extend(Draft202012Validator, {"x-uniqueBy": _unique_by, "x-mirror": _mirror})


# --------------------------------------------------------------------------- loading


@lru_cache(maxsize=None)
def _text(name: str) -> str:
    if name not in NAMES:
        raise UnknownSchema(f"unknown contract {name!r}; known: {', '.join(NAMES)}")
    return resources.files("klode_kit").joinpath("schemas", f"{name}.json").read_text(encoding="utf-8")


def load(name: str) -> dict:
    """A fresh copy of the schema document."""
    return json.loads(_text(name))


@lru_cache(maxsize=None)
def _validator(name: str):
    doc = load(name)
    _Validator.check_schema(doc)
    return _Validator(doc)


# --------------------------------------------------------------------------- issues


def _fmt(parts: Iterable) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _extra_properties(error: ValidationError) -> list[str]:
    schema = error.schema if isinstance(error.schema, dict) else {}
    known = set(schema.get("properties", {}))
    patterns = [re.compile(p) for p in schema.get("patternProperties", {})]
    return [k for k in error.instance if k not in known and not any(p.search(k) for p in patterns)]


def _issues_of(error: ValidationError) -> list[Issue]:
    base = list(error.absolute_path)
    if error.validator == "required":
        missing = [p for p in error.validator_value if p not in error.instance and error.message.startswith(repr(p))]
        if missing:
            return [Issue(_fmt(base + [missing[0]]), error.message, "required")]
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extras = _extra_properties(error)
        if extras:
            return [Issue(_fmt(base + [k]), f"unexpected property {k!r}", "additionalProperties") for k in extras]
    return [Issue(_fmt(base), error.message, str(error.validator))]


def issues(name: str, instance: Any) -> list[Issue]:
    """Every contract violation of ``instance``, sorted by path; ``[]`` when it conforms."""
    found: dict[tuple[str, str], Issue] = {}
    for error in _validator(name).iter_errors(instance):
        for issue in _issues_of(error):
            found.setdefault((issue.path, issue.message), issue)
    return sorted(found.values(), key=lambda i: (i.path, i.message))


def validate(name: str, instance: Any) -> None:
    problems = issues(name, instance)
    if problems:
        raise ContractError(name, problems)

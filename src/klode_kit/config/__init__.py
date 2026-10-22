"""Configuration: packaged defaults, data-project override, canonical form (WI-00b2).

``defaults.toml`` ships inside the package (beside this module, like ``klode_kit/schemas``) so an
installed plugin carries it. A data project overrides individual keys in ``<project>/klode-kit.toml``.

Rules:
- ``KEYS`` is the authoritative key set (design 02 r2.6). The defaults file must provide every
  key, with the right type, and nothing else; an override may set any subset of them.
- Unknown keys, missing default keys, wrong types, out-of-enum values and unparseable TOML raise
  ``ConfigError`` (``exit_code`` 40, the message names the key or file).
- ``config_version`` is the format version of the defaults file. It cannot be overridden and is
  excluded from ``Config.canonical()`` - and therefore from ``cfg_fp`` (r4.4 R3-06).
- Types are strict: ``bool`` is not an ``int`` and a ``float`` is not an ``int``. A ``float`` key
  accepts an integer literal and normalises it to ``float``, so ``2`` and ``2.0`` are the same
  effective configuration (fixed numeric representation for ``cfg_fp``).
- ``CLOUD_ENV_ALLOWLIST`` (r5) is a constant on purpose: it is not a configuration key, so no
  project file can widen the environment passed to cloud processes.

CLI: ``klode-kit config show [--project DIR]`` prints the effective configuration as JSON
(``{"config_version", "values", "config_sha256"}``); ``ConfigError`` is exit 40.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

CONFIG_EXIT = 40
USAGE_EXIT = 2
OVERRIDE_FILE = "klode-kit.toml"
VERSION_KEY = "config_version"

CLOUD_ENV_ALLOWLIST: frozenset[str] = frozenset({"PATH", "HOME", "TMPDIR", "LANG", "ANTHROPIC_API_KEY"})

# dotted key -> type tag ("str", "int", "float", "list[str]") ; design 02 r2.6
KEYS: dict[str, str] = {
    "routes": "list[str]",
    "base_route": "str",
    "route_retry": "int",
    "repeat_ratio_max": "float",
    "line_sim_tau": "float",
    "claude.model": "str",
    "claude.effort": "str",
    "codex.model": "str",
    "codex.effort": "str",
    "cloud.timeout_s": "int",
    "cloud.max_cost_usd_per_batch": "float",
    "text_layer.min_chars": "int",
    "text_layer.garbage_max": "float",
    "cards.min_aliases": "int",
    "cards.min_anchors_full": "int",
    "cards.writer_max_rounds": "int",
    "cards.writer_concurrency": "int",
    "cards.writer_timeout_s": "int",
    "desk.holdout_min": "int",
    "desk.holdout_pass": "float",
    "loops.max_total_rounds": "int",
    "loops.no_progress_rounds": "int",
    "deident.mode": "str",
}
ENUMS: dict[str, frozenset[str]] = {"deident.mode": frozenset({"pseudonym", "dateshift"})}
TABLES = frozenset(k.split(".", 1)[0] for k in KEYS if "." in k)


class ConfigError(Exception):
    """Invalid configuration; environment/config class failure (exit 40)."""

    exit_code = CONFIG_EXIT

    def __init__(self, message: str, key: str | None = None):
        super().__init__(message)
        self.key = key


def _packaged_defaults() -> Path:
    return Path(str(resources.files(__name__) / "defaults.toml"))


def _parse(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc


def _flatten(data: Mapping[str, Any], origin: str) -> dict[str, Any]:
    """Nested tables and/or dotted keys -> dotted keys; tables only one level deep."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise ConfigError(f"{origin}: invalid key {key!r}", str(key))
        if isinstance(value, Mapping):
            if key not in TABLES:
                raise ConfigError(f"{origin}: unknown configuration table [{key}]", key)
            for sub, subval in value.items():
                dotted = f"{key}.{sub}"
                if isinstance(subval, Mapping):
                    raise ConfigError(f"{origin}: unknown configuration table [{dotted}]", dotted)
                _put(flat, dotted, subval, origin)
        else:
            _put(flat, key, value, origin)
    return flat


def _put(flat: dict[str, Any], key: str, value: Any, origin: str) -> None:
    if key in flat:
        raise ConfigError(f"{origin}: key {key} given twice", key)
    flat[key] = value


def _check(key: str, value: Any, origin: str) -> Any:
    tag = KEYS[key]
    if tag == "str":
        ok = isinstance(value, str)
    elif tag == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif tag == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok:
            value = float(value)
            ok = value == value and value not in (float("inf"), float("-inf"))
    else:  # list[str]
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        value = list(value) if ok else value
    if not ok:
        raise ConfigError(f"{origin}: {key} must be {tag}, got {value!r}", key)
    if key in ENUMS and value not in ENUMS[key]:
        raise ConfigError(f"{origin}: {key} must be one of {sorted(ENUMS[key])}, got {value!r}", key)
    return value


def _nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for key in sorted(flat):
        if "." in key:
            table, sub = key.split(".", 1)
            nested.setdefault(table, {})[sub] = flat[key]
        else:
            nested[key] = flat[key]
    return nested


@dataclass(frozen=True)
class Config:
    """Effective configuration. ``values`` is keyed by dotted key and excludes ``config_version``."""

    config_version: int
    values: Mapping[str, Any]
    sources: tuple[str, ...]

    def __getitem__(self, key: str) -> Any:
        if key == VERSION_KEY:
            return self.config_version
        try:
            value = self.values[key]
        except KeyError:
            raise KeyError(key) from None
        return list(value) if isinstance(value, list) else value

    def flat(self) -> dict[str, Any]:
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in self.values.items()}
        out[VERSION_KEY] = self.config_version
        return out

    def nested(self) -> dict[str, Any]:
        return _nest(self.values)

    def canonical(self) -> bytes:
        """Sorted keys, compact separators, UTF-8; ``config_version`` excluded (r4.4)."""
        return json.dumps(self.nested(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()


def _defaults(path: Path | None) -> tuple[int, dict[str, Any], str]:
    path = _packaged_defaults() if path is None else Path(path)
    origin = str(path)
    flat = _flatten(_parse(path), origin)
    version = flat.pop(VERSION_KEY, None)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ConfigError(f"{origin}: {VERSION_KEY} must be a positive integer, got {version!r}", VERSION_KEY)
    unknown = sorted(set(flat) - set(KEYS))
    if unknown:
        raise ConfigError(f"{origin}: unknown configuration key(s): {', '.join(unknown)}", unknown[0])
    missing = sorted(set(KEYS) - set(flat))
    if missing:
        raise ConfigError(f"{origin}: missing default key(s): {', '.join(missing)}", missing[0])
    return version, {k: _check(k, v, origin) for k, v in flat.items()}, origin


def _apply(base: dict[str, Any], overrides: Mapping[str, Any], origin: str) -> dict[str, Any]:
    flat = _flatten(overrides, origin)
    if VERSION_KEY in flat:
        raise ConfigError(f"{origin}: {VERSION_KEY} is set by the defaults only and cannot be overridden",
                          VERSION_KEY)
    unknown = sorted(set(flat) - set(KEYS))
    if unknown:
        raise ConfigError(f"{origin}: unknown configuration key(s): {', '.join(unknown)}", unknown[0])
    merged = dict(base)
    merged.update({k: _check(k, v, origin) for k, v in flat.items()})
    return merged


def resolve(overrides: Mapping[str, Any], *, defaults: str | Path | None = None) -> Config:
    """Defaults overlaid with an in-memory override mapping (dotted keys or nested tables)."""
    version, base, origin = _defaults(None if defaults is None else Path(defaults))
    merged = _apply(base, overrides, "<overrides>")
    return Config(version, merged, (origin, "<overrides>"))


def load(project: str | Path | None = None, *, defaults: str | Path | None = None) -> Config:
    """Defaults, then ``<project>/klode-kit.toml`` if the project has one."""
    version, values, origin = _defaults(None if defaults is None else Path(defaults))
    sources = [origin]
    if project is not None:
        override = Path(project) / OVERRIDE_FILE
        if override.is_file():
            values = _apply(values, _parse(override), str(override))
            sources.append(str(override))
    return Config(version, values, tuple(sources))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit config", description="Show the effective configuration.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="print the effective configuration as JSON")
    show.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    cfg = load(Path.cwd() if args.project is None else args.project)
    out = {"config_version": cfg.config_version, "values": cfg.nested(), "config_sha256": cfg.sha256(),
           "sources": list(cfg.sources)}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit config``
    sys.exit(main())

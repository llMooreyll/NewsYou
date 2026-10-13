#!/usr/bin/env python3
"""Layered configuration for the newsletter pipeline.

Precedence, highest first::

    CLI flag  >  NEWSLETTER_* env var  >  config/config.toml  >  built-in default

Every value has a built-in default, so the pipeline runs with no config file at
all and the file only overrides what it names. Two deliberate choices:

* **Unknown keys are an error.** A typo in a config file should be loud, not a
  silent no-op that leaves the old value in place.
* **TOML.** ``tomllib`` is in the standard library from Python 3.11, so the
  configuration layer adds no dependency and still allows comments.

Environment variables spell the dotted path in caps with the prefix::

    NEWSLETTER_AI_BACKEND=none        -> ai.backend
    NEWSLETTER_COLLECT_ENGINE=legacy  -> collect.engine

Only scalar leaves can be set this way; lists and tables come from the file.

``--show-config`` on any entry point prints the effective configuration as TOML
with a trailing ``# from <layer>`` comment on every line, so it is both a
diffable snapshot and still valid TOML.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:            # so `import collectors` works anywhere
    sys.path.insert(0, str(_TOOLS))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "config.toml"
ENV_PREFIX = "NEWSLETTER_"

# Keys allowed inside one [[feeds]] entry, and which of them are required.
FEED_KEYS = {"id", "name", "url", "fulltext", "limit", "engine", "kind",
             "date_source", "source"}
FEED_REQUIRED = {"id", "url"}

# Tables keyed by source name. Their keys are open — a config-defined feed adds
# its own entry — so unknown keys are allowed there; only the value type is
# checked, using the built-in entries as the sample.
OPEN_TABLES = {"sources.limits", "sources.labels"}


class ConfigError(RuntimeError):
    """A config file that cannot be trusted: unknown key, wrong type, bad value."""


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------

def defaults() -> dict:
    """The full default configuration.

    This dict *is* the schema: it is what validation checks a file against, what
    ``--show-config`` renders, and what every entry point falls back to. The
    collector registry is imported lazily because it pulls in every source
    module, and a module-level import would create a cycle once a collector
    wants to read configuration.
    """
    from collectors import registry, telegram

    return {
        "ai": {
            "backend": "pi",
            "model": "deepseek-v4.1-flash",
            "timeout": 900,
            "reuse": False,
            "args": [],
        },
        "collect": {
            "engine": "modern",
            "fulltext": "today",
            "images": True,
            "images_per_post": 2,
            "image_cap": 200,
            "limit": 0,                     # 0 = fall back to sources.limits
        },
        "telegram": {
            "channels": list(telegram.DEFAULT_CHANNELS),
            "pages": 4,
            "day": "auto",
        },
        "hackernews": {
            "feed": "topstories",
        },
        "sources": {
            "order": list(registry.ORDER),
            "enabled": [],                  # empty = every source in order
            "limits": {name: registry.default_limit(name) for name in registry.ORDER},
            "labels": dict(registry.SOURCE_LABELS),
        },
        "feeds": [],                        # generic RSS/Atom, free-form list
        "output": {
            "digest": "site/digest.html",
        },
        "archive": {
            "root": "archive",
        },
        "paths": {
            "collected": "data/collected",
            "ai": "data/ai",
            "processed": "data/processed",
        },
    }


# --------------------------------------------------------------------------
# Flatten / unflatten / render
# --------------------------------------------------------------------------

def _flatten(node: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, value in node.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _unflatten(flat: dict) -> dict:
    out: dict = {}
    for dotted, value in flat.items():
        node = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _toml_str(text: str) -> str:
    return '"' + (text.replace("\\", "\\\\").replace('"', '\\"')
                  .replace("\n", "\\n")) + '"'


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ConfigError(f"cannot render {type(value).__name__} as TOML")


def _render(node: dict, path: str, origins: dict | None) -> list[str]:
    lines: list[str] = []
    tables: list[tuple[str, object]] = []
    for key, value in node.items():
        if isinstance(value, dict) or (isinstance(value, list) and value
                                       and all(isinstance(x, dict) for x in value)):
            tables.append((key, value))
            continue
        dotted = f"{path}.{key}" if path else key
        suffix = ""
        if origins is not None:
            suffix = f"  # from {origins.get(dotted, 'default')}"
        lines.append(f"{key} = {_toml_value(value)}{suffix}")
    for key, value in tables:
        name = f"{path}.{key}" if path else key
        if isinstance(value, list):
            for item in value:
                lines += ["", f"[[{name}]]"]
                lines += _render(item, name, origins)
        else:
            lines += ["", f"[{name}]"]
            lines += _render(value, name, origins)
    return lines


def to_toml(node: dict, origins: dict | None = None) -> str:
    """Render a config dict as TOML, optionally annotated with value origins."""
    return "\n".join(_render(node, "", origins)).lstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _type_ok(value, like) -> bool:
    if like is None:
        return True
    if isinstance(like, bool):
        return isinstance(value, bool)
    if isinstance(like, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(like, str):
        return isinstance(value, str)
    if isinstance(like, list):
        return isinstance(value, list)
    return True


def _validate(node, schema, where: str = "") -> None:
    if not isinstance(node, dict):
        raise ConfigError(f"{where or 'root'}: expected a table, "
                          f"got {type(node).__name__}")
    for key, value in node.items():
        dotted = f"{where}.{key}" if where else key
        if key not in schema:
            if where in OPEN_TABLES:
                sample = next(iter(schema.values()), None)
                if sample is not None and not _type_ok(value, sample):
                    raise ConfigError(
                        f"{dotted}: expected {type(sample).__name__}, "
                        f"got {type(value).__name__}")
                continue
            known = ", ".join(sorted(schema)) or "(none)"
            raise ConfigError(f"{dotted}: unknown key; "
                              f"known keys here: {known}")
        like = schema[key]
        if isinstance(like, dict):
            _validate(value, like, dotted)
        elif key == "feeds":
            _validate_feeds(value)
        elif not _type_ok(value, like):
            raise ConfigError(
                f"{dotted}: expected {type(like).__name__}, "
                f"got {type(value).__name__}")


def _validate_feeds(value) -> None:
    if not isinstance(value, list):
        raise ConfigError("feeds: expected a list of tables")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ConfigError(f"feeds[{index}]: expected a table")
        unknown = set(entry) - FEED_KEYS
        if unknown:
            raise ConfigError(
                f"feeds[{index}]: unknown key(s) {', '.join(sorted(unknown))}; "
                f"known: {', '.join(sorted(FEED_KEYS))}")
        missing = FEED_REQUIRED - set(entry)
        if missing:
            raise ConfigError(f"feeds[{index}]: missing {', '.join(sorted(missing))}")


def _coerce(text: str, like):
    """Turn an env var string into the type the default implies."""
    if isinstance(like, bool):
        lowered = text.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"expected a boolean, got {text!r}")
    if isinstance(like, int) and not isinstance(like, bool):
        try:
            return int(text)
        except ValueError as exc:
            raise ConfigError(f"expected an integer, got {text!r}") from exc
    if isinstance(like, list):
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


# --------------------------------------------------------------------------
# The resolved configuration
# --------------------------------------------------------------------------

class Config:
    """Effective values plus the layer each one came from."""

    def __init__(self, values: dict, origins: dict, path: Path | None):
        self._values = values
        self._origins = origins
        self.path = path

    def get(self, dotted: str, fallback=None):
        """A leaf value, or a whole subtree reconstructed as a nested dict.

        Values are stored flat, so `sources.limits` has no entry of its own —
        only `sources.limits.sspai` and friends. Asking for the table has to
        rebuild it, otherwise every nested table reads as missing.
        """
        if dotted in self._values:
            return self._values[dotted]
        prefix = dotted + "."
        subtree = {key[len(prefix):]: value for key, value in self._values.items()
                   if key.startswith(prefix)}
        if subtree:
            return _unflatten(subtree)
        return fallback

    def origin(self, dotted: str) -> str:
        return self._origins.get(dotted, "default")

    def as_dict(self) -> dict:
        return _unflatten(self._values)

    def dump(self, show_origin: bool = True) -> str:
        return to_toml(self.as_dict(), self._origins if show_origin else None)

    # A tiny bit of sugar so callers read naturally.
    def __getitem__(self, dotted: str):
        return self.get(dotted)


def load(path: Path | str | None = None, *, cli: dict | None = None,
         use_env: bool = True) -> Config:
    """Resolve the effective configuration.

    `cli` maps dotted keys to values the caller got from argparse; a value of
    None means "the flag was not given", which is what lets a flag beat the file
    without every default having to be duplicated.
    """
    schema = defaults()
    values = _flatten(schema)
    origins = {key: "default" for key in values}

    file_path = Path(path) if path is not None else DEFAULT_PATH
    if path is not None and not file_path.exists():
        raise ConfigError(f"config file not found: {file_path}")
    if file_path.exists():
        try:
            raw = tomllib.loads(file_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{file_path}: {exc}") from exc
        try:
            _validate(raw, schema)
        except ConfigError as exc:
            raise ConfigError(f"{file_path}: {exc}") from exc
        for key, value in _flatten(raw).items():
            values[key] = value
            origins[key] = "file"

    if use_env:
        for key in list(values):
            name = ENV_PREFIX + key.upper().replace(".", "_")
            if name in os.environ:
                values[key] = _coerce(os.environ[name], schema_leaf(schema, key))
                origins[key] = "env"

    for key, value in (cli or {}).items():
        if value is None:
            continue
        if key not in values:
            raise ConfigError(f"internal: CLI override for unknown key {key!r}")
        values[key] = value
        origins[key] = "cli"

    return Config(values, origins, file_path if file_path.exists() else None)


def schema_leaf(schema: dict, dotted: str):
    """Look up the default value for a dotted key, for type coercion."""
    node = schema
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.get(part, {})
        if not isinstance(node, dict):
            return None
    return node.get(parts[-1])


# --------------------------------------------------------------------------
# Shared CLI plumbing
# --------------------------------------------------------------------------

def add_arguments(parser) -> None:
    """Add the two flags every entry point shares."""
    parser.add_argument("--config", type=Path, default=None,
                        help="path to config.toml (default: config/config.toml)")
    parser.add_argument("--show-config", action="store_true",
                        help="print the effective configuration and exit")


def resolve(args, cli: dict | None = None) -> Config:
    """Build the Config for a parsed argparse namespace, honouring --config."""
    return load(getattr(args, "config", None), cli=cli)


def resolve_or_exit(args, cli: dict | None = None) -> Config:
    """Like `resolve`, but turn a bad config into a clean exit code 2."""
    try:
        return resolve(args, cli)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def emit_show_config(cfg: Config, args) -> bool:
    """Print the effective config when --show-config was passed. Returns True."""
    if not getattr(args, "show_config", False):
        return False
    where = cfg.path or "built-in defaults (no config file)"
    print(f"# effective configuration — {where}")
    print(f"# layers: CLI > {ENV_PREFIX}* env > config.toml > built-in default")
    print(cfg.dump(show_origin=True))
    return True


if __name__ == "__main__":
    print(load().dump(show_origin=True))

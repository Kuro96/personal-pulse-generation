#!/usr/bin/env python3
"""Load and validate local Personal Pulse configuration."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.local.json"
FIELDS = {
    "user": {"name", "language", "timezone"},
    "delivery": {"cards_target", "feed_target"},
    "paths": {"state_file", "journal_dir", "scratch_dir", "workdir", "image_executor", "python_executable", "env_file"},
    "sources": {"personal_context_dir", "notes_roots"},
    "schedule": {"cron", "job_name"},
}


class ConfigError(ValueError):
    """Configuration is absent or fails its schema."""


def object_fields(value, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ConfigError(f"{label} must contain exactly: {', '.join(sorted(fields))}")


def string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
        raise ConfigError(f"{label} must be a nonempty string without surrounding whitespace or NUL")
    return value


def expand_path(value, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    expanded = os.path.expanduser(os.path.expandvars(string(value, label)))
    if "$" in expanded or expanded.startswith("~"):
        raise ConfigError(f"{label} contains an unresolved variable or home directory")
    return str(Path(expanded).absolute())


def load_config(path: str | Path | None = None) -> dict:
    """Return validated, expanded values; explicit path precedes PULSE_CONFIG."""
    selected = path if path is not None else os.environ.get("PULSE_CONFIG", DEFAULT_CONFIG_PATH)
    selected = expand_path(str(selected), "config path")
    try:
        config = json.loads(Path(selected).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("local Pulse configuration could not be read as JSON") from exc
    object_fields(config, {*FIELDS, "version", "token_env"}, "config")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ConfigError("version must be integer 1")
    for section, fields in FIELDS.items():
        object_fields(config[section], fields, section)
    for field in FIELDS["user"]:
        string(config["user"][field], f"user.{field}")
    try:
        ZoneInfo(config["user"]["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("user.timezone must identify an IANA timezone") from exc
    targets = []
    for field in ("cards_target", "feed_target"):
        target = string(config["delivery"][field], f"delivery.{field}")
        match = re.fullmatch(r"telegram:(-100[1-9][0-9]*):([1-9][0-9]*)", target)
        if match is None:
            raise ConfigError(f"delivery.{field} must name a Telegram supergroup and positive thread")
        targets.append(match.groups())
    if targets[0][0] != targets[1][0] or targets[0][1] == targets[1][1]:
        raise ConfigError("delivery targets must name distinct threads in the same supergroup")
    for field in FIELDS["paths"] - {"python_executable"}:
        config["paths"][field] = expand_path(config["paths"][field], f"paths.{field}", nullable=field == "env_file")
    executable = string(config["paths"]["python_executable"], "paths.python_executable")
    executable = os.path.expanduser(os.path.expandvars(executable))
    if "$" in executable or executable.startswith("~"):
        raise ConfigError("paths.python_executable contains an unresolved variable")
    resolved = shutil.which(executable)
    if resolved is None:
        raise ConfigError("paths.python_executable must resolve to an executable")
    config["paths"]["python_executable"] = str(Path(resolved).absolute())
    sources = config["sources"]
    sources["personal_context_dir"] = expand_path(sources["personal_context_dir"], "sources.personal_context_dir", nullable=True)
    if not isinstance(sources["notes_roots"], list):
        raise ConfigError("sources.notes_roots must be an array of paths")
    sources["notes_roots"] = [expand_path(value, "sources.notes_roots item") for value in sources["notes_roots"]]
    for field in FIELDS["schedule"]:
        string(config["schedule"][field], f"schedule.{field}")
    if len(config["schedule"]["cron"].split()) != 5:
        raise ConfigError("schedule.cron must contain five cron fields")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", string(config["token_env"], "token_env")) is None:
        raise ConfigError("token_env must be an environment variable name")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=("check", "show"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))
    if args.command == "check":
        print("Configuration valid.")
    else:
        print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render private cronjob_manage create arguments from Pulse configuration."""
from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pulse_config import ConfigError, DEFAULT_CONFIG_PATH, load_config
import os

SKILL_DIR = Path(__file__).resolve().parent.parent


def build_job(config_path: Path, scheduler_timezone: str, image_skill: str) -> dict:
    config_path = config_path.expanduser().resolve()
    config = load_config(config_path)
    try:
        ZoneInfo(scheduler_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("scheduler timezone must identify an IANA timezone") from exc
    if scheduler_timezone != config["user"]["timezone"]:
        raise ConfigError("scheduler timezone must match user.timezone; verify the scheduler before deployment")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?", image_skill):
        raise ConfigError("image skill must be an installed skill name")
    if not Path(config["paths"]["workdir"]).is_dir():
        raise ConfigError("paths.workdir must be an existing directory")
    if image_skill == "codex-gpt-image" and config["paths"]["image_executor"] is None:
        raise ConfigError("codex-gpt-image requires paths.image_executor")
    values = {
        "job_name": config["schedule"]["job_name"],
        "cron": config["schedule"]["cron"],
        "workdir": config["paths"]["workdir"],
        "feed_target": config["delivery"]["feed_target"],
        "image_skill": image_skill,
    }
    job = json.loads((SKILL_DIR / "templates" / "cron_job.example.json").read_text(encoding="utf-8"))
    def resolve(value):
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            return values[value[2:-1]]
        return value
    job = resolve(job)
    job["prompt"] = (
        "Generate and publish the daily Personal Pulse. Load the attached skills. "
        f"The installed Pulse skill directory is {json.dumps(str(SKILL_DIR))}. "
        "Read templates/daily_pulse_cron_prompt.md there on each run. "
        f"Use the private configuration at {json.dumps(str(config_path))}; "
        f"set PULSE_CONFIG={shlex.quote(str(config_path))} for every shell invocation, "
        "including child processes. Run scripts/pulse_config.py check before collection. "
        f"Use the installed {image_skill} skill for image generation and follow "
        "references/image-production.md for the shared image contract. "
        "Treat resolved configuration and generated material as private local data. "
        "Preserve preference state and publication journals. Each invocation executes "
        "the authorized daily publication workflow: pulse_runtime.py send publishes "
        "cards and the linked feed to configured targets using the durable daily journal. "
        "The scheduler delivers the final run-status response. After reading back a "
        "complete matching journal return exactly [SILENT]; otherwise return concise "
        "PULSE_SEND_FAILED with the failed stage and concrete reader-safe reason."
    )
    return job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("PULSE_CONFIG", DEFAULT_CONFIG_PATH)))
    parser.add_argument("--scheduler-timezone", required=True, help="Verified effective scheduler IANA timezone")
    parser.add_argument("--image-skill", default="codex-gpt-image")
    args = parser.parse_args(argv)
    try:
        job = build_job(args.config, args.scheduler_timezone, args.image_skill)
    except ConfigError as exc:
        parser.error(str(exc))
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

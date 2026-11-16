#!/usr/bin/env python3
"""Isolated configuration and runtime-loading contracts."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pulse_config


def fixture_config(root: Path) -> dict:
    return {
        "version": 1,
        "user": {"name": "Synthetic Reader", "language": "en-GB", "timezone": "UTC"},
        "delivery": {"cards_target": "telegram:-1001234567890:301", "feed_target": "telegram:-1001234567890:302"},
        "paths": {
            "state_file": str(root / "state.json"), "journal_dir": str(root / "journals"),
            "scratch_dir": str(root / "scratch"), "workdir": str(root),
            "image_executor": str(root / "image.py"), "python_executable": sys.executable,
            "env_file": None,
        },
        "sources": {"personal_context_dir": None, "notes_roots": []},
        "schedule": {"cron": "30 7 * * *", "job_name": "synthetic-pulse"},
        "token_env": "SYNTHETIC_PULSE_TOKEN",
    }


def isolated_runtime(config_path: Path):
    """Execute a fresh module with scoped environment and no sys.modules entry."""
    spec = importlib.util.spec_from_file_location("isolated_pulse_runtime", Path(__file__).with_name("pulse_runtime.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"PULSE_CONFIG": str(config_path)}):
        spec.loader.exec_module(module)
    return module


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "config.json"
        self.config = fixture_config(self.root)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")

    def test_valid_config_and_nullable_sources(self):
        result = pulse_config.load_config(self.path)
        self.assertEqual(result, self.config)

    def test_missing_config_is_fatal_and_explicit_path_wins(self):
        with mock.patch.dict(os.environ, {"PULSE_CONFIG": str(self.root / "missing.json")}):
            with self.assertRaises(pulse_config.ConfigError):
                pulse_config.load_config()
            self.assertEqual(pulse_config.load_config(self.path), self.config)
            with self.assertRaises(pulse_config.ConfigError):
                isolated_runtime(self.root / "missing.json")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(pulse_config, "DEFAULT_CONFIG_PATH", self.root / "absent.json"):
            with self.assertRaises(pulse_config.ConfigError):
                pulse_config.load_config()

    def test_types_and_missing_fields(self):
        cases = [("version", True), ("version", "1"), ("user", []), ("paths", None), ("token_env", 4)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.config = fixture_config(self.root)
                self.config[key] = value
                self.save()
                with self.assertRaises(pulse_config.ConfigError):
                    pulse_config.load_config(self.path)
        for section, key, value in [("user", "name", 5), ("user", "timezone", "Invalid/Zone"),
                                    ("user", "language", ""), ("paths", "state_file", None),
                                    ("sources", "notes_roots", "notes"), ("sources", "notes_roots", [False]),
                                    ("schedule", "cron", 7)]:
            with self.subTest(section=section, key=key):
                self.config = fixture_config(self.root)
                self.config[section][key] = value
                self.save()
                with self.assertRaises(pulse_config.ConfigError):
                    pulse_config.load_config(self.path)
        self.config = fixture_config(self.root)
        del self.config["paths"]["env_file"]
        self.save()
        with self.assertRaises(pulse_config.ConfigError):
            pulse_config.load_config(self.path)

    def test_targets_reject_malformed_shared_and_cross_group_threads(self):
        for value in (None, True, "telegram:123:301", "telegram:-123:301", "telegram:-1001234567890",
                      "telegram:-1001234567890:0", "telegram:-1001234567890:-1", "telegram:-1001234567890:0301",
                      "telegram:-1001234567890:302", "telegram:-1009999999999:301", "telegram:-1001234567890:301:x"):
            with self.subTest(value=value):
                self.config["delivery"]["cards_target"] = value
                self.save()
                with self.assertRaises(pulse_config.ConfigError):
                    pulse_config.load_config(self.path)

    def test_unknown_fields_and_plaintext_token_are_rejected(self):
        for section in (None, *pulse_config.FIELDS):
            for key in ("unexpected", "token", "bot_token", "TELEGRAM_BOT_TOKEN"):
                self.config = fixture_config(self.root)
                (self.config if section is None else self.config[section])[key] = "synthetic-secret-value"
                self.save()
                with self.subTest(section=section, key=key), self.assertRaises(pulse_config.ConfigError):
                    pulse_config.load_config(self.path)
        self.config = fixture_config(self.root)
        self.config["token_env"] = "12345:synthetic-secret"
        self.save()
        with self.assertRaises(pulse_config.ConfigError):
            pulse_config.load_config(self.path)

    def test_expansion_executable_resolution_and_unresolved_variables(self):
        self.config["paths"].update(state_file="~/state.json", scratch_dir="${PULSE_TEST_ROOT}/scratch", python_executable=Path(sys.executable).name)
        self.config["sources"] = {"personal_context_dir": "$PULSE_TEST_ROOT/context", "notes_roots": ["~/notes"]}
        self.save()
        with mock.patch.dict(os.environ, {"HOME": str(self.root), "PULSE_TEST_ROOT": str(self.root), "PATH": str(Path(sys.executable).parent)}):
            result = pulse_config.load_config(self.path)
        self.assertEqual(result["paths"]["state_file"], str(self.root / "state.json"))
        self.assertEqual(result["sources"]["notes_roots"], [str(self.root / "notes")])
        self.assertEqual(result["paths"]["python_executable"], sys.executable)
        for value in ("$UNRESOLVED_PULSE_VARIABLE/file", "${UNRESOLVED_PULSE_VARIABLE}/file", "~no-such-pulse-user/file"):
            self.config = fixture_config(self.root)
            self.config["paths"]["state_file"] = value
            self.save()
            with self.assertRaises(pulse_config.ConfigError):
                pulse_config.load_config(self.path)
        self.config = fixture_config(self.root)
        self.config["paths"]["python_executable"] = "missing-pulse-python-command"
        self.save()
        with self.assertRaises(pulse_config.ConfigError):
            pulse_config.load_config(self.path)

    def test_cli_help_check_show_and_failure(self):
        script = str(Path(pulse_config.__file__))
        env = dict(os.environ, PULSE_CONFIG=str(self.root / "absent.json"))
        def run(*args):
            return subprocess.run([sys.executable, "-B", script, *args], env=env, capture_output=True, text=True)
        self.assertEqual(run("--help").returncode, 0)
        self.assertNotEqual(run("check").returncode, 0)
        checked = run("--config", str(self.path), "check")
        self.assertEqual((checked.returncode, checked.stdout), (0, "Configuration valid.\n"))
        shown = run("--config", str(self.path), "show")
        self.assertEqual(json.loads(shown.stdout), self.config)

    def test_alternate_runtime_and_import_environment_isolation(self):
        before = dict(os.environ)
        previous = sys.modules.get("pulse_runtime")
        self.config["user"] = {"name": "Alternate Synthetic Reader", "language": "fr-FR", "timezone": "Europe/Paris"}
        self.config["delivery"] = {"cards_target": "telegram:-1009876543210:401", "feed_target": "telegram:-1009876543210:402"}
        self.config["paths"]["scratch_dir"] = str(self.root / "other-scratch")
        self.save()
        runtime = isolated_runtime(self.path)
        self.assertEqual(os.environ, before)
        self.assertIs(sys.modules.get("pulse_runtime"), previous)
        normalized = runtime.normalize_pulse({"cards": []})
        self.assertEqual(normalized["scope"]["user"], self.config["user"]["name"])
        self.assertEqual(normalized["language"], "fr-FR")
        record = runtime.get_or_create_daily_target(runtime.DEFAULT_STATE_FILE, "2026-01-10")
        self.assertEqual(record["timezone"], "Europe/Paris")
        self.assertTrue(record["selected_at"].endswith(("+01:00", "+02:00")))
        runtime.validate_native_targets("telegram:-1009876543210:401", "telegram:-1009876543210:402")
        self.assertEqual(runtime.official_message_link(runtime.NATIVE_CARDS_TARGET, 9), "https://t.me/c/9876543210/401/9")
        self.assertEqual(runtime.APPROVED_COVER_ROOTS, (self.root / "other-scratch" / "hermes-pulse",))
        self.assertEqual(runtime.default_send_record("2026-01-10"), str(self.root / "journals" / "pulse_20260110.send.json"))

    def test_auth_uses_selected_variable_and_optional_env_file(self):
        runtime = isolated_runtime(self.path)
        self.assertEqual(runtime.load_env_file(), {})
        with mock.patch.dict(os.environ, {"SYNTHETIC_PULSE_TOKEN": "synthetic-token"}, clear=True):
            self.assertEqual(runtime.telegram_token(), "synthetic-token")
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "irrelevant-synthetic-token"}, clear=True):
            with self.assertRaises(SystemExit):
                runtime.telegram_token()
        env_path = self.root / "synthetic.env"
        env_path.write_text('SYNTHETIC_PULSE_TOKEN="file-synthetic-token"\n')
        self.config["paths"]["env_file"] = str(env_path)
        self.save()
        runtime = isolated_runtime(self.path)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime.telegram_token(), "file-synthetic-token")
        with mock.patch.dict(os.environ, {"SYNTHETIC_PULSE_TOKEN": "process-synthetic-token"}, clear=True):
            self.assertEqual(runtime.telegram_token(), "process-synthetic-token")


if __name__ == "__main__":
    unittest.main()

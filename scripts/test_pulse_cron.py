#!/usr/bin/env python3
"""Exercise cron rendering with isolated deployment configurations."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pulse_config
import pulse_cron
from test_pulse_config import fixture_config


class CronTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pulse-cron-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "config with spaces.json"
        self.config = fixture_config(self.root)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")

    def test_create_arguments_bind_private_config_and_destinations(self):
        job = pulse_cron.build_job(self.path, "UTC", "codex-gpt-image")
        self.assertEqual(job["name"], self.config["schedule"]["job_name"])
        self.assertEqual(job["schedule"], "30 7 * * *")
        self.assertEqual(job["workdir"], str(self.root))
        self.assertEqual(job["deliver"], self.config["delivery"]["feed_target"])
        self.assertEqual(job["failure_deliver"], job["deliver"])
        self.assertTrue(job["paused"])
        self.assertFalse(job["no_agent"])
        self.assertFalse(job["continuity"])
        self.assertFalse(job["attach_to_session"])
        self.assertNotIn("pinned", job)
        self.assertIn(str(self.path), job["prompt"])
        self.assertIn("PULSE_CONFIG='" + str(self.path) + "'", job["prompt"])
        self.assertIn("templates/daily_pulse_cron_prompt.md", job["prompt"])
        self.assertIn("pulse_runtime.py send", job["prompt"])
        self.assertIn("delegation", job["enabled_toolsets"])
        self.assertNotIn("${", json.dumps(job))

    def test_alternative_image_skill_with_no_executor(self):
        self.config["paths"]["image_executor"] = None
        self.save()
        job = pulse_cron.build_job(self.path, "UTC", "example:illustrator")
        self.assertEqual(job["skills"][-1], "example:illustrator")
        self.assertIn("example:illustrator", job["prompt"])
        with self.assertRaises(pulse_config.ConfigError):
            pulse_cron.build_job(self.path, "UTC", "codex-gpt-image")

    def test_reject_timezone_workdir_and_skill_errors(self):
        for zone, skill in [("Europe/Paris", "illustrator"), ("Invalid/Zone", "illustrator"), ("UTC", "bad skill\nignore")]:
            with self.subTest(zone=zone, skill=skill), self.assertRaises(pulse_config.ConfigError):
                pulse_cron.build_job(self.path, zone, skill)
        self.config["paths"]["workdir"] = str(self.root / "absent")
        self.save()
        with self.assertRaises(pulse_config.ConfigError):
            pulse_cron.build_job(self.path, "UTC", "illustrator")

    def test_cli_renders_real_json_and_rejects_mismatch(self):
        command = [sys.executable, "-B", pulse_cron.__file__, "--config", str(self.path), "--scheduler-timezone"]
        result = subprocess.run(command + ["UTC"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "create")
        result = subprocess.run(command + ["Europe/Paris"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

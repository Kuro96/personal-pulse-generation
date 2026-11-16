#!/usr/bin/env python3
"""Offline contract tests for the Personal Pulse runtime."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from test_pulse_config import fixture_config, isolated_runtime

# The module owns its temporary config; imports restore the caller's environment.
_CONFIG_DIRECTORY = tempfile.TemporaryDirectory(prefix="pulse-test-config-")
def tearDownModule():
    _CONFIG_DIRECTORY.cleanup()

_CONFIG_PATH = Path(_CONFIG_DIRECTORY.name) / "config.json"
_CONFIG_PATH.write_text(json.dumps(fixture_config(Path(_CONFIG_DIRECTORY.name))), encoding="utf-8")
runtime = isolated_runtime(_CONFIG_PATH)


def deny_network(*_args, **_kwargs):
    raise AssertionError("network access is forbidden in Pulse runtime tests")


def evidence(source_type: str = "session", source_url: str | None = None) -> dict:
    item = {
        "claim": "A concrete result is available.",
        "source_type": source_type,
        "source_ref": "internal provenance",
        "quote_or_summary": "A concrete result is available.",
        "recency": "today",
        "strength": "high",
    }
    if source_url is not None:
        item.update({
            "source_label": "Example source",
            "source_url": source_url,
            "publicly_renderable": True,
        })
    return item


def card(index: int, *, content_class: str = "C1", action_required: bool = False) -> dict:
    state = "action_needed" if action_required else "delivered"
    return {
        "id": f"pulse-card-{index:03d}",
        "title": f"Card {index}",
        "summary": f"Usable result {index}",
        "recommendation": f"Use the completed result {index}",
        "why_now": "It is current and useful.",
        "content_class": content_class,
        "time_horizon": "recent_2w",
        "event_key": f"event:{index}",
        "delivery": {
            "state": state,
            "user_action_required": action_required,
            "result_summary": f"Usable result {index}",
            "empty_result": False,
        },
        "feedback_context": {
            "mode": "natural_language_reply",
            "card_id": f"pulse-card-{index:03d}",
            "accepted_intents": ["follow", "completed", "mute_until", "boost_topic", "reduce_action_load"],
        },
        "evidence": [evidence()],
        "uncertainty": {"level": "low", "reason": "Verified locally", "what_would_reduce_uncertainty": "None"},
        "confidence": 0.9,
        "sensitivity": {"contains_private_context": False, "safe_to_share_publicly": True, "notes": "Public-safe"},
        "illustration_brief": {"enabled": True, "prompt": "Abstract editorial illustration", "style": "minimal", "avoid": []},
        "cover_image": {"status": "ready", "url": "https://images.example.org/card.png", "file": None, "alt": "Abstract cover"},
    }


def payload(count: int, *, pulse_day: str = "2026-07-22") -> dict:
    classes = ("C1", "C2", "C3", "C4")
    horizons = ("today_72h", "recent_2w", "long_term")
    cards = []
    for offset in range(count):
        item = card(offset + 1, content_class=classes[offset % len(classes)])
        item["tags"] = ["refresh"] if offset == 0 else []
        item["time_horizon"] = horizons[offset % len(horizons)]
        if item["content_class"] == "C4":
            item["evidence"] = [
                evidence("external_source", f"https://example.org/public/card-{offset + 1}")
            ]
        cards.append(item)
    return {
        "pulse_version": "1.1",
        "pulse_day": pulse_day,
        "daily_target": {"count": count, "timezone": "UTC"},
        "feed_cover_image": {
            "status": "ready",
            "url": "https://images.example.org/pulse-collage.png",
            "file": None,
            "alt": "Uncut Pulse card collage",
        },
        "scope": {
            "requested_card_count": count,
            "content_classes_considered": ["C1", "C2", "C3", "C4"],
            "time_horizons_considered": ["today_72h", "recent_2w", "long_term"],
        },
        "cards": cards,
    }


def reduced_fixture(actual: int, desired: int) -> dict:
    """Build a reduced edition with explicit coverage exceptions."""
    value = payload(actual)
    value["generated_at"] = "2026-07-22T00:00:00+00:00"
    value["daily_target"]["count"] = desired
    value["scope"].update(requested_card_count=desired, selected_card_count=actual)
    missing = runtime.TIME_HORIZONS - {item["time_horizon"] for item in value["cards"]}
    value["scope"]["selection_exceptions"] = [
        {"type": "missing_horizon", "value": horizon, "reason": f"Offline fixture has no {horizon} card."}
        for horizon in sorted(missing)
    ]
    value["global_uncertainties"] = [item["reason"] for item in value["scope"]["selection_exceptions"]]
    if actual == 1:
        value["cards"][0]["cover_image"] = dict(value["feed_cover_image"])
    return value


def write_state(path: Path, day: str, count: int, **extra) -> None:
    state = {
        "legacy_preference": "keep-me",
        "daily_card_targets": {
            day: {
                "count": count,
                "timezone": "UTC",
                "selected_at": f"{day}T08:00:00+00:00",
                "random_function": "secrets.randbelow",
            }
        },
    }
    state.update(extra)
    path.write_text(json.dumps(state), encoding="utf-8")


CARDS_TARGET = "telegram:-1001234567890:301"
FEED_TARGET = "telegram:-1001234567890:302"


def native_pulse(count: int) -> dict:
    value = payload(count)
    value.pop("feed_cover_image", None)
    for index, item in enumerate(value["cards"], start=1):
        item["feed_copy"] = {
            "transition_lead": f"FORCED_TRANSITION_{index}",
            "card_teaser": f"Teaser {index}: " + "😀<&>" * 420,
            "cover_alt": f"Cover {index}",
        }
        item["cover_image"] = {
            "status": "force_majeure_text_fallback",
            "url": None,
            "file": None,
            "alt": f"Cover {index}",
            "reason": "Codex image generation hit an authorization, rate-limit, or service blocker.",
        }
    return runtime.normalize_pulse(value, tools_target=CARDS_TARGET)


class OfflineRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir=runtime.scratch_root())
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.enterContext(mock.patch.object(socket, "socket", side_effect=deny_network))


class StatefulRuntimeTest(OfflineRuntimeTest):
    def setUp(self) -> None:
        super().setUp()
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "send.json"
        write_state(self.state_path, "2026-07-22", 6)

    def validate(self, value: dict) -> dict:
        return runtime.normalize_pulse(
            value, strict_count=True, pulse_day="2026-07-22", state_file=str(self.state_path),
        )

    def send(self, value: dict, **options) -> dict:
        arguments = dict(
            pulse_day="2026-07-22", state_file=str(self.state_path),
            send_record=str(self.journal_path), cards_target=CARDS_TARGET,
            feed_target=FEED_TARGET, dry_run=True,
        )
        return runtime.send_details_and_feed(value, **(arguments | options))


class ManagedScratchTests(OfflineRuntimeTest):
    def test_alternate_config_cli_defaults_and_digest(self) -> None:
        config = fixture_config(self.root)
        config["user"] = {"name": "Second Synthetic Reader", "language": "de-DE", "timezone": "Europe/Berlin"}
        config["delivery"] = {"cards_target": "telegram:-1009876543210:401", "feed_target": "telegram:-1009876543210:402"}
        config["paths"]["scratch_dir"] = str(self.root)
        config_path = self.root / "alternate.json"
        config_path.write_text(json.dumps(config))
        alternate = isolated_runtime(config_path)
        state = Path(alternate.DEFAULT_STATE_FILE)
        write_state(state, "2026-07-22", 6)
        saved = json.loads(state.read_text())
        saved["daily_card_targets"]["2026-07-22"]["timezone"] = "Europe/Berlin"
        state.write_text(json.dumps(saved))
        value = reduced_fixture(3, 6)
        value["daily_target"]["timezone"] = "Europe/Berlin"
        source = self.root / "alternate-pulse.json"
        source.write_text(json.dumps(value))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(alternate.main(["send", str(source), "--pulse-day", "2026-07-22", "--dry-run"]), 0)
        journal = json.loads(Path(alternate.default_send_record("2026-07-22")).read_text())
        self.assertEqual(journal, json.loads(output.getvalue()))
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["cards_target"], config["delivery"]["cards_target"])
        self.assertEqual(journal["feed_target"], config["delivery"]["feed_target"])
        self.assertTrue(all(ref.startswith("https://t.me/c/9876543210/401/") for ref in journal["detail_refs"].values()))
        normalized = alternate.normalize_pulse(value, tools_target=alternate.NATIVE_CARDS_TARGET)
        self.assertEqual(normalized["language"], "de-DE")
        self.assertEqual(journal["payload_sha256"], alternate.pulse_payload_sha256(normalized))

    def test_configured_preflight_and_offline_cli(self) -> None:
        # Exercise configured media policy with a real 1x1 PNG.
        runtime.PULSE_RUN_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="offline-preflight-", dir=runtime.PULSE_RUN_ROOT) as directory:
            root = Path(directory)
            cover = root / "valid.png"
            cover.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
            ))
            runtime.validate_local_cover_file(str(cover))
            bad = root / "malformed.png"
            bad.write_bytes(b"not an image")
            link = root / "link.png"
            link.symlink_to(cover)
            directory_link = root / "linked-directory"
            directory_link.symlink_to(root, target_is_directory=True)
            outside = self.root / "outside.png"
            outside.write_bytes(cover.read_bytes())
            with tempfile.TemporaryDirectory(prefix="hermes-pulse-sibling-", dir=runtime.scratch_root()) as sibling:
                sibling_cover = Path(sibling) / "valid.png"
                sibling_cover.write_bytes(cover.read_bytes())
                for rejected in (Path("/tmp/hermes-pulse/old.png"), outside, sibling_cover,
                                 link, directory_link / "valid.png", bad, root / ".." / "valid.png"):
                    with self.subTest(path=str(rejected)), self.assertRaises(SystemExit):
                        runtime.validate_local_cover_file(str(rejected))
            pulse = payload(6)
            for item in [*pulse["cards"], pulse]:
                field = "cover_image" if "cover_image" in item else "feed_cover_image"
                item[field].update({"url": None, "file": str(cover)})
            runtime.validate_live_native_covers(pulse)
            state = self.root / "state.json"
            journal = self.root / "send.json"
            source = self.root / "pulse.json"
            write_state(state, "2026-07-22", 6)
            source.write_text(json.dumps(pulse))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = runtime.main([
                    "send", str(source), "--dry-run", "--pulse-day", "2026-07-22",
                    "--state-file", str(state), "--send-record", str(journal),
                    "--cards-target", CARDS_TARGET, "--feed-target", FEED_TARGET,
                ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "complete")
            saved = json.loads(journal.read_text())
            self.assertEqual(len(saved["details"]), 6)
            self.assertEqual(saved["feed_message_id"], 7)

    def test_isolated_process_config_and_environment_independence(self) -> None:
        env = dict(os.environ, PULSE_CONFIG=str(_CONFIG_PATH))
        for setting in (str(self.root), "relative-temp", str(self.root / "missing")):
            child_env = dict(env, TMPDIR=setting, HERMES_HOME=str(self.root / "profile"))
            result = subprocess.run(
                [sys.executable, "-B", "-c", "import pulse_runtime as p; print(p.PULSE_RUN_ROOT)"],
                cwd=Path(runtime.__file__).parent, env=child_env, text=True, capture_output=True, check=True,
            )
            with self.subTest(setting=setting):
                self.assertEqual(result.stdout.strip(), str(runtime.PULSE_RUN_ROOT))


class RuntimeStateTests(OfflineRuntimeTest):
    def test_feedback_is_validated_and_atomically_appended(self) -> None:
        state_path = self.root / "state.json"
        write_state(state_path, "2026-07-22", 6, legacy_keyboard={"choice": "keep"})
        recorded = runtime.record_card_feedback(
            str(state_path),
            pulse_day="2026-07-22",
            card_id="pulse-card-001",
            intent="completed",
            text="Finished this",
            event_key="event:1",
        )
        self.assertEqual(recorded["intent"], "completed")
        self.assertEqual(recorded["raw_text"], "Finished this")
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["legacy_keyboard"], {"choice": "keep"})
        self.assertEqual(saved["card_feedback"], [recorded])
        with self.assertRaises(SystemExit):
            runtime.record_card_feedback(
                str(state_path), pulse_day="2026-07-22", card_id="pulse-card-001", intent="guessed", text="raw"
            )

    def test_feedback_can_resolve_card_and_day_from_send_journal(self) -> None:
        state_path = self.root / "state.json"
        journal_path = self.root / "send.json"
        runtime.write_send_journal(str(journal_path), {
            "pulse_day": "2026-07-22",
            "details": {"pulse-card-001": 101, "pulse-card-002": 102},
            "feed_message_id": 200,
        })
        recorded = runtime.record_card_feedback(
            str(state_path),
            intent="follow",
            text="Keep following this",
            send_journal=str(journal_path),
            detail_message_id=102,
        )
        self.assertEqual((recorded["pulse_day"], recorded["card_id"]), ("2026-07-22", "pulse-card-002"))


class RuntimeSchemaTests(StatefulRuntimeTest):
    def test_payload_target_and_requested_count_cannot_override_state(self) -> None:
        for mutation in ("daily_target", "scope"):
            value = payload(6)
            if mutation == "daily_target":
                value["daily_target"]["count"] = 7
            else:
                value["scope"]["requested_card_count"] = 7
            with self.subTest(mutation=mutation), self.assertRaises(SystemExit):
                self.validate(value)

    def test_content_and_delivery_contract_is_required(self) -> None:
        required_paths = ["content_class", "time_horizon", "event_key", "delivery", "feedback_context"]
        for field in required_paths:
            value = payload(6)
            del value["cards"][0][field]
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.validate(value)
        value = payload(6)
        value["cards"][0]["delivery"] = {
            "state": "delivered", "user_action_required": True, "result_summary": "Mismatch"
        }
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_card_ids_are_nonempty_unique_and_match_feedback_association(self) -> None:
        invalid_values = []

        duplicate = payload(6)
        duplicate["cards"][1]["id"] = duplicate["cards"][0]["id"]
        duplicate["cards"][1]["feedback_context"]["card_id"] = duplicate["cards"][0]["id"]
        invalid_values.append(("duplicate", duplicate))

        for missing_id in (None, "", "   "):
            value = payload(6)
            if missing_id is None:
                del value["cards"][0]["id"]
            else:
                value["cards"][0]["id"] = missing_id
            invalid_values.append((f"empty-{missing_id!r}", value))

        mismatch = payload(6)
        mismatch["cards"][0]["feedback_context"]["card_id"] = "pulse-card-999"
        invalid_values.append(("feedback-mismatch", mismatch))

        for case, value in invalid_values:
            with self.subTest(case=case), self.assertRaises(SystemExit):
                self.validate(value)

    def test_more_than_two_user_action_cards_fails(self) -> None:
        value = payload(6)
        value["cards"][:3] = [card(index, action_required=True) for index in range(1, 4)]
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_external_citations_require_safe_https_urls(self) -> None:
        unsafe_urls = [
            None,
            "http://example.org/item",
            "file:///home/synthetic-reader/private.txt",
            "https://localhost/item",
            "https://service.internal/item",
            "https://127.0.0.1/item",
            "https://10.2.3.4/item",
            "https://user:pass@example.org/item",
            "https://example.org/item?api_key=secret",
            "https://example.org/item?signature=secret",
            "https://example.org/item?x.amz.signature=secret",
            "https://example.org/item?X_Goog_Credential=secret",
            "https://example.org/item?ACCESS.TOKEN=secret",
        ]
        for url in unsafe_urls:
            value = payload(6)
            value["cards"][0]["content_class"] = "C4"
            value["cards"][0]["evidence"] = [evidence("external_source", url)]
            with self.subTest(url=url), self.assertRaises(SystemExit):
                self.validate(value)
        value = payload(6)
        value["cards"][0]["content_class"] = "C4"
        value["cards"][0]["evidence"] = [evidence("external_source", "https://example.org/public?id=42")]
        self.assertEqual(self.validate(value)["cards"][0]["evidence"][0]["source_url"], "https://example.org/public?id=42")

    def test_ready_generated_and_available_covers_require_a_reference(self) -> None:
        for status in ("ready", "generated", "available"):
            value = payload(6)
            value["cards"][0]["cover_image"].update({"status": status, "url": "  ", "file": None})
            with self.subTest(status=status), self.assertRaises(SystemExit):
                self.validate(value)

    def test_media_less_cover_requires_an_approved_status_and_nonempty_reason(self) -> None:
        for status in ("force_majeure_text_fallback", "user_opt_out_text_fallback"):
            value = payload(6)
            value["cards"][0]["cover_image"].update({
                "status": status,
                "url": None,
                "file": None,
                "reason": "Explicitly approved text-only delivery.",
            })
            with self.subTest(status=status):
                self.validate(value)

        invalid_cases = [
            ("force_majeure_text_fallback", ""),
            ("user_opt_out_text_fallback", "   "),
            ("pending", "Waiting for generation."),
            ("failed", "Generation failed."),
            ("stalled", "Generation stalled."),
            ("unknown", "No media state is known."),
        ]
        for status, reason in invalid_cases:
            value = payload(6)
            value["cards"][0]["cover_image"].update({
                "status": status,
                "url": None,
                "file": None,
                "reason": reason,
            })
            with self.subTest(status=status, reason=reason), self.assertRaises(SystemExit):
                self.validate(value)

    def test_reader_visible_local_paths_fail(self) -> None:
        value = payload(6)
        value["cards"][0]["summary"] = "Read /home/synthetic-reader/private/notes.md"
        with self.assertRaises(SystemExit):
            self.validate(value)


class RuntimeCompatibilityTests(OfflineRuntimeTest):
    def test_schema_1_0_normalizes_without_phase_1_fields(self) -> None:
        legacy = {
            "pulse_version": "1.0",
            "cards": [{"title": "Legacy card", "summary": "Still readable", "evidence": ["Legacy evidence"]}],
        }
        normalized = runtime.normalize_pulse(legacy)
        self.assertEqual(normalized["pulse_version"], "1.0")
        self.assertEqual(normalized["cards"][0]["title"], "Legacy card")
        self.assertIn("follow_up", normalized)

    def test_legacy_send_journal_is_normalized(self) -> None:
        legacy = runtime.normalize_send_journal({
            "details": {"pulse-card-001": 100},
            "feed_message_id": 101,
            "feed_telegraph": {"url": "https://telegra.ph/legacy"},
        })
        self.assertEqual(legacy["delivery_mode"], "legacy_telegraph_v1")
        self.assertEqual(legacy["status"], "complete")


class SelectionContractTests(StatefulRuntimeTest):
    def test_duplicate_event_keys_fail(self) -> None:
        value = payload(6)
        value["cards"][1]["event_key"] = value["cards"][0]["event_key"]
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_empty_result_cards_fail(self) -> None:
        value = payload(6)
        value["cards"][0]["delivery"]["empty_result"] = True
        value["cards"][0]["delivery"]["result_summary"] = "No change was found."
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_recency_only_repeat_requires_qualifying_novelty(self) -> None:
        value = payload(6)
        value["cards"][0]["recent_topic_repeat"] = True
        value["cards"][0]["novelty_reason"] = "discussed_recently"
        with self.assertRaises(SystemExit):
            self.validate(value)

        for reason in ("new_information", "new_state", "current_relevance",
                       "completed_deliverable", "real_time_window", "unresolved_decision"):
            qualified = payload(6)
            qualified["cards"][0]["recent_topic_repeat"] = True
            qualified["cards"][0]["novelty_reason"] = reason
            with self.subTest(reason=reason):
                self.validate(qualified)

    def test_refresh_prefix_is_removed_without_losing_internal_tag(self) -> None:
        for prefix in ("Refresh: ", "Refresh：", "[Refresh] "):
            value = payload(6)
            value["cards"][0]["title"] = prefix + "A useful discovery"
            value["cards"][0]["tags"] = ["refresh", "design"]
            with self.subTest(prefix=prefix):
                normalized = self.validate(value)
                self.assertEqual(normalized["cards"][0]["title"], "A useful discovery")
                self.assertEqual(normalized["cards"][0]["tags"], ["refresh", "design"])

        natural = payload(6)
        natural["cards"][0]["title"] = "A refresh rate comparison"
        self.assertEqual(self.validate(natural)["cards"][0]["title"], "A refresh rate comparison")

    def test_default_selection_allows_refresh_shortfall_with_reader_note(self) -> None:
        valid = payload(6)
        self.validate(valid)

        missing = payload(6)
        missing["cards"][0]["tags"] = []
        missing["scope"]["refresh_status"] = {
            "desired_count": 1,
            "selected_count": 0,
            "note": "外部检索未获得达到质量门槛的新内容，今天用其他高质量卡片补齐。",
        }
        self.validate(missing)

        missing_note = payload(6)
        missing_note["cards"][0]["tags"] = []
        with self.assertRaises(SystemExit):
            self.validate(missing_note)

        extra = payload(6)
        extra["cards"][1]["tags"] = ["refresh"]
        self.validate(extra)

    def test_refresh_preference_allows_extra_qualified_cards(self) -> None:
        write_state(self.state_path, "2026-07-22", 6, last_preference="Refresh 多一点")
        value = reduced_fixture(3, 6)
        for item in value["cards"]:
            item["tags"] = ["refresh"]
            item["evidence"] = [evidence("external_source", f"https://example.org/{item['id']}")]
        value["scope"]["refresh_status"] = {"desired_count": 2, "selected_count": 3, "note": ""}
        normalized = self.validate(value)
        self.assertEqual(len(normalized["cards"]), 3)
        self.assertEqual(runtime.native_feed_refresh_note(normalized), "")

    def test_refresh_status_counts_require_exact_integers(self) -> None:
        value = payload(6)
        status = {"desired_count": 1, "selected_count": 1, "note": ""}
        value["scope"]["refresh_status"] = status
        for field in ("desired_count", "selected_count"):
            for invalid in (True, 1.0, "1", None, 2):
                with self.subTest(field=field, invalid=invalid):
                    status[field] = invalid
                    with self.assertRaises(SystemExit):
                        self.validate(value)
                    status[field] = 1

    def test_image_run_requires_a_usable_uncut_feed_cover(self) -> None:
        missing = payload(6)
        missing.pop("feed_cover_image")
        with self.assertRaises(SystemExit):
            self.validate(missing)

        unusable = payload(6)
        unusable["feed_cover_image"].update({"url": None, "file": None})
        with self.assertRaises(SystemExit):
            self.validate(unusable)

    def test_fixed_feedback_menu_fails(self) -> None:
        value = payload(6)
        value["follow_up"] = {"question": "Choose one", "choices": ["Follow", "Mute"]}
        with self.assertRaises(SystemExit):
            self.validate(value)

        value = payload(6)
        value["cards"][0]["feedback_context"]["buttons"] = ["Follow", "Mute"]
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_class_and_horizon_coverage_metadata_is_required(self) -> None:
        for field in ("content_classes_considered", "time_horizons_considered"):
            value = payload(6)
            del value["scope"][field]
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.validate(value)

        value = payload(6)
        value["scope"]["content_classes_considered"] = ["C1", "C2", "C3"]
        with self.assertRaises(SystemExit):
            self.validate(value)

        value = payload(6)
        value["scope"]["time_horizons_considered"] = ["today_72h", "recent_2w"]
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_class_concentration_requires_an_exact_recorded_exception(self) -> None:
        value = payload(6)
        value["cards"][1]["content_class"] = "C1"
        value["cards"][2]["content_class"] = "C1"
        reason = "Four selected cards are C1 because their deadlines coincide."

        with self.assertRaises(SystemExit):
            self.validate(value)

        value["scope"]["selection_exceptions"] = [{
            "type": "class_concentration",
            "value": "C1",
            "reason": reason,
        }]
        value["global_uncertainties"] = [reason]
        self.assertEqual(
            self.validate(value)["scope"]["selection_exceptions"][0]["value"],
            "C1",
        )

        value["global_uncertainties"] = [reason + " Not an exact record."]
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_each_missing_horizon_requires_an_exact_recorded_exception(self) -> None:
        value = payload(6)
        for item in value["cards"]:
            if item["time_horizon"] == "long_term":
                item["time_horizon"] = "recent_2w"
        reason = "No eligible long-term event had enough current evidence."

        with self.assertRaises(SystemExit):
            self.validate(value)

        value["scope"]["selection_exceptions"] = [{
            "type": "missing_horizon",
            "value": "long_term",
            "reason": reason,
        }]
        value["global_uncertainties"] = [reason]
        self.validate(value)

        value["scope"]["selection_exceptions"][0]["value"] = "today_72h"
        with self.assertRaises(SystemExit):
            self.validate(value)

    def test_selection_exceptions_reject_malformed_duplicate_unknown_and_unnecessary_entries(self) -> None:
        reason = "Exception reason recorded globally."
        invalid_exceptions = {
            "not-a-list": "class_concentration",
            "not-an-object": ["class_concentration"],
            "missing-field": [{"type": "class_concentration", "value": "C1"}],
            "extra-field": [{"type": "class_concentration", "value": "C1", "reason": reason, "extra": True}],
            "blank-reason": [{"type": "class_concentration", "value": "C1", "reason": "  "}],
            "unknown-type": [{"type": "other", "value": "C1", "reason": reason}],
            "unknown-class": [{"type": "class_concentration", "value": "C9", "reason": reason}],
            "wrong-horizon-subject": [{"type": "missing_horizon", "value": "C1", "reason": reason}],
            "unnecessary-class": [{"type": "class_concentration", "value": "C1", "reason": reason}],
            "unnecessary-horizon": [{"type": "missing_horizon", "value": "long_term", "reason": reason}],
            "duplicate": [
                {"type": "class_concentration", "value": "C1", "reason": reason},
                {"type": "class_concentration", "value": "C1", "reason": reason},
            ],
        }
        for case, exceptions in invalid_exceptions.items():
            value = payload(6)
            if case == "duplicate":
                value["cards"][1]["content_class"] = "C1"
                value["cards"][2]["content_class"] = "C1"
            value["scope"]["selection_exceptions"] = exceptions
            value["global_uncertainties"] = [reason]
            with self.subTest(case=case), self.assertRaises(SystemExit):
                self.validate(value)


class TelegramRenderTests(StatefulRuntimeTest):
    def test_native_cards_target_requires_configured_topic(self) -> None:
        for target in ("not-a-hermes-target", "telegram:-1001234567890",
                       "telegram:-1009999999999:301", FEED_TARGET):
            with self.subTest(target=target), self.assertRaises(SystemExit):
                self.send(payload(6), cards_target=target)
            self.assertFalse(self.journal_path.exists())

    def test_native_feed_target_requires_configured_topic(self) -> None:
        for target in (CARDS_TARGET, "telegram:-1009999999999:302"):
            with self.subTest(target=target), self.assertRaises(SystemExit):
                self.send(payload(6), feed_target=target)
            self.assertFalse(self.journal_path.exists())

    def test_card_detail_target_must_match_send_target(self) -> None:
        value = payload(6)
        value["cards"][0]["detail_message"] = {"target": FEED_TARGET}
        with self.assertRaises(SystemExit):
            self.send(value)
        self.assertFalse(self.journal_path.exists())

    def test_official_group_topic_links_use_documented_t_me_c_shape(self) -> None:
        self.assertEqual(
            runtime.official_message_link(CARDS_TARGET, 1601),
            "https://t.me/c/1234567890/301/1601",
        )
        self.assertIsNone(runtime.official_message_link("telegram:123456789:999999", 1603))

    def test_eight_card_text_feed_is_bounded_and_links_every_title(self) -> None:
        pulse = native_pulse(8)
        refs = {item["id"]: f"https://t.me/c/1234567890/301/{1600 + index}"
                for index, item in enumerate(pulse["cards"], 1)}
        rendered = runtime.render_feed_html(pulse, detail_refs=refs)
        self.assertLessEqual(runtime.utf16_units(rendered), 3900)
        for item in pulse["cards"]:
            self.assertIn(f'<a href="{refs[item["id"]]}">{item["title"]}</a>', rendered)
        self.assertNotIn("FORCED_TRANSITION_", rendered)
        self.assertNotIn("telegra.ph", rendered)

    def test_eight_card_photo_feed_caption_is_bounded_and_links_every_title(self) -> None:
        pulse = native_pulse(8)
        refs = {
            item["id"]: runtime.official_message_link(CARDS_TARGET, 1800 + index)
            for index, item in enumerate(pulse["cards"], start=1)
        }

        rendered = runtime.render_feed_html(pulse, detail_refs=refs, max_units=1000)

        self.assertLessEqual(runtime.utf16_units(rendered), 1000)
        self.assertIn("…", rendered)
        for block in rendered.split("\n\n")[1:]:
            if "Teaser " in block:
                self.assertTrue(block.endswith("…"), block)
        for item in pulse["cards"]:
            escaped_title = runtime.html_escape(runtime.as_text(item["title"]))
            self.assertEqual(rendered.count(escaped_title), 1)
            self.assertIn(f'<a href="{refs[item["id"]]}">{escaped_title}</a>', rendered)

    def test_native_feed_explains_refresh_shortfall(self) -> None:
        pulse = native_pulse(6)
        pulse["cards"][0]["tags"] = []
        pulse["scope"]["refresh_status"] = {
            "desired_count": 1,
            "selected_count": 0,
            "note": "外部检索未获得达到质量门槛的新内容，今天用其他高质量卡片补齐。",
        }

        rendered = runtime.render_feed_html(pulse)

        self.assertIn("今日新鲜内容：外部检索未获得达到质量门槛的新内容", rendered)

    def test_native_feed_explains_user_opt_out_cover_fallback(self) -> None:
        pulse = native_pulse(6)
        for item in pulse["cards"]:
            item["cover_image"]["status"] = "user_opt_out_text_fallback"

        rendered = runtime.render_feed_html(pulse)

        self.assertIn("配图：本次按设置省略。", rendered)
        self.assertNotIn("配图：本次因授权、限流或服务阻断未生成。", rendered)
        self.assertLessEqual(runtime.utf16_units(rendered), 3900)

    def test_native_feed_adds_no_cover_note_when_all_covers_are_ready(self) -> None:
        pulse = native_pulse(6)
        for index, item in enumerate(pulse["cards"], start=1):
            item["cover_image"].update({
                "status": "ready",
                "file": str(runtime.PULSE_RUN_ROOT / f"20260722/test-run/cover-{index}.png"),
                "url": None,
            })

        rendered = runtime.render_feed_html(pulse)

        self.assertNotIn("配图：", rendered)
        self.assertLessEqual(runtime.utf16_units(rendered), 3900)

    def test_full_detail_keeps_delivery_result_summary_and_safe_public_citation(self) -> None:
        item = native_pulse(6)["cards"][0]
        item["summary"] = "Optional context " + "😀<&>" * 350
        item["delivery"]["result_summary"] = "DELIVERY_RESULT_ALREADY_AVAILABLE"
        item["evidence"] = [{
            **evidence("external_source", "https://example.org/public/report?id=42"),
            "claim": "Published evidence supports the result.",
            "source_label": "Public report",
            "source_ref": "/home/synthetic-reader/private/internal-provenance.md",
        }]

        rendered = runtime.render_detail_photo_caption(item)

        self.assertIn("DELIVERY_RESULT_ALREADY_AVAILABLE", rendered)
        self.assertIn('<a href="https://example.org/public/report?id=42">', rendered)
        self.assertIn("公开来源：example.org", rendered)
        self.assertNotIn("Public report", rendered)
        self.assertNotIn("internal-provenance", rendered)
        self.assertNotIn("/home/synthetic-reader", rendered)
        self.assertIn(runtime.html_escape(item["summary"]), rendered)
        self.assertGreater(runtime.utf16_units(rendered), 1000)

    def test_full_detail_keeps_all_safe_public_citations_without_budget_slicing(self) -> None:
        item = native_pulse(6)["cards"][0]
        over_budget_url = "https://example.org/" + "😀<&>" * 100
        fallback_url = "https://www.nasa.gov/public/report"
        item["evidence"] = [
            evidence("external_source", over_budget_url),
            evidence("external_source", fallback_url),
        ]

        rendered = runtime.render_detail_photo_caption(item)

        self.assertIn(runtime.html_attribute_escape(over_budget_url), rendered)
        self.assertIn(f'<a href="{fallback_url}">', rendered)
        self.assertIn("公开来源：example.org", rendered)
        self.assertIn("公开来源：www.nasa.gov", rendered)

    def test_reader_renderers_hide_provenance_paths_and_raw_citation_labels(self) -> None:
        pulse = native_pulse(6)
        item = pulse["cards"][0]
        item["summary"] = "Reader summary from /home/synthetic-reader/private/summary.md"
        item["recommendation"] = "Use /home/operator/private-plan.txt"
        item["why_now"] = "Checked file:///tmp/private-status.json"
        item["delivery"]["result_summary"] = "Delivered from /root/private/result.txt"
        item["illustration_brief"]["prompt"] = "RAW_ILLUSTRATION_PROVENANCE /tmp/private-prompt.txt"
        item["evidence"] = [{
            **evidence("external_source", "https://example.org/public/report?id=42"),
            "claim": "RAW_EVIDENCE_CLAIM",
            "quote_or_summary": "RAW_PROVENANCE_SUMMARY",
            "source_label": "RAW_SOURCE_LABEL",
            "source_ref": "/home/synthetic-reader/private/RAW_SOURCE_REF.md",
        }]
        pulse["global_uncertainties"] = ["RAW_GLOBAL_PROVENANCE /tmp/private-feed-note.txt"]

        render_dir = self.root / "rendered-details"
        input_path = self.root / "pulse.json"
        input_path.write_text(json.dumps(pulse), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.main(["render-details", str(input_path), "--out-dir", str(render_dir)])

        outputs = {
            "native-caption": runtime.render_detail_photo_caption(item),
            "compatibility-html": runtime.render_detail_html(item),
            "compatibility-markdown": runtime.render_detail(item),
            "render-details": (render_dir / f"{item['id']}.md").read_text(encoding="utf-8"),
        }
        forbidden = [
            "/home/synthetic-reader",
            "/home/operator",
            "file:///tmp",
            "/root/private",
            "/tmp/private",
            "RAW_EVIDENCE_CLAIM",
            "RAW_PROVENANCE_SUMMARY",
            "RAW_SOURCE_LABEL",
            "RAW_SOURCE_REF",
            "RAW_ILLUSTRATION_PROVENANCE",
            "RAW_GLOBAL_PROVENANCE",
        ]
        for surface, rendered in outputs.items():
            for marker in forbidden:
                with self.subTest(surface=surface, marker=marker):
                    self.assertNotIn(marker, rendered)

        for surface in ("native-caption", "compatibility-html", "compatibility-markdown", "render-details"):
            with self.subTest(surface=surface):
                self.assertIn("https://example.org/public/report?id=42", outputs[surface])
                self.assertIn("公开来源：example.org", outputs[surface])


class ReducedEditionTests(StatefulRuntimeTest):
    def test_actual_count_controls_concentration_and_horizon_exceptions(self) -> None:
        value = reduced_fixture(3, 6)
        for item in value["cards"]:
            item["content_class"] = "C1"
        with self.assertRaises(SystemExit):
            self.send(value)
        reason = "Offline fixture has three independently eligible C1 results."
        value["scope"]["selection_exceptions"].append(
            {"type": "class_concentration", "value": "C1", "reason": reason}
        )
        value["global_uncertainties"].append(reason)
        self.assertEqual(self.send(value)["status"], "complete")
        single = reduced_fixture(1, 6)
        single["scope"]["selection_exceptions"] = []
        with self.assertRaises(SystemExit):
            runtime.normalize_pulse(single, strict_count=True, pulse_day="2026-07-22", state_file=str(self.state_path))

    def test_one_card_image_is_shared_and_refresh_remains_best_effort(self) -> None:
        value = reduced_fixture(1, 6)
        value["cards"][0]["cover_image"]["url"] = "https://images.example.org/different.png"
        with self.assertRaises(SystemExit):
            self.send(value)
        value = reduced_fixture(1, 6)
        value["cards"][0]["tags"] = []
        write_state(self.state_path, "2026-07-22", 6, last_preference="Refresh 多一点")
        with self.assertRaises(SystemExit):
            self.send(value)
        note = "Offline test: external discovery yielded no qualifying refresh material."
        value["scope"]["refresh_status"] = {"desired_count": 2, "selected_count": 0, "note": note}
        journal = self.send(value)
        normalized = runtime.normalize_pulse(value)
        self.assertEqual(runtime.card_cover_ref(normalized["cards"][0]), runtime.feed_cover_ref(normalized))
        feed = runtime.render_feed_html(normalized, detail_refs=journal["detail_refs"])
        self.assertEqual(feed.count(note), 1)

    def test_historical_full_size_payload_digest_and_journal_resume(self) -> None:
        value = runtime.normalize_pulse(payload(6), tools_target=CARDS_TARGET)
        digest = runtime.pulse_payload_sha256(value)
        checked = runtime.normalize_pulse(value, strict_count=True, tools_target=CARDS_TARGET,
                                          pulse_day="2026-07-22", state_file=str(self.state_path))
        self.assertEqual(runtime.pulse_payload_sha256(checked), digest)
        self.assertNotIn("selected_card_count", checked["scope"])
        self.assertNotIn("follow_up", checked)
        old = runtime.native_send_journal(value, pulse_day="2026-07-22", cards_target=CARDS_TARGET, feed_target=FEED_TARGET)
        del old["desired_card_count"]
        old["historical_test_marker"] = "preserve"
        runtime.write_send_journal(str(self.journal_path), old)
        self.assertEqual(runtime.load_send_journal(str(self.journal_path)), old)
        completed = self.send(value)
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["historical_test_marker"], "preserve")
        self.assertNotIn("desired_card_count", completed)
        with self.assertRaises(SystemExit):
            self.send(value)

    def test_reduced_normalization_records_actual_without_mutating_input(self) -> None:
        value = reduced_fixture(3, 6)
        del value["scope"]["selected_card_count"]
        checked = runtime.normalize_pulse(value, strict_count=True, pulse_day="2026-07-22", state_file=str(self.state_path))
        self.assertEqual(checked["scope"]["selected_card_count"], 3)
        self.assertEqual(checked["scope"]["requested_card_count"], 6)
        self.assertNotIn("selected_card_count", value["scope"])


class ImagePolicyRegressionTests(StatefulRuntimeTest):
    def test_schema_rejects_missing_or_malformed_image_metadata(self) -> None:
        for field in ("illustration_brief", "cover_image"):
            value = payload(6)
            del value["cards"][0][field]
            with self.subTest(field=field), self.assertRaises(SystemExit):
                self.validate(value)

        value = payload(6)
        value["cards"][0]["illustration_brief"]["avoid"] = "private screenshots"
        with self.assertRaises(SystemExit):
            self.validate(value)


class CurrentPathTests(StatefulRuntimeTest):
    def test_full_eight_card_edition_preserves_daily_state(self) -> None:
        write_state(self.state_path, "2026-07-22", 8)
        before = self.state_path.read_bytes()
        result = self.send(payload(8))
        self.assertEqual(runtime.load_send_journal(str(self.journal_path)), result)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["target_card_count"], 8)
        self.assertEqual(len(result["details"]), 8)
        self.assertEqual(result["feed_message_id"], 9)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_cli_sends_real_dry_run_and_blocks_duplicate(self) -> None:
        source = self.root / "pulse.json"
        source.write_text(json.dumps(reduced_fixture(3, 6)))
        args = ["send", str(source), "--dry-run", "--pulse-day", "2026-07-22",
                "--state-file", str(self.state_path), "--send-record", str(self.journal_path),
                "--cards-target", CARDS_TARGET, "--feed-target", FEED_TARGET]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runtime.main(args), 0)
        saved = json.loads(self.journal_path.read_text())
        self.assertEqual(json.loads(output.getvalue()), saved)
        self.assertEqual((saved["target_card_count"], saved["desired_card_count"]), (3, 6))
        self.assertEqual(len(saved["details"]), 3)
        before = self.journal_path.read_bytes()
        with self.assertRaises(SystemExit):
            runtime.main(args)
        self.assertEqual(self.journal_path.read_bytes(), before)

    def test_partial_and_feed_pending_resume_preserve_sent_details(self) -> None:
        for actual in (1, 3):
            value = runtime.normalize_pulse(reduced_fixture(actual, 6), tools_target=CARDS_TARGET)
            partial = runtime.native_send_journal(
                value, pulse_day="2026-07-22", cards_target=CARDS_TARGET, feed_target=FEED_TARGET)
            partial["details"] = {"pulse-card-001": 1601}
            partial["detail_refs"] = {"pulse-card-001": "https://t.me/c/1234567890/301/1601"}
            partial["status"] = "feed_pending" if actual == 1 else "details_partial"
            runtime.write_send_journal(str(self.journal_path), partial)
            completed = self.send(value)
            self.assertEqual(completed["details"]["pulse-card-001"], 1601)
            self.assertEqual(len(completed["details"]), actual)
            self.assertEqual(completed["status"], "complete")

    def test_conflicting_and_ambiguous_journals_remain_unchanged(self) -> None:
        value = runtime.normalize_pulse(reduced_fixture(3, 6), tools_target=CARDS_TARGET)
        expected = runtime.native_send_journal(
            value, pulse_day="2026-07-22", cards_target=CARDS_TARGET, feed_target=FEED_TARGET)
        mutations = [
            {"payload_sha256": "0" * 64}, {"desired_card_count": 7},
            {"target_card_count": True}, {"cards_target": FEED_TARGET},
            {"status": "feed_pending"}, {"feed_message_id": 40},
            {"details": {"unknown": 1}, "detail_refs": {"unknown": "https://example.org"}},
            {"details": {"pulse-card-001": True}, "detail_refs": {"pulse-card-001": "https://t.me/c/1234567890/301/1"}},
            {"details": {"pulse-card-001": 1}, "detail_refs": {"pulse-card-001": "https://example.org"}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                runtime.write_send_journal(str(self.journal_path), dict(expected, **mutation))
                before = self.journal_path.read_bytes()
                with self.assertRaises(SystemExit):
                    self.send(value)
                self.assertEqual(self.journal_path.read_bytes(), before)

    def test_transport_timeout_preserves_in_flight_and_blocks_retry(self) -> None:
        value = native_pulse(6)
        with mock.patch.dict(os.environ, {"SYNTHETIC_PULSE_TOKEN": "offline-token"}), mock.patch.object(
            runtime.urllib.request, "urlopen", side_effect=TimeoutError("response lost")
        ) as transport:
            with self.assertRaises(TimeoutError):
                self.send(value, dry_run=False)
            before = self.journal_path.read_bytes()
            journal = json.loads(before)
            self.assertEqual(journal["details"], {})
            self.assertEqual(journal["in_flight"]["card_id"], "pulse-card-001")
            with self.assertRaises(SystemExit):
                self.send(value, dry_run=False)
            self.assertEqual(self.journal_path.read_bytes(), before)
            self.assertEqual(transport.call_count, 1)

    def test_concurrent_dry_runs_commit_one_edition(self) -> None:
        value = reduced_fixture(3, 6)
        barrier = threading.Barrier(2)
        def send():
            barrier.wait(timeout=5)
            return self.send(value)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(send) for _ in range(2)]
            outcomes = [future.exception(timeout=10) for future in futures]
        self.assertEqual(sum(error is None for error in outcomes), 1)
        self.assertEqual(sum(isinstance(error, SystemExit) for error in outcomes), 1)
        self.assertEqual(runtime.load_send_journal(str(self.journal_path))["status"], "complete")

    def test_preference_cli_preserves_historical_state_and_updates_refresh_budget(self) -> None:
        write_state(self.state_path, "2026-07-22", 6,
                    keyboard_token="historical", clear_keyboard_required=True,
                    card_feedback=[{"intent": "follow"}])
        before = json.loads(self.state_path.read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.main(["preference", "set", "新鲜一点", "--state-file", str(self.state_path)])
        saved = runtime.load_preference(str(self.state_path))
        for key, value in before.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(saved["last_preference"], "Refresh 多一点")
        self.assertEqual(runtime.get_required_refresh_count(str(self.state_path)), 2)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            runtime.main(["preference", "get", "--state-file", str(self.state_path)])
        self.assertEqual(json.loads(output.getvalue()), saved)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runtime.main(["preference", "expire-keyboard", "--state-file", str(self.state_path)])
        self.assertEqual(runtime.load_preference(str(self.state_path)), saved)

    def test_daily_target_is_stable_and_malformed_state_is_preserved(self) -> None:
        path = self.root / "new-state.json"
        first = runtime.get_or_create_daily_target(str(path), "2026-07-22")
        self.assertIn(first["count"], (6, 7, 8))
        self.assertEqual(runtime.get_or_create_daily_target(str(path), "2026-07-22"), first)
        for text in ('{"daily_card_targets": []}', '{"daily_card_targets":{"2026-07-22":{"count":9}}}', '{broken'):
            path.write_text(text)
            with self.assertRaises(SystemExit):
                runtime.get_or_create_daily_target(str(path), "2026-07-22")
            self.assertEqual(path.read_text(), text)

    def test_zero_over_ceiling_and_long_detail_fail_before_journal(self) -> None:
        values = [reduced_fixture(0, 6), reduced_fixture(7, 6)]
        long = reduced_fixture(3, 6)
        long["cards"][0]["delivery"]["result_summary"] = "观测范围有限。" * 1000
        values.append(long)
        for value in values:
            with self.assertRaises(SystemExit):
                self.send(value)
            self.assertFalse(self.journal_path.exists())

    def test_new_value_can_be_synthesis_context_or_tradeoff(self) -> None:
        examples = [
            ("new_information", "睡眠记录的两种读法", "把时长和起床时间放在一起看，能区分睡得少和作息漂移。"),
            ("new_state", "通勤变短后的练习安排", "通勤从一小时缩到二十分钟后，一段完整听力更适合拆成两次复听。"),
            ("current_relevance", "排队任务该何时合并", "批量处理能减少切换，但会增加等待；对即时消息和归档任务应采用不同窗口。"),
        ]
        value = reduced_fixture(3, 6)
        for item, (reason, title, prose) in zip(value["cards"], examples):
            item.update(title=title, summary=prose, why_now="", recent_topic_repeat=True, novelty_reason=reason)
            item["delivery"].update(state="insight", result_summary=prose)
        normalized = runtime.normalize_pulse(value, strict_count=True, pulse_day="2026-07-22", state_file=str(self.state_path))
        for item, (_, title, prose) in zip(normalized["cards"], examples):
            rendered = runtime.render_detail_html(item, include_cover_note=False)
            self.assertTrue(rendered.startswith(f"<b>{title}</b>"))
            self.assertEqual(rendered.count(prose), 1)
            self.assertNotIn("已交付", rendered)
            self.assertNotIn("<b>摘要</b>", rendered)
            self.assertNotIn("为什么现在", rendered)
        self.assertEqual(self.send(value)["status"], "complete")

    def test_all_external_insights_need_no_personal_quota(self) -> None:
        value = reduced_fixture(3, 6)
        for item in value["cards"]:
            item["content_class"] = "C4"
            item["delivery"]["state"] = "insight"
            item["evidence"] = [evidence("external_source", "https://example.org/research")]
        reason = "这次三个独立的外部发现更值得展开。"
        value["scope"]["selection_exceptions"] = [{"type": "class_concentration", "value": "C4", "reason": reason}]
        value["global_uncertainties"] = [reason]
        self.assertEqual(self.send(value)["status"], "complete")

    def test_scientific_qualifications_and_optional_actions_survive_rendering(self) -> None:
        item = runtime.normalize_card(card(1), 1, CARDS_TARGET)
        prose = "样本量有限，不确定性主要来自测量误差；相关性不能证明因果。建议先区分观察与推断。"
        item["delivery"]["result_summary"] = prose
        item["summary"] = prose
        item["why_now"] = prose
        item["recommendation"] = "比较两组观测。"
        item["suggested_next_action"] = {"label": "查看误差区间", "estimated_effort": "5m"}
        for renderer in (runtime.render_detail, runtime.render_detail_html, runtime.render_detail_photo_caption):
            rendered = renderer(item)
            self.assertEqual(rendered.count(prose), 1)
            self.assertNotIn("查看误差区间", rendered)
        item["delivery"].update(state="decision_needed", user_action_required=True)
        rendered = runtime.render_detail_html(item)
        self.assertIn("查看误差区间（5m）", rendered)
        self.assertIn("比较两组观测。", rendered)

    def test_media_size_and_regular_file_checks(self) -> None:
        cover = self.root / "oversized.png"
        with cover.open("wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
            handle.truncate(10 * 1024 * 1024 + 1)
        directory = self.root / "directory.png"
        directory.mkdir()
        for path in (cover, directory):
            with self.assertRaises(SystemExit):
                runtime.validate_local_cover_file(str(path), allowed_roots=(self.root,))

    def test_input_parser_and_detail_output_reject_partial_json_and_traversal(self) -> None:
        source = self.root / "pulse.json"
        source.write_text('{"cards": [broken, {"title": "fragment"}]}')
        with self.assertRaises(SystemExit):
            runtime.load_jsonish(str(source))
        value = reduced_fixture(1, 6)
        value["cards"][0]["id"] = "../escape"
        source.write_text(json.dumps(value))
        with self.assertRaises(SystemExit):
            runtime.main(["render-details", str(source), "--out-dir", str(self.root / "details")])
        self.assertFalse((self.root / "escape.md").exists())
        self.assertFalse((self.root / "details").exists())

    def test_supplied_invalid_confidence_is_rejected(self) -> None:
        for confidence in ("not-a-number", float("nan"), float("inf"), -4, 101, True, None, {}):
            value = reduced_fixture(3, 6)
            value["cards"][0]["confidence"] = confidence
            with self.subTest(confidence=confidence), self.assertRaises((SystemExit, ValueError, TypeError)):
                runtime.normalize_pulse(value, strict_count=True, pulse_day="2026-07-22", state_file=str(self.state_path))
        for confidence in (0.8, "0.8", "80%", 80):
            self.assertEqual(runtime.normalize_confidence(confidence), 0.8)

    def test_transport_requests_preserve_targets_media_and_optional_buttons(self) -> None:
        photo = self.root / "cover.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9")
        markup = runtime.card_action_markup()
        self.assertEqual(markup["inline_keyboard"], [[
            {"text": "展开讲讲", "callback_data": "pulse:expand"},
            {"text": "关闭按钮", "callback_data": "pulse:close"},
        ]])
        calls = {
            "text": lambda **options: runtime.telegram_send_text("正文", **options),
            "photo": lambda **options: runtime.telegram_send_photo("https://example.org/cover.png", caption="正文", **options),
            "multipart": lambda **options: runtime.telegram_send_local_photo(str(photo), caption="正文", **options),
        }
        for kind, send in calls.items():
            for target, options in ((FEED_TARGET, {}), (CARDS_TARGET, {"reply_markup": markup})):
                with self.subTest(kind=kind, target=target), mock.patch.dict(
                    os.environ, {"SYNTHETIC_PULSE_TOKEN": "offline-token"}
                ), mock.patch.object(runtime.urllib.request, "urlopen", return_value=io.BytesIO(
                    b'{"ok": true, "result": {"message_id": 1}}'
                )) as transport:
                    send(target=target, parse_mode="HTML", **options)
                    request = transport.call_args.args[0]
                    thread = 301 if options else 302
                    if kind == "multipart":
                        self.assertIn(b"Content-Type: image/jpeg", request.data)
                        self.assertIn(b'name="chat_id"\r\n\r\n-1001234567890\r\n', request.data)
                        self.assertIn(f'name="message_thread_id"\r\n\r\n{thread}\r\n'.encode(), request.data)
                        if options:
                            self.assertIn(b'name="reply_markup"\r\n\r\n' + json.dumps(markup, ensure_ascii=False).encode() + b"\r\n", request.data)
                    else:
                        body = json.loads(request.data)
                        self.assertEqual((body["chat_id"], body["message_thread_id"]), ("-1001234567890", thread))
                        if options:
                            self.assertEqual(body["reply_markup"], markup)
                    if not options:
                        self.assertNotIn(b"reply_markup", request.data)

    def test_feed_cli_previews_native_linked_layout(self) -> None:
        value = reduced_fixture(3, 6)
        source = self.root / "preview.json"
        source.write_text(json.dumps(value))
        refs = {item["id"]: runtime.official_message_link(CARDS_TARGET, index) for index, item in enumerate(value["cards"], 1)}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            runtime.main(["render-feed", str(source), "--detail-refs-json", json.dumps(refs)])
        actual = output.getvalue().strip()
        expected = runtime.render_feed_html(runtime.normalize_pulse(value), detail_refs=refs, max_units=1000)
        self.assertEqual(actual, expected)
        self.assertLessEqual(runtime.utf16_units(actual), 1000)
        self.assertNotIn("接下来", actual)

    def test_long_feed_fails_before_journal_or_details(self) -> None:
        value = reduced_fixture(8, 8)
        write_state(self.state_path, "2026-07-22", 8)
        for index, item in enumerate(value["cards"]):
            item["title"] = str(index) + "很长的标题" * 25
        source = self.root / "long-feed.json"
        source.write_text(json.dumps(value))
        with self.assertRaises(SystemExit):
            runtime.main(["render-feed", str(source)])
        with self.assertRaises(SystemExit):
            self.send(value)
        self.assertFalse(self.journal_path.exists())


if __name__ == "__main__":
    unittest.main()

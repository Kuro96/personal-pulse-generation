#!/usr/bin/env python3
"""Validate, render and publish Personal Pulse using the Python standard library."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pulse_config import load_config

CONFIG = load_config()
# Legacy normalization uses the feed topic; native sends bind the cards topic.
DEFAULT_TOOLS_TARGET = CONFIG["delivery"]["feed_target"]
DEFAULT_FEED_TARGET = CONFIG["delivery"]["feed_target"]
NATIVE_CARDS_TARGET = CONFIG["delivery"]["cards_target"]
DEFAULT_STATE_FILE = CONFIG["paths"]["state_file"]
DEFAULT_ENV_FILE = CONFIG["paths"]["env_file"]
DEFAULT_JOURNAL_DIR = CONFIG["paths"]["journal_dir"]


def scratch_root() -> Path:
    """Return the configured scratch directory, creating it when needed."""
    path = Path(CONFIG["paths"]["scratch_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path


PULSE_RUN_ROOT = scratch_root() / "hermes-pulse"
APPROVED_COVER_ROOTS = (PULSE_RUN_ROOT,)
TELEGRAM_PHOTO_MAX_BYTES = 10 * 1024 * 1024

DEFAULT_CHOICES = [
    "项目收口优先",
    "生活/精力优先",
    "消息处理优先",
    "Refresh 多一点",
    "保持现状就好",
]
LEGACY_CHOICE_ALIASES = {
    "项目推进": "项目收口优先",
    "通知/邮箱": "消息处理优先",
    "收件箱/通知": "消息处理优先",
    "风险排查": "项目收口优先",
    "长期风险": "项目收口优先",
    "生活/健康": "生活/精力优先",
    "生活/精力": "生活/精力优先",
    "多一点证据": "Refresh 多一点",
    "证据多一点": "Refresh 多一点",
    "少一点工程细节": "Refresh 多一点",
    "refresh": "Refresh 多一点",
    "Refresh": "Refresh 多一点",
    "Refresh 多一点": "Refresh 多一点",
    "新鲜一点": "Refresh 多一点",
    "多一点新鲜东西": "Refresh 多一点",
}
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
PULSE_TIMEZONE = CONFIG["user"]["timezone"]
PULSE_TARGET_COUNTS = {6, 7, 8}
CONTENT_CLASSES = {"C1", "C2", "C3", "C4"}
TIME_HORIZONS = {"today_72h", "recent_2w", "long_term"}
DELIVERY_STATES = {"delivered", "insight", "decision_needed", "action_needed"}
FEEDBACK_INTENTS = {"follow", "completed", "mute_until", "boost_topic", "reduce_action_load"}
QUALIFYING_NOVELTY_REASONS = {
    "new_information", "new_state", "current_relevance",
    "completed_deliverable", "real_time_window", "unresolved_decision",
}
SECRET_QUERY_KEY_PARTS = {
    "auth", "authentication", "authorization", "credential", "credentials",
    "key", "password", "passwd", "secret", "signature", "signing", "token",
}
SECRET_QUERY_KEY_SUFFIXES = ("apikey", "credential", "password", "secret", "signature", "token")
LOCAL_PATH_RE = re.compile(r"(?:^|[\s'\"(])(?:file:|/(?:opt|home|root|tmp)/|[A-Za-z]:\\)", re.IGNORECASE)
LOCAL_PATH_VALUE_RE = re.compile(
    r"(?:file:(?://)?[^\s<>\"']+|/(?:opt|home|root|tmp)(?:/[^\s<>\"']*)?|[A-Za-z]:\\[^\s<>\"']+)",
    re.IGNORECASE,
)
REFRESH_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:refresh\s*[:：|｜\-–—]\s*|\[\s*refresh\s*\]\s*|【\s*refresh\s*】\s*)",
    re.IGNORECASE,
)
FIXED_FEEDBACK_FIELDS = {"buttons", "choices", "inline_keyboard", "reply_markup", "menu", "footer"}
COVER_REFERENCE_STATUSES = {"ready", "generated", "available"}
TEXT_FALLBACK_COVER_STATUSES = {"force_majeure_text_fallback", "user_opt_out_text_fallback"}
def die(message: str, code: int = 2) -> None:
    print(f"pulse_runtime.py: error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_env_file(path: str | None = DEFAULT_ENV_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
    if path is None:
        return env
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def telegram_token() -> str:
    token = os.getenv(CONFIG["token_env"]) or load_env_file().get(CONFIG["token_env"])
    if not token:
        die("configured token variable is absent from the environment and configured env file")
    return token


def telegram_api(method: str, payload: dict[str, Any] | None = None, *, timeout: int = 60) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{telegram_token()}/{method}"
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if not out.get("ok"):
        die(f"Telegram API {method} failed: {out}")
    return out


def normalize_preference_choice(choice: Any) -> str:
    text = as_text(choice)
    return LEGACY_CHOICE_ALIASES.get(text, text)


def detail_action_required(card: dict[str, Any]) -> bool:
    delivery = card.get("delivery") if isinstance(card.get("delivery"), dict) else {}
    if delivery.get("state") in {"decision_needed", "action_needed"}:
        return True
    return delivery.get("user_action_required") is not False


def detail_paragraphs(card: dict[str, Any]) -> list[str]:
    """Collect substantive prose once, preserving factual qualifications."""
    delivery = card.get("delivery") or {}
    values = [delivery.get("result_summary"), card.get("summary") or card.get("subtitle")]
    context = card.get("why_now")
    if context != "近期证据显示它值得现在处理。":
        values.append(context)
    if detail_action_required(card):
        values.append(card["recommendation"])
        action = card["suggested_next_action"]
        values.append(f"{action['label']}（{action['estimated_effort']}）")
    paragraphs = []
    for value in values:
        for paragraph in reader_surface_text(value).split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph and paragraph not in paragraphs:
                paragraphs.append(paragraph)
    return paragraphs


def render_detail_html(card: dict[str, Any], *, include_cover_note: bool = True) -> str:
    parts = [f"<b>{html_escape(reader_surface_text(card['title']))}</b>"]
    parts.extend(render_cited_paragraphs(card, as_html=True))
    cover = card.get("cover_image") or {}
    cover_url = safe_public_citation_url(cover.get("url"))
    if include_cover_note and cover_url:
        parts.append(f'<a href="{html_attribute_escape(cover_url)}">配图</a>')
    return "\n\n".join(parts)


def render_detail_photo_caption(card: dict[str, Any]) -> str:
    """Render the complete native detail; callers choose caption or text delivery."""
    return render_detail_html(card, include_cover_note=False)


def card_cover_ref(card: dict[str, Any]) -> str:
    cover = card.get("cover_image") or {}
    url = as_text(cover.get("url"))
    if usable_cover_url(url):
        return url
    return as_text(cover.get("file"))


def feed_cover_ref(pulse: dict[str, Any]) -> str:
    cover = pulse.get("feed_cover_image") or {}
    url = as_text(cover.get("url"))
    if usable_cover_url(url):
        return url
    return as_text(cover.get("file"))


def thread_for_target(target: str) -> tuple[str, int | None]:
    match = re.fullmatch(r"telegram:(-?[1-9]\d*)(?::([1-9]\d*))?", target)
    if match is None:
        raise ValueError(f"invalid Telegram target: {target!r}")
    return match[1], int(match[2]) if match[2] else None


def official_message_link(target: str, message_id: str | int) -> str | None:
    """Return Telegram's documented t.me/c message link when the target supports it.

    Telegram documents stable links for private groups/channels as:
    - https://t.me/c/<internal_chat_id>/<message_id>
    - https://t.me/c/<internal_chat_id>/<topic_thread_id>/<message_id>

    Bot private DM topics do not have a verified stable link format, so they
    intentionally fall back to readable labels.
    """
    chat_id, thread_id = thread_for_target(target)
    if chat_id.startswith("-100"):
        internal_id = chat_id[4:]
        if thread_id is not None:
            return f"https://t.me/c/{internal_id}/{thread_id}/{message_id}"
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def html_escape(text: Any) -> str:
    return as_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_attribute_escape(text: Any) -> str:
    return html_escape(text).replace('"', "&quot;")


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def fit_escaped_text(value: Any, budget: int) -> str:
    """Fit escaped text to a UTF-16 budget and visibly mark any shortening."""
    text = reader_surface_text(value)
    escaped_text = html_escape(text)
    if utf16_units(escaped_text) <= budget:
        return escaped_text
    if budget < utf16_units("…"):
        return ""
    rendered: list[str] = []
    used = 0
    content_budget = budget - utf16_units("…")
    for character in text:
        escaped = character.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        units = utf16_units(escaped)
        if used + units > content_budget:
            break
        rendered.append(character)
        used += units
    shortened = "".join(rendered).rstrip()
    boundaries = list(re.finditer(r"[。！？!?；;，,、：:\n.]", shortened))
    if boundaries and boundaries[-1].end() >= len(shortened) // 2:
        shortened = shortened[:boundaries[-1].end()].rstrip()
    return html_escape(shortened) + "…"


def card_action_markup() -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "展开讲讲", "callback_data": "pulse:expand"},
        {"text": "关闭按钮", "callback_data": "pulse:close"},
    ]]}


def telegram_send_text(text: str, *, target: str, parse_mode: str | None = None, reply_markup: dict | None = None) -> dict[str, Any]:
    chat_id, thread_id = thread_for_target(target)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)


def telegram_send_photo(photo: str, *, caption: str, target: str, parse_mode: str | None = None, reply_markup: dict | None = None) -> dict[str, Any]:
    """Send a URL or Telegram file ID as a photo."""
    chat_id, thread_id = thread_for_target(target)
    payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo, "caption": caption}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendPhoto", payload)


def telegram_send_local_photo(photo_path: str, *, caption: str, target: str, parse_mode: str | None = None, reply_markup: dict | None = None) -> dict[str, Any]:
    """Upload a local image file with an optional caption using multipart/form-data."""
    boundary = f"----pulse{int(time.time() * 1000)}"
    chat_id, thread_id = thread_for_target(target)
    fields: dict[str, Any] = {"chat_id": chat_id, "caption": caption}
    if thread_id is not None:
        fields["message_thread_id"] = str(thread_id)

    if parse_mode:
        fields["parse_mode"] = parse_mode
    if reply_markup is not None:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(as_text(value).encode("utf-8"))
        body.extend(b"\r\n")
    path = Path(photo_path)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'.encode())
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    url = f"https://api.telegram.org/bot{telegram_token()}/sendPhoto"
    req = urllib.request.Request(url, data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if not out.get("ok"):
        die(f"Telegram API sendPhoto failed: {out}")
    return out


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def pulse_local_day() -> str:
    return dt.datetime.now(ZoneInfo(PULSE_TIMEZONE)).date().isoformat()


def validate_pulse_day(value: Any) -> str:
    text = as_text(value)
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError:
        die(f"invalid pulse day {text!r}; expected YYYY-MM-DD")
    if parsed.isoformat() != text:
        die(f"invalid pulse day {text!r}; expected YYYY-MM-DD")
    return text


def load_state_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        value = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError) as exc:
        die(f"invalid state file: {path}: {exc}")
    if not isinstance(value, dict):
        die(f"invalid state file: {path}: top level must be an object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def with_locked_state(state_file: str, update: Any) -> Any:
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state_document(path)
            return update(path, state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def validate_target_record(record: Any, pulse_day: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        die(f"malformed persisted target for {pulse_day}: expected an object")
    count = record.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count not in PULSE_TARGET_COUNTS:
        die(f"malformed persisted target for {pulse_day}: count must be 6, 7, or 8")
    if record.get("timezone") != PULSE_TIMEZONE:
        die(f"malformed persisted target for {pulse_day}: timezone must be {PULSE_TIMEZONE}")
    if parse_iso_datetime(record.get("selected_at")) is None:
        die(f"malformed persisted target for {pulse_day}: selected_at must be an ISO timestamp")
    if record.get("random_function") != "secrets.randbelow":
        die(f"malformed persisted target for {pulse_day}: random_function must be secrets.randbelow")
    return dict(record)


def get_or_create_daily_target(state_file: str, pulse_day: str | None = None) -> dict[str, Any]:
    day = validate_pulse_day(pulse_day or pulse_local_day())

    def update(path: Path, state: dict[str, Any]) -> dict[str, Any]:
        if "daily_card_targets" not in state:
            targets = {}
        else:
            targets = state["daily_card_targets"]
        if not isinstance(targets, dict):
            die("malformed daily_card_targets state: expected an object")
        if day in targets:
            return validate_target_record(targets[day], day)
        record = {
            "count": secrets.randbelow(3) + 6,
            "timezone": PULSE_TIMEZONE,
            "selected_at": dt.datetime.now(ZoneInfo(PULSE_TIMEZONE)).isoformat(timespec="seconds"),
            "random_function": "secrets.randbelow",
        }
        targets = dict(targets)
        targets[day] = record
        state["daily_card_targets"] = targets
        atomic_write_json(path, state)
        return dict(record)

    return with_locked_state(state_file, update)


def get_daily_target(state_file: str, pulse_day: str) -> dict[str, Any]:
    day = validate_pulse_day(pulse_day)

    def read(_path: Path, state: dict[str, Any]) -> dict[str, Any]:
        targets = state.get("daily_card_targets")
        if not isinstance(targets, dict) or day not in targets:
            die(f"no persisted daily target for {day}")
        return validate_target_record(targets[day], day)

    return with_locked_state(state_file, read)


def get_required_refresh_count(state_file: str) -> int:
    def read(_path: Path, state: dict[str, Any]) -> int:
        preference = normalize_preference_choice(state.get("last_preference"))
        return 2 if preference == "Refresh 多一点" else 1

    return with_locked_state(state_file, read)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def reader_surface_text(value: Any) -> str:
    """Return reader prose with local filesystem references removed."""
    return LOCAL_PATH_VALUE_RE.sub("[local path removed]", as_text(value))


def normalize_reader_title(value: Any) -> str:
    title = as_text(value)
    while REFRESH_TITLE_PREFIX_RE.match(title):
        title = REFRESH_TITLE_PREFIX_RE.sub("", title, count=1).strip()
    return title


def load_jsonish(path: str | None) -> Any:
    text = sys.stdin.read() if not path or path == "-" else Path(path).read_text(encoding="utf-8")
    text = text.strip()
    candidates = [text] if text else []
    candidates.extend(m.group(1).strip() for m in FENCE_RE.finditer(text))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    die("no valid JSON found")


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_confidence(value: Any) -> float:
    if value is None:
        return 0.5
    if isinstance(value, str):
        value = value.strip()
        if value.endswith("%"):
            value = value[:-1]
            scale = 100.0
        else:
            scale = 1.0
        number = float(value) / scale
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 1:
            number = number / 100.0
    else:
        die("confidence must be numeric")
    if not math.isfinite(number) or not 0 <= number <= 1:
        die("confidence must be finite and between 0 and 1")
    return round(number, 4)


def normalize_uncertainty(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        level = as_text(value.get("level") or "medium").lower()
        if level not in {"low", "medium", "high"}:
            level = "medium"
        return {
            "level": level,
            "reason": as_text(value.get("reason") or "未提供不确定性原因。"),
            "what_would_reduce_uncertainty": as_text(value.get("what_would_reduce_uncertainty") or value.get("reduce") or "补充检查相关来源。"),
        }
    text = as_text(value)
    return {"level": "medium", "reason": text or "未提供不确定性原因。", "what_would_reduce_uncertainty": "补充检查相关来源。"}


def normalize_next_action(card: dict[str, Any]) -> dict[str, str]:
    value = card.get("suggested_next_action", card.get("next_action", card.get("suggested_action")))
    if isinstance(value, dict):
        return {
            "label": as_text(value.get("label") or value.get("title") or value.get("action") or "确认下一步"),
            "estimated_effort": as_text(value.get("estimated_effort") or "15m"),
            "action_type": as_text(value.get("action_type") or "other"),
        }
    return {"label": as_text(value) or "确认下一步", "estimated_effort": "15m", "action_type": "other"}


def normalize_evidence(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in ensure_list(value):
        if isinstance(item, dict):
            normalized: dict[str, Any] = {
                "claim": as_text(item.get("claim") or item.get("title") or item.get("evidence") or item.get("quote_or_summary")),
                "source_type": as_text(item.get("source_type") or "unknown"),
                "source_ref": as_text(item.get("source_ref") or item.get("ref") or "unspecified"),
                "quote_or_summary": as_text(item.get("quote_or_summary") or item.get("summary") or item.get("evidence") or item.get("claim")),
                "recency": as_text(item.get("recency") or "unknown"),
                "strength": as_text(item.get("strength") or "medium"),
            }
            if "source_label" in item:
                normalized["source_label"] = as_text(item.get("source_label"))
            if "source_url" in item:
                normalized["source_url"] = as_text(item.get("source_url"))
            if "publicly_renderable" in item:
                normalized["publicly_renderable"] = item.get("publicly_renderable")
            items.append(normalized)
        else:
            text = as_text(item)
            if text:
                items.append({"claim": text, "source_type": "unknown", "source_ref": "unspecified", "quote_or_summary": text, "recency": "unknown", "strength": "medium"})
    return [item for item in items if item["claim"] or item["quote_or_summary"]]


def normalize_card(
    card: dict[str, Any], index: int, tools_target: str, *, schema_version: str = "1.0"
) -> dict[str, Any]:
    if not isinstance(card, dict):
        die(f"card #{index} is not an object")
    if schema_version == "1.1" and not isinstance(card.get("id"), str):
        die("schema 1.1 card IDs must be nonempty strings")
    if schema_version == "1.1" and card.get("confidence") is None:
        die("schema 1.1 cards require confidence")
    title = normalize_reader_title(card.get("title"))
    if not title:
        die(f"card #{index} missing title")
    evidence = normalize_evidence(card.get("evidence"))
    if not evidence:
        die(f"card {title!r} missing evidence")
    next_action = normalize_next_action(card)
    summary = as_text(card.get("summary") or card.get("subtitle") or card.get("recommendation"))
    feed_copy = card.get("feed_copy") if isinstance(card.get("feed_copy"), dict) else {}
    detail_message = card.get("detail_message") if isinstance(card.get("detail_message"), dict) else {}
    normalized = dict(card)
    normalized.update({
        "id": as_text(card.get("id")) if schema_version == "1.1" else as_text(card.get("id")) or f"pulse-card-{index:03d}",
        "type": as_text(card.get("type") or "recommendation"),
        "priority": as_text(card.get("priority") or "medium"),
        "title": title,
        "subtitle": as_text(card.get("subtitle")),
        "summary": summary,
        "feed_copy": {
            "transition_lead": as_text(feed_copy.get("transition_lead")) or "**接下来，看一个值得顺手处理的点。**",
            "card_teaser": as_text(feed_copy.get("card_teaser")) or summary,
            "cover_alt": as_text(feed_copy.get("cover_alt")) or f"{title} 的抽象配图",
        },
        "detail_message": {
            "target": as_text(detail_message.get("target")) or tools_target,
            "message_id": as_text(detail_message.get("message_id")) or None,
            "url": as_text(detail_message.get("url")) or None,
            "fallback_label": as_text(detail_message.get("fallback_label")) or "详情 #message_id",
        },
        "recommendation": as_text(card.get("recommendation") or summary),
        "why_now": as_text(card.get("why_now") or "近期证据显示它值得现在处理。"),
        "suggested_next_action": next_action,
        "evidence": evidence,
        "uncertainty": normalize_uncertainty(card.get("uncertainty")),
        "confidence": normalize_confidence(card.get("confidence")),
        "tags": [as_text(x) for x in ensure_list(card.get("tags")) if as_text(x)],
        "related_projects": [as_text(x) for x in ensure_list(card.get("related_projects")) if as_text(x)],
        "sensitivity": card.get("sensitivity") if isinstance(card.get("sensitivity"), dict) else {
            "contains_private_context": True,
            "safe_to_share_publicly": False,
            "notes": "Personal Pulse 默认按私密上下文处理。",
        },
        "cover_image": card.get("cover_image") if schema_version == "1.1" or isinstance(card.get("cover_image"), dict) else {"status": "pending_generation", "url": None, "file": None, "alt": as_text(feed_copy.get("cover_alt")) or f"{title} 的抽象配图"},
        "illustration_brief": card.get("illustration_brief") if schema_version == "1.1" or isinstance(card.get("illustration_brief"), dict) else {
            "enabled": True,
            "prompt": f"抽象、温暖、无私人文字的编辑插图，象征：{title}",
            "style": "warm, minimal, editorial, soft lighting",
            "avoid": ["private screenshots", "exact note titles", "faces", "tokens", "specific file paths"],
        },
    })
    for key in ("content_class", "time_horizon", "event_key"):
        if key in card:
            normalized[key] = as_text(card.get(key))
    if "delivery" in card:
        delivery = card.get("delivery")
        if isinstance(delivery, dict):
            normalized["delivery"] = {
                "state": as_text(delivery.get("state")),
                "user_action_required": delivery.get("user_action_required"),
                "result_summary": as_text(delivery.get("result_summary")),
                "empty_result": delivery.get("empty_result"),
            }
        else:
            normalized["delivery"] = delivery
    if "feedback_context" in card:
        feedback = card.get("feedback_context")
        if isinstance(feedback, dict):
            normalized["feedback_context"] = dict(feedback)
            normalized["feedback_context"].update({
                "mode": as_text(feedback.get("mode")),
                "card_id": as_text(feedback.get("card_id")),
                "accepted_intents": [as_text(item) for item in ensure_list(feedback.get("accepted_intents"))],
            })
        else:
            normalized["feedback_context"] = feedback
    return normalized


def safe_public_citation_url(value: Any) -> str | None:
    url = as_text(value)
    if not url or re.search(r"[\x00-\x20\x7f]", url) or "\\" in url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    if host in {"localhost", "local", "internal"} or host.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    for query_part in re.split(r"[&;]", parsed.query):
        key = urllib.parse.unquote_plus(query_part.partition("=")[0])
        normalized_key = unicodedata.normalize("NFKC", key).casefold()
        key_parts = re.findall(r"[a-z0-9]+", normalized_key)
        compact_key = "".join(key_parts)
        if (
            SECRET_QUERY_KEY_PARTS.intersection(key_parts)
            or any(compact_key.endswith(suffix) for suffix in SECRET_QUERY_KEY_SUFFIXES)
        ):
            return None
    return url


def public_citation_label(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").rstrip(".").lower()
    return f"公开来源：{host}" if host else "公开来源"


def render_cited_paragraphs(card: dict[str, Any], *, as_html: bool) -> list[str]:
    paragraphs = detail_paragraphs(card)
    rendered = [html_escape(p) if as_html else p for p in paragraphs]
    # A repeated URL can acquire a placement from a later evidence item.
    placements: dict[str, int | None] = {}
    for item in card.get("evidence", []):
        if item.get("source_type") != "external_source" or item.get("publicly_renderable") is not True:
            continue
        url = safe_public_citation_url(item.get("source_url"))
        if url and placements.get(url) is None:
            claim = as_text(item.get("claim"))
            placements[url] = next((i for i, p in enumerate(paragraphs) if claim and claim in p), None)

    unmatched = []
    for url, index in placements.items():
        label = public_citation_label(url)
        if as_html:
            link = f'<a href="{html_attribute_escape(url)}">{html_escape(label)}</a>'
        else:
            link = f"[{label}]({url.replace('(', '%28').replace(')', '%29')})"
        if index is None:
            unmatched.append(("• " if as_html else "- ") + link)
        else:
            rendered[index] += " " + link
    if unmatched:
        rendered.append("\n".join(unmatched))
    return rendered


def usable_cover_url(value: Any) -> bool:
    url = as_text(value)
    if not url or re.search(r"[\x00-\x20\x7f]", url) or "\\" in url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    if host in {"localhost", "local", "internal"} or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def validate_public_citation_url(value: Any) -> str:
    url = safe_public_citation_url(value)
    if url is None:
        die(f"unsafe public citation URL: {as_text(value)!r}")
    return url


def validate_reader_surfaces(card: dict[str, Any]) -> None:
    values = [
        card.get("title"),
        card.get("subtitle"),
        card.get("summary"),
        card.get("recommendation"),
        card.get("why_now"),
        (card.get("delivery") or {}).get("result_summary") if isinstance(card.get("delivery"), dict) else None,
        (card.get("feed_copy") or {}).get("transition_lead") if isinstance(card.get("feed_copy"), dict) else None,
        (card.get("feed_copy") or {}).get("card_teaser") if isinstance(card.get("feed_copy"), dict) else None,
        (card.get("suggested_next_action") or {}).get("label") if isinstance(card.get("suggested_next_action"), dict) else None,
    ]
    if any(LOCAL_PATH_RE.search(as_text(value)) for value in values if value is not None):
        die(f"card {card.get('id')!r} exposes a local path on a reader-visible surface")


def validate_schema_1_1(pulse: dict[str, Any], *, pulse_day: str | None, state_file: str | None) -> None:
    if pulse.get("pulse_version") != "1.1":
        die("strict count validation requires pulse_version 1.1")
    explicit_day = validate_pulse_day(pulse_day or "")
    payload_day = validate_pulse_day(pulse.get("pulse_day"))
    if payload_day != explicit_day:
        die(f"payload pulse_day {payload_day} does not match requested day {explicit_day}")
    if not state_file:
        die("strict count validation requires --state-file")
    persisted_count = get_daily_target(state_file, explicit_day)["count"]
    required_refresh_count = get_required_refresh_count(state_file)
    target = pulse.get("daily_target")
    if not isinstance(target, dict):
        die("schema 1.1 payload missing daily_target")
    payload_count = target.get("count")
    if isinstance(payload_count, bool) or not isinstance(payload_count, int) or payload_count not in PULSE_TARGET_COUNTS:
        die("daily_target.count must be 6, 7, or 8")
    if target.get("timezone") != PULSE_TIMEZONE:
        die(f"daily_target.timezone must be {PULSE_TIMEZONE}")
    scope = pulse.get("scope")
    if not isinstance(scope, dict):
        die("schema 1.1 payload missing scope")
    requested_count = scope.get("requested_card_count")
    if isinstance(requested_count, bool) or not isinstance(requested_count, int):
        die("scope.requested_card_count must be an integer")
    card_count = len(pulse["cards"])
    if len({persisted_count, payload_count, requested_count}) != 1:
        die(
            "strict desired card count mismatch: "
            f"persisted={persisted_count}, payload={payload_count}, requested={requested_count}, cards={card_count}"
        )
    if card_count == 0:
        die("PULSE_SEND_FAILED: no qualified content; zero eligible cards")
    if card_count > persisted_count:
        die("selected card count exceeds persisted desired target")
    selected_count = scope.get("selected_card_count", card_count)
    if isinstance(selected_count, bool) or not isinstance(selected_count, int) or selected_count != card_count:
        die("scope.selected_card_count must be an integer matching the actual cards")
    # Keep historical full-size payload digests stable; reduced editions persist actual separately.
    if card_count < persisted_count:
        pulse["scope"] = dict(scope, selected_card_count=card_count)

    coverage = (
        ("content_classes_considered", CONTENT_CLASSES),
        ("time_horizons_considered", TIME_HORIZONS),
    )
    for field, expected in coverage:
        considered = scope.get(field)
        if (
            not isinstance(considered, list)
            or len(considered) != len(expected)
            or set(considered) != expected
        ):
            die(f"scope.{field} must contain every approved value exactly once")
    if "follow_up" in pulse:
        die("schema 1.1 payload must not contain a fixed feedback menu")

    action_count = 0
    class_counts = {value: 0 for value in CONTENT_CLASSES}
    horizon_counts = {value: 0 for value in TIME_HORIZONS}
    card_ids: set[str] = set()
    event_keys: set[str] = set()
    refresh_count = 0
    for card in pulse["cards"]:
        card_id = card.get("id")
        if not card_id:
            die("schema 1.1 card IDs must be nonempty")
        if card_id in card_ids:
            die(f"duplicate card ID {card_id!r}")
        card_ids.add(card_id)
        if card.get("content_class") not in CONTENT_CLASSES:
            die(f"card {card_id!r} has invalid or missing content_class")
        if card.get("time_horizon") not in TIME_HORIZONS:
            die(f"card {card_id!r} has invalid or missing time_horizon")
        class_counts[card["content_class"]] += 1
        horizon_counts[card["time_horizon"]] += 1
        event_key = as_text(card.get("event_key"))
        if not event_key:
            die(f"card {card_id!r} missing event_key")
        if event_key in event_keys:
            die(f"duplicate event_key {event_key!r}")
        event_keys.add(event_key)
        tags = card.get("tags")
        if isinstance(tags, list) and any(as_text(tag).lower() == "refresh" for tag in tags):
            refresh_count += 1
        delivery = card.get("delivery")
        if not isinstance(delivery, dict):
            die(f"card {card_id!r} missing delivery")
        delivery_state = delivery.get("state")
        if delivery_state not in DELIVERY_STATES:
            die(f"card {card_id!r} has invalid delivery state")
        action_required = delivery.get("user_action_required")
        if not isinstance(action_required, bool):
            die(f"card {card_id!r} delivery.user_action_required must be boolean")
        expected_action = delivery_state in {"decision_needed", "action_needed"}
        if action_required != expected_action:
            die(f"card {card_id!r} delivery state disagrees with user_action_required")
        if not as_text(delivery.get("result_summary")):
            die(f"card {card_id!r} missing delivery.result_summary")
        empty_result = delivery.get("empty_result")
        if not isinstance(empty_result, bool):
            die(f"card {card_id!r} delivery.empty_result must be boolean")
        if empty_result:
            die(f"card {card_id!r} is an empty-result card")
        action_count += int(action_required)

        repeated_topic = card.get("recent_topic_repeat", False)
        if not isinstance(repeated_topic, bool):
            die(f"card {card_id!r} recent_topic_repeat must be boolean")
        if repeated_topic and card.get("novelty_reason") not in QUALIFYING_NOVELTY_REASONS:
            die(f"card {card_id!r} repeats a recent topic without a qualifying novelty reason")

        feedback = card.get("feedback_context")
        if not isinstance(feedback, dict):
            die(f"card {card_id!r} missing feedback_context")
        accepted = feedback.get("accepted_intents")
        if feedback.get("mode") != "natural_language_reply" or feedback.get("card_id") != card_id:
            die(f"card {card_id!r} has invalid feedback_context association")
        if not isinstance(accepted, list) or not accepted or any(item not in FEEDBACK_INTENTS for item in accepted):
            die(f"card {card_id!r} has invalid feedback intents")
        if FIXED_FEEDBACK_FIELDS.intersection(feedback):
            die(f"card {card_id!r} feedback_context contains fixed reader controls")

        illustration = card.get("illustration_brief")
        if not isinstance(illustration, dict):
            die(f"card {card_id!r} missing illustration_brief")
        if not isinstance(illustration.get("enabled"), bool):
            die(f"card {card_id!r} illustration_brief.enabled must be boolean")
        if not as_text(illustration.get("prompt")) or not as_text(illustration.get("style")):
            die(f"card {card_id!r} illustration_brief requires prompt and style")
        avoid = illustration.get("avoid")
        if not isinstance(avoid, list) or any(not isinstance(item, str) for item in avoid):
            die(f"card {card_id!r} illustration_brief.avoid must be a string array")
        cover = card.get("cover_image")
        if not isinstance(cover, dict):
            die(f"card {card_id!r} missing cover_image")
        if not as_text(cover.get("status")) or not as_text(cover.get("alt")):
            die(f"card {card_id!r} cover_image requires status and alt")
        if "url" not in cover or "file" not in cover:
            die(f"card {card_id!r} cover_image requires url and file metadata")
        cover_status = as_text(cover.get("status"))
        has_cover_reference = bool(as_text(cover.get("url")) or as_text(cover.get("file")))
        if cover_status in COVER_REFERENCE_STATUSES and not has_cover_reference:
            die(f"card {card_id!r} cover status {cover_status!r} requires a URL or file reference")
        if not has_cover_reference:
            if cover_status not in TEXT_FALLBACK_COVER_STATUSES:
                die(f"card {card_id!r} media-less cover status {cover_status!r} is not publishable")
            if not as_text(cover.get("reason")):
                die(f"card {card_id!r} text fallback cover requires a nonempty reason")

        external_count = 0
        for item in card.get("evidence", []):
            if item.get("source_type") != "external_source":
                continue
            external_count += 1
            validate_public_citation_url(item.get("source_url"))
            if "publicly_renderable" in item and not isinstance(item.get("publicly_renderable"), bool):
                die(f"card {card_id!r} evidence publicly_renderable must be boolean")
        if card.get("content_class") == "C4" and external_count == 0:
            die(f"C4 card {card_id!r} requires external_source evidence")
        validate_reader_surfaces(card)
        if REFRESH_TITLE_PREFIX_RE.match(as_text(card.get("title"))):
            die(f"card {card_id!r} retains a reader-visible Refresh prefix")
    if action_count > 2:
        die(f"payload has {action_count} user-action cards; maximum is 2")
    refresh_status = scope.get("refresh_status")
    if refresh_status is not None:
        if not isinstance(refresh_status, dict) or set(refresh_status) != {
            "desired_count", "selected_count", "note"
        }:
            die("scope.refresh_status must contain exactly desired_count, selected_count, and note")
        desired_refresh = refresh_status.get("desired_count")
        if isinstance(desired_refresh, bool) or not isinstance(desired_refresh, int) or desired_refresh != required_refresh_count:
            die("scope.refresh_status.desired_count must be an integer matching the current preference")
        selected_refresh = refresh_status.get("selected_count")
        if isinstance(selected_refresh, bool) or not isinstance(selected_refresh, int) or selected_refresh != refresh_count:
            die("scope.refresh_status.selected_count must be an integer matching selected refresh cards")
        if not isinstance(refresh_status.get("note"), str):
            die("scope.refresh_status.note must be a string")
    if refresh_count < required_refresh_count:
        if not isinstance(refresh_status, dict) or not as_text(refresh_status.get("note")):
            die("refresh shortfall requires a reader-facing scope.refresh_status.note")

    all_cards_text_fallback = all(
        as_text((card.get("cover_image") or {}).get("status")) in TEXT_FALLBACK_COVER_STATUSES
        and not as_text((card.get("cover_image") or {}).get("url"))
        and not as_text((card.get("cover_image") or {}).get("file"))
        for card in pulse["cards"]
    )
    feed_cover = pulse.get("feed_cover_image")
    if feed_cover is None:
        if not all_cards_text_fallback:
            die("image Pulse requires an uncut feed_cover_image collage")
    elif not isinstance(feed_cover, dict):
        die("feed_cover_image must be an object")
    else:
        feed_status = as_text(feed_cover.get("status"))
        feed_url = as_text(feed_cover.get("url"))
        feed_file = as_text(feed_cover.get("file"))
        if not feed_status or not as_text(feed_cover.get("alt")):
            die("feed_cover_image requires status and alt")
        if "url" not in feed_cover or "file" not in feed_cover:
            die("feed_cover_image requires url and file metadata")
        if feed_url and not usable_cover_url(feed_url):
            die("feed_cover_image has an unusable remote URL")
        if feed_status in COVER_REFERENCE_STATUSES:
            if not feed_url and not feed_file:
                die(f"feed cover status {feed_status!r} requires a URL or file reference")
        elif feed_status in TEXT_FALLBACK_COVER_STATUSES:
            if feed_url or feed_file:
                die("text-fallback feed_cover_image must not include media")
            if not all_cards_text_fallback:
                die("feed cover fallback is allowed only when every card is text fallback")
            if not as_text(feed_cover.get("reason")):
                die("text-fallback feed_cover_image requires a nonempty reason")
        else:
            die(f"feed_cover_image has unpublishable status {feed_status!r}")

    if card_count == 1 and not all_cards_text_fallback:
        cover = pulse["cards"][0]["cover_image"]
        if any(as_text(cover.get(key)) != as_text(feed_cover.get(key)) for key in ("file", "url")):
            die("one-card Pulse must use the same 1x1 image for card and feed")

    class_limit = (card_count + 1) // 2
    required_exceptions = {
        ("class_concentration", value)
        for value, count in class_counts.items()
        if count > class_limit
    }
    required_exceptions.update(
        ("missing_horizon", value)
        for value, count in horizon_counts.items()
        if count == 0
    )
    raw_exceptions = scope.get("selection_exceptions", [])
    if not isinstance(raw_exceptions, list):
        die("scope.selection_exceptions must be an array")
    global_uncertainties = pulse.get("global_uncertainties")
    if not isinstance(global_uncertainties, list):
        die("global_uncertainties must be an array")
    seen_exceptions: set[tuple[str, str]] = set()
    for exception in raw_exceptions:
        if not isinstance(exception, dict) or set(exception) != {"type", "value", "reason"}:
            die("each selection exception must contain exactly type, value, and reason")
        exception_type = exception.get("type")
        value = exception.get("value")
        reason = exception.get("reason")
        if exception_type not in {"class_concentration", "missing_horizon"}:
            die(f"unknown selection exception type {exception_type!r}")
        approved_values = CONTENT_CLASSES if exception_type == "class_concentration" else TIME_HORIZONS
        if value not in approved_values:
            die(f"selection exception {exception_type!r} has unknown subject {value!r}")
        if not isinstance(reason, str) or not reason.strip():
            die("selection exception reason must be a nonempty string")
        key = (exception_type, value)
        if key in seen_exceptions:
            die(f"duplicate selection exception for {exception_type}:{value}")
        if key not in required_exceptions:
            die(f"unnecessary selection exception for {exception_type}:{value}")
        if reason not in global_uncertainties:
            die(f"selection exception reason for {exception_type}:{value} is not recorded globally")
        seen_exceptions.add(key)
    missing_exceptions = required_exceptions - seen_exceptions
    if missing_exceptions:
        subjects = ", ".join(f"{kind}:{value}" for kind, value in sorted(missing_exceptions))
        die(f"missing required selection exceptions: {subjects}")


def normalize_pulse(
    payload: Any,
    *,
    strict_count: bool = False,
    tools_target: str = DEFAULT_TOOLS_TARGET,
    pulse_day: str | None = None,
    state_file: str | None = None,
) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = {"cards": payload}
    if not isinstance(payload, dict):
        die("pulse payload must be an object or card list")
    cards_raw = payload.get("cards") if "cards" in payload else payload.get("pulse_cards", payload.get("candidate_cards"))
    if not isinstance(cards_raw, list):
        die("payload missing cards array")
    schema_version = as_text(payload.get("pulse_version")) or "1.0"
    cards = [
        normalize_card(card, i + 1, tools_target, schema_version=schema_version)
        for i, card in enumerate(cards_raw)
    ]
    out = dict(payload)
    out.setdefault("pulse_version", "1.0")
    out.setdefault("generated_at", now_iso())
    out.setdefault("language", CONFIG["user"]["language"])
    out.setdefault("scope", {"user": CONFIG["user"]["name"], "time_window": "recent", "sources_used": [], "sources_unavailable": []})
    out["cards"] = cards
    out.setdefault("omitted_candidates", [])
    out.setdefault("global_uncertainties", [])
    if out["pulse_version"] == "1.0":
        out.setdefault("follow_up", {
            "question": "明天的 Pulse 想优先偏向哪类？",
            "choices": DEFAULT_CHOICES,
            "keyboard": "temporary_reply_keyboard_or_plain_text_fallback",
            "clear_after_response": True,
        })
    if strict_count:
        validate_schema_1_1(out, pulse_day=pulse_day, state_file=state_file)
    return out


def render_detail(card: dict[str, Any]) -> str:
    parts = [f"## {reader_surface_text(card['title'])}", *render_cited_paragraphs(card, as_html=False)]
    return "\n\n".join(parts)


def render_feed_preview(pulse: dict[str, Any], *, detail_refs: dict[str, str] | None = None) -> str:
    """Use native layout with link space reserved for not-yet-sent details."""
    refs = dict(detail_refs or {})
    for card in pulse["cards"]:
        if card["id"] not in refs:
            refs[card["id"]] = official_message_link(NATIVE_CARDS_TARGET, 9223372036854775807)
    return render_feed_html(pulse, detail_refs=refs, max_units=1000 if feed_cover_ref(pulse) else 3900)


def native_feed_cover_note(cards: list[dict[str, Any]]) -> str:
    """Return a generic reader-safe note for approved media-less delivery."""
    statuses = {
        as_text((card.get("cover_image") or {}).get("status"))
        for card in cards
    }
    if "force_majeure_text_fallback" in statuses:
        return "配图：本次因授权、限流或服务阻断未生成。"
    if "user_opt_out_text_fallback" in statuses:
        return "配图：本次按设置省略。"
    return ""


def native_feed_refresh_note(pulse: dict[str, Any]) -> str:
    """Return the reader-facing note for a best-effort refresh shortfall."""
    scope = pulse.get("scope")
    status = scope.get("refresh_status") if isinstance(scope, dict) else None
    if not isinstance(status, dict):
        return ""
    desired = status.get("desired_count")
    selected = status.get("selected_count")
    if not isinstance(desired, int) or not isinstance(selected, int) or selected >= desired:
        return ""
    note = reader_surface_text(status.get("note"))
    return f"今日新鲜内容：{note}" if note else ""


def render_feed_html(
    pulse: dict[str, Any], *, detail_refs: dict[str, str] | None = None, max_units: int = 3900
) -> str:
    if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units <= 0:
        die("native feed max_units must be a positive integer")
    detail_refs = detail_refs or {}
    cards = pulse["cards"]
    header = f"<b>今日 Pulse</b>\n\n整理了 {len(cards)} 张卡片，标题可直接打开详情。"
    refresh_note = native_feed_refresh_note(pulse)
    if refresh_note:
        header += "\n" + fit_escaped_text(refresh_note, 600)
    cover_note = native_feed_cover_note(cards)
    if cover_note:
        header += "\n" + cover_note
    titles: list[str] = []
    for card in cards:
        ref = detail_refs.get(card["id"])
        url = ref if isinstance(ref, str) and ref.startswith("https://") else card.get("detail_message", {}).get("url")
        title = fit_escaped_text(card["title"], 180)
        if isinstance(url, str) and url.startswith("https://"):
            titles.append(f'<a href="{html_attribute_escape(url)}">{title}</a>')
        else:
            titles.append(f"<b>{title}</b>")

    base = "\n\n".join([header, *titles])
    if utf16_units(base) > max_units:
        die("native feed linked titles exceed Telegram's UTF-16 message budget")
    teaser_budget = max(0, (max_units - utf16_units(base) - 2 * len(cards)) // max(1, len(cards)))
    parts = [header]
    for card, title in zip(cards, titles):
        teaser = fit_escaped_text(card["feed_copy"].get("card_teaser"), teaser_budget)
        parts.append(f"{title}\n{teaser}" if teaser else title)
    rendered = "\n\n".join(parts)
    if utf16_units(rendered) > max_units:
        die("native feed exceeds Telegram's UTF-16 message budget")
    return rendered


def pulse_payload_sha256(pulse: dict[str, Any]) -> str:
    encoded = json.dumps(pulse, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_send_record(pulse_day: str) -> str:
    """Return journal_dir/pulse_YYYYMMDD.send.json for the requested local day."""
    day = validate_pulse_day(pulse_day).replace("-", "")
    return str(Path(DEFAULT_JOURNAL_DIR) / f"pulse_{day}.send.json")


def native_send_journal(
    pulse: dict[str, Any], *, pulse_day: str, cards_target: str, feed_target: str
) -> dict[str, Any]:
    return {
        "schema_version": "2",
        "delivery_mode": "telegram_native_v1",
        "status": "details_partial",
        "pulse_day": pulse_day,
        "target_card_count": len(pulse["cards"]),
        "desired_card_count": pulse["daily_target"]["count"],
        "payload_sha256": pulse_payload_sha256(pulse),
        "cards_target": cards_target,
        "feed_target": feed_target,
        "details": {},
        "detail_refs": {},
        "feed_message_id": None,
        "feed_telegraph": None,
        "in_flight": None,
    }


def validate_resumable_journal(existing: dict[str, Any], expected: dict[str, Any], card_ids: set[str]) -> None:
    actual = existing.get("target_card_count")
    desired = existing.get("desired_card_count", actual)
    if (
        isinstance(actual, bool) or not isinstance(actual, int)
        or isinstance(desired, bool) or not isinstance(desired, int)
        or desired not in PULSE_TARGET_COUNTS or not 1 <= actual <= desired
        or actual != len(card_ids) or desired != expected["desired_card_count"]
    ):
        die("send journal conflicts on actual/desired card count")
    for field in (
        "schema_version", "delivery_mode", "pulse_day", "target_card_count",
        "payload_sha256", "cards_target", "feed_target",
    ):
        if existing.get(field) != expected.get(field):
            die(f"send journal conflicts on {field}")
    if existing.get("in_flight") is not None:
        die("send journal has an unresolved in_flight operation; operator reconciliation is required")
    status = existing.get("status")
    if status == "complete":
        die("Pulse send journal is already complete")
    if status not in {"details_partial", "feed_pending"}:
        die("send journal has an invalid resumable status")
    details = existing.get("details")
    refs = existing.get("detail_refs")
    if (
        not isinstance(details, dict)
        or not isinstance(refs, dict)
        or set(details) != set(refs)
        or not set(details).issubset(card_ids)
    ):
        die("send journal has invalid detail progress")
    for card_id, message_id in details.items():
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            die(f"send journal detail {card_id!r} has an invalid message ID")
        if refs[card_id] != official_message_link(expected["cards_target"], message_id):
            die(f"send journal detail {card_id!r} has a non-official or mismatched link")
    if existing.get("feed_message_id") is not None or existing.get("feed_telegraph") is not None:
        die("incomplete native send journal contains a feed result")
    if status == "details_partial" and set(details) == card_ids:
        die("details_partial send journal already contains every detail")
    if status == "feed_pending" and set(details) != card_ids:
        die("feed_pending send journal is missing detail progress")


def validate_native_targets(cards_target: str, feed_target: str) -> None:
    if cards_target != NATIVE_CARDS_TARGET:
        die(f"native cards target must be exactly {NATIVE_CARDS_TARGET}")
    if feed_target != DEFAULT_FEED_TARGET:
        die(f"native feed target must be exactly {DEFAULT_FEED_TARGET}")
    if official_message_link(cards_target, 1) is None:
        die("native cards target cannot produce an official Telegram message link")


def bind_native_detail_targets(pulse: Any, cards_target: str) -> None:
    if not isinstance(pulse, dict) or not isinstance(pulse.get("cards"), list):
        return
    for index, card in enumerate(pulse["cards"], start=1):
        if not isinstance(card, dict):
            continue
        detail = card.get("detail_message")
        if not isinstance(detail, dict):
            continue
        target = detail.get("target")
        if as_text(target) and target != cards_target:
            die(f"card #{index} detail_message.target conflicts with explicit cards target")


def validate_local_cover_file(file_ref: str, *, allowed_roots: tuple[Path, ...] | None = None) -> None:
    path = Path(file_ref)
    if ".." in path.parts:
        die(f"local cover file must not use path traversal: {file_ref}")

    absolute_path = path if path.is_absolute() else Path.cwd() / path
    if any(candidate.is_symlink() for candidate in (absolute_path, *absolute_path.parents)):
        die(f"local cover file must not use symlinks: {file_ref}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        die(f"local cover file is missing: {file_ref}")

    roots = APPROVED_COVER_ROOTS if allowed_roots is None else allowed_roots
    approved_roots = tuple(Path(root).resolve() for root in roots)
    if not any(root in resolved.parents for root in approved_roots):
        die(f"local cover file must resolve beneath an approved root: {file_ref}")

    try:
        metadata = os.stat(resolved, follow_symlinks=False)
    except OSError:
        die(f"local cover file is unavailable: {file_ref}")
    if not stat.S_ISREG(metadata.st_mode):
        die(f"local cover must be a regular file: {file_ref}")
    if metadata.st_size == 0:
        die(f"local cover file must be nonempty: {file_ref}")
    if metadata.st_size > TELEGRAM_PHOTO_MAX_BYTES:
        die(f"local cover file exceeds Telegram's 10 MB photo limit: {file_ref}")

    suffix = resolved.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        die(f"local cover file extension must be .png, .jpg, or .jpeg: {file_ref}")
    try:
        with resolved.open("rb") as cover_file:
            signature = cover_file.read(8)
    except OSError:
        die(f"local cover file is unreadable: {file_ref}")
    is_png = signature == b"\x89PNG\r\n\x1a\n"
    is_jpeg = signature.startswith(b"\xff\xd8\xff")
    if (suffix == ".png" and not is_png) or (suffix in {".jpg", ".jpeg"} and not is_jpeg):
        die(f"local cover file signature does not match its extension: {file_ref}")


def validate_live_native_covers(
    pulse: dict[str, Any], *, allowed_roots: tuple[Path, ...] | None = None
) -> None:
    for card in pulse["cards"]:
        cover = card.get("cover_image") or {}
        if usable_cover_url(cover.get("url")):
            continue
        file_ref = as_text(cover.get("file"))
        if file_ref:
            validate_local_cover_file(file_ref, allowed_roots=allowed_roots)
            continue
        if not file_ref and not as_text(cover.get("url")) and cover.get("status") in TEXT_FALLBACK_COVER_STATUSES:
            continue
        die(f"card {card.get('id')!r} has no usable cover URL or local file")

    feed_cover = pulse.get("feed_cover_image")
    if feed_cover is None:
        return
    if usable_cover_url(feed_cover.get("url")):
        return
    feed_file = as_text(feed_cover.get("file"))
    if feed_file:
        validate_local_cover_file(feed_file, allowed_roots=allowed_roots)
        return
    if feed_cover.get("status") in TEXT_FALLBACK_COVER_STATUSES:
        return
    die("feed_cover_image has no usable cover URL or local file")


def send_details_and_feed(
    pulse: dict[str, Any], *, pulse_day: str, state_file: str, send_record: str,
    cards_target: str, feed_target: str = DEFAULT_FEED_TARGET, dry_run: bool = False,
) -> dict[str, Any]:
    """Send a strict 1.1 Pulse natively and atomically journal each operation."""
    validate_native_targets(cards_target, feed_target)
    bind_native_detail_targets(pulse, cards_target)
    if dry_run:
        temp_root = scratch_root().resolve()
        for label, path in (("state file", state_file), ("send record", send_record)):
            resolved = Path(path).resolve()
            if resolved != temp_root and temp_root not in resolved.parents:
                die(f"dry-run {label} must be under {temp_root}")
    normalized = normalize_pulse(
        pulse,
        strict_count=True,
        tools_target=cards_target,
        pulse_day=pulse_day,
        state_file=state_file,
    )
    for card in normalized["cards"]:
        card["detail_message"]["target"] = cards_target
    detail_deliveries: dict[str, dict[str, str]] = {}
    for card in normalized["cards"]:
        detail_html = render_detail_photo_caption(card)
        detail_units = utf16_units(detail_html)
        if detail_units > 3900:
            die(
                f"card {card['id']!r} full detail exceeds the 3900 UTF-16 unit limit "
                f"({detail_units} units)"
            )
        detail_deliveries[card["id"]] = {
            "html": detail_html,
            "mode": "photo_caption" if card_cover_ref(card) and detail_units <= 1000 else "text",
        }
    if not dry_run:
        validate_live_native_covers(normalized)
    render_feed_preview(normalized)
    expected = native_send_journal(
        normalized,
        pulse_day=validate_pulse_day(pulse_day),
        cards_target=cards_target,
        feed_target=feed_target,
    )
    def publish(record_path: Path) -> dict[str, Any]:
        if record_path.exists():
            journal = load_send_journal(str(record_path))
            validate_resumable_journal(journal, expected, {card["id"] for card in normalized["cards"]})
        else:
            journal = expected
            write_send_journal(str(record_path), journal)

        details = journal["details"]
        detail_refs = journal["detail_refs"]
        for index, card in enumerate(normalized["cards"], start=1):
            if card["id"] in details:
                continue
            journal["in_flight"] = {
                "operation": "detail",
                "card_id": card["id"],
                "target": cards_target,
                "started_at": now_iso(),
            }
            write_send_journal(str(record_path), journal)
            delivery = detail_deliveries[card["id"]]
            detail_html = delivery["html"]
            if dry_run:
                result = {"result": {"message_id": index}}
            elif delivery["mode"] == "photo_caption":
                cover_ref = card_cover_ref(card)
                if cover_ref and Path(cover_ref).exists():
                    result = telegram_send_local_photo(
                        cover_ref, caption=detail_html, target=cards_target, parse_mode="HTML",
                        reply_markup=card_action_markup()
                    )
                else:
                    result = telegram_send_photo(
                        cover_ref, caption=detail_html, target=cards_target, parse_mode="HTML",
                        reply_markup=card_action_markup()
                    )
            else:
                result = telegram_send_text(detail_html, target=cards_target, parse_mode="HTML",
                                            reply_markup=card_action_markup())
            message_id = telegram_result_message_id(result)
            details[card["id"]] = message_id
            detail_ref = official_message_link(cards_target, message_id)
            if detail_ref is None:
                die("native cards target cannot produce an official Telegram message link")
            detail_refs[card["id"]] = detail_ref
            journal["status"] = "feed_pending" if len(details) == len(normalized["cards"]) else "details_partial"
            journal["in_flight"] = None
            write_send_journal(str(record_path), journal)
            if not dry_run:
                time.sleep(0.25)

        uncut_feed_cover = feed_cover_ref(normalized)
        feed_html = render_feed_html(
            normalized,
            detail_refs=detail_refs,
            max_units=1000 if uncut_feed_cover else 3900,
        )
        journal["in_flight"] = {
            "operation": "feed",
            "target": feed_target,
            "started_at": now_iso(),
        }
        write_send_journal(str(record_path), journal)
        if dry_run:
            feed_result = {"result": {"message_id": len(normalized["cards"]) + 1}}
        elif uncut_feed_cover and Path(uncut_feed_cover).exists():
            feed_result = telegram_send_local_photo(
                uncut_feed_cover, caption=feed_html, target=feed_target, parse_mode="HTML"
            )
        elif uncut_feed_cover.startswith(("http://", "https://")):
            feed_result = telegram_send_photo(
                uncut_feed_cover, caption=feed_html, target=feed_target, parse_mode="HTML"
            )
        else:
            feed_result = telegram_send_text(feed_html, target=feed_target, parse_mode="HTML")
        journal["feed_message_id"] = telegram_result_message_id(feed_result)
        journal["status"] = "complete"
        journal["in_flight"] = None
        write_send_journal(str(record_path), journal)
        return journal

    return with_locked_send_journal(send_record, publish)


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    text = as_text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_send_journal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        die("send journal must be an object")
    journal = dict(value)
    if not journal.get("delivery_mode") and journal.get("feed_telegraph") is not None:
        journal["delivery_mode"] = "legacy_telegraph_v1"
    if not journal.get("status"):
        if journal.get("feed_message_id") is not None and journal.get("details"):
            journal["status"] = "complete"
        elif journal.get("details"):
            journal["status"] = "details_partial"
    return journal


def load_send_journal(path: str) -> dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        die(f"send journal not found: {path}")
    try:
        value = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"invalid send journal {path}: {exc}")
    return normalize_send_journal(value)


def write_send_journal(path: str, value: dict[str, Any]) -> Path:
    journal_path = Path(path)
    atomic_write_json(journal_path, normalize_send_journal(value))
    return journal_path


def with_locked_send_journal(send_record: str, publish: Any) -> Any:
    record_path = Path(send_record)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = record_path.with_name(record_path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return publish(record_path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def telegram_result_message_id(result: Any) -> int:
    message_id = result.get("result", {}).get("message_id") if isinstance(result, dict) else None
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        die("Telegram operation did not return a positive integer message ID")
    return message_id


def resolve_feedback_from_journal(path: str, message_id: int | str) -> tuple[str, str]:
    journal = load_send_journal(path)
    wanted = as_text(message_id)
    matches = [card_id for card_id, sent_id in (journal.get("details") or {}).items() if as_text(sent_id) == wanted]
    if len(matches) != 1 or not journal.get("pulse_day"):
        die(f"detail message ID {wanted!r} does not resolve to exactly one journal card")
    return validate_pulse_day(journal["pulse_day"]), as_text(matches[0])


def record_card_feedback(
    state_file: str,
    *,
    pulse_day: str | None = None,
    card_id: str | None = None,
    intent: str,
    text: str,
    event_key: str | None = None,
    mute_until: str | None = None,
    expires_at: str | None = None,
    send_journal: str | None = None,
    detail_message_id: int | str | None = None,
) -> dict[str, Any]:
    if detail_message_id is not None:
        if not send_journal:
            die("feedback by detail message ID requires --send-journal")
        resolved_day, resolved_card_id = resolve_feedback_from_journal(send_journal, detail_message_id)
        if pulse_day and validate_pulse_day(pulse_day) != resolved_day:
            die("explicit pulse day conflicts with send journal")
        if card_id and as_text(card_id) != resolved_card_id:
            die("explicit card ID conflicts with send journal")
        pulse_day, card_id = resolved_day, resolved_card_id
    day = validate_pulse_day(pulse_day or "")
    normalized_card_id = as_text(card_id)
    if not normalized_card_id:
        die("feedback requires a card ID")
    normalized_intent = as_text(intent)
    if normalized_intent not in FEEDBACK_INTENTS:
        die(f"unknown feedback intent {normalized_intent!r}")
    if not isinstance(text, str) or not text.strip():
        die("feedback requires non-empty raw text")
    for label, value in (("mute_until", mute_until), ("expires_at", expires_at)):
        if value is not None and parse_iso_datetime(value) is None:
            die(f"feedback {label} must be an ISO timestamp")
    event = {
        "pulse_day": day,
        "card_id": normalized_card_id,
        "intent": normalized_intent,
        "raw_text": text,
        "recorded_at": now_iso(),
    }
    if event_key:
        event["event_key"] = as_text(event_key)
    if mute_until:
        event["mute_until"] = mute_until
    if expires_at:
        event["expires_at"] = expires_at

    def update(path: Path, state: dict[str, Any]) -> dict[str, Any]:
        feedback = state.get("card_feedback", [])
        if not isinstance(feedback, list):
            die("malformed card_feedback state: expected an array")
        state["card_feedback"] = [*feedback, event]
        atomic_write_json(path, state)
        return dict(event)

    return with_locked_state(state_file, update)


def save_preference(choice: str, state_file: str, source: str = "manual") -> dict[str, Any]:
    choice = normalize_preference_choice(choice)
    if choice not in DEFAULT_CHOICES:
        die(f"unknown preference choice {choice!r}; expected one of: {', '.join(DEFAULT_CHOICES)}")

    def update(path: Path, state: dict[str, Any]) -> dict[str, Any]:
        timestamp = now_iso()
        state.update({
            "last_preference": choice,
            "updated_at": timestamp,
            "source": source,
            "preference_recorded_at": timestamp,
        })
        atomic_write_json(path, state)
        return state

    return with_locked_state(state_file, update)


def load_preference(state_file: str) -> dict[str, Any]:
    return load_state_document(Path(state_file))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Personal Pulse runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate")
    p_val.add_argument("input", nargs="?", default="-")
    p_val.add_argument("--pretty", action="store_true")
    p_val.add_argument("--strict-count", action="store_true")
    p_val.add_argument("--pulse-day")
    p_val.add_argument("--state-file", default=DEFAULT_STATE_FILE)

    p_target = sub.add_parser("daily-target")
    p_target.add_argument("action", choices=["get-or-create", "get"])
    p_target.add_argument("--pulse-day")
    p_target.add_argument("--state-file", default=DEFAULT_STATE_FILE)

    p_det = sub.add_parser("render-details")
    p_det.add_argument("input", nargs="?", default="-")
    p_det.add_argument("--out-dir", required=True)

    p_feed = sub.add_parser("render-feed")
    p_feed.add_argument("input", nargs="?", default="-")
    p_feed.add_argument("--detail-refs-json", default="{}")

    p_send = sub.add_parser("send")
    p_send.add_argument("input", nargs="?", default="-")
    p_send.add_argument("--pulse-day", required=True)
    p_send.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    p_send.add_argument("--send-record", help="Default: journal_dir/pulse_YYYYMMDD.send.json")
    p_send.add_argument("--cards-target", default=NATIVE_CARDS_TARGET)
    p_send.add_argument("--feed-target", default=DEFAULT_FEED_TARGET)
    p_send.add_argument("--dry-run", action="store_true")

    p_pref = sub.add_parser("preference")
    p_pref.add_argument("action", choices=["get", "set"])
    p_pref.add_argument("choice", nargs="?")
    p_pref.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    p_pref.add_argument("--source", default="manual")

    p_feedback = sub.add_parser("feedback")
    p_feedback.add_argument("action", choices=["record"])
    p_feedback.add_argument("--pulse-day")
    p_feedback.add_argument("--card-id")
    p_feedback.add_argument("--intent", required=True, choices=sorted(FEEDBACK_INTENTS))
    p_feedback.add_argument("--text", required=True)
    p_feedback.add_argument("--event-key")
    p_feedback.add_argument("--mute-until")
    p_feedback.add_argument("--expires-at")
    p_feedback.add_argument("--send-journal")
    p_feedback.add_argument("--detail-message-id")
    p_feedback.add_argument("--state-file", default=DEFAULT_STATE_FILE)

    args = parser.parse_args(argv)
    if args.cmd == "validate":
        pulse = normalize_pulse(
            load_jsonish(args.input),
            strict_count=args.strict_count,
            tools_target=NATIVE_CARDS_TARGET if args.strict_count else DEFAULT_TOOLS_TARGET,
            pulse_day=args.pulse_day,
            state_file=args.state_file,
        )
        json.dump(pulse, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
        print()
    elif args.cmd == "daily-target":
        if args.action == "get-or-create":
            result = get_or_create_daily_target(args.state_file, args.pulse_day)
        else:
            if not args.pulse_day:
                die("daily-target get requires --pulse-day")
            result = get_daily_target(args.state_file, args.pulse_day)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.cmd == "render-details":
        pulse = normalize_pulse(load_jsonish(args.input))
        out = Path(args.out_dir)
        for card in pulse["cards"]:
            card_id = card["id"]
            if card_id in {".", ".."} or "/" in card_id or "\\" in card_id:
                die(f"card ID is not a safe detail filename: {card_id!r}")
        out.mkdir(parents=True, exist_ok=True)
        index = {}
        for card in pulse["cards"]:
            path = out / f"{card['id']}.md"
            path.write_text(render_detail(card) + "\n", encoding="utf-8")
            index[card["id"]] = str(path)
        print(json.dumps(index, ensure_ascii=False, indent=2))
    elif args.cmd == "render-feed":
        refs = json.loads(args.detail_refs_json)
        pulse = normalize_pulse(load_jsonish(args.input))
        print(render_feed_preview(pulse, detail_refs=refs))
    elif args.cmd == "send":
        pulse = load_jsonish(args.input)
        result = send_details_and_feed(
            pulse,
            pulse_day=args.pulse_day,
            state_file=args.state_file,
            send_record=args.send_record or default_send_record(args.pulse_day),
            cards_target=args.cards_target,
            feed_target=args.feed_target,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.cmd == "preference":
        if args.action == "get":
            print(json.dumps(load_preference(args.state_file), ensure_ascii=False, indent=2))
        elif args.action == "set":
            if not args.choice:
                die("preference set requires choice")
            print(json.dumps(save_preference(args.choice, args.state_file, args.source), ensure_ascii=False, indent=2))
    elif args.cmd == "feedback":
        result = record_card_feedback(
            args.state_file,
            pulse_day=args.pulse_day,
            card_id=args.card_id,
            intent=args.intent,
            text=args.text,
            event_key=args.event_key,
            mute_until=args.mute_until,
            expires_at=args.expires_at,
            send_journal=args.send_journal,
            detail_message_id=args.detail_message_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Severity Beacon Lights — Home Assistant Supervisor add-on.

Polls Zabbix problem.get (Severity_Beacon semantics) and drives HA RGB lights
via the Supervisor Core API proxy (SUPERVISOR_TOKEN).

Ingress UI (port 8099): multi-light registry, mute buttons, Base/Recovery,
per-severity Steady|Pattern (+ targets / audio), black→off. Options hot-reload
from /data/options.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from features_v7 import (
        alert_audio_blocked,
        check_mute_timeout,
        clear_audio_fire,
        clear_mute,
        consume_controls_changed,
        controls_changed,
        fetch_ha_states,
        filter_entities,
        get_mute_snapshot,
        get_runtime_status,
        lights_commands_held,
        load_mute_state,
        maybe_notify,
        migrate_lights_options,
        parse_entity_list,
        play_severity_audio,
        poll_button_groups,
        registry_lights,
        set_audio_suppressed,
        set_lights_rgb,
        set_lights_suppressed,
        set_muted,
        signal_controls_changed,
        speak_button_status,
        stop_media_player,
        targets_for_severity,
        update_runtime_status,
    )
except ImportError:  # when installed as /usr/bin/severity_beacon.py
    sys.path.insert(0, "/usr/bin")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from features_v7 import (  # type: ignore
        alert_audio_blocked,
        check_mute_timeout,
        clear_audio_fire,
        clear_mute,
        consume_controls_changed,
        controls_changed,
        fetch_ha_states,
        filter_entities,
        get_mute_snapshot,
        get_runtime_status,
        lights_commands_held,
        load_mute_state,
        maybe_notify,
        migrate_lights_options,
        parse_entity_list,
        play_severity_audio,
        poll_button_groups,
        registry_lights,
        set_audio_suppressed,
        set_lights_rgb,
        set_lights_suppressed,
        set_muted,
        signal_controls_changed,
        speak_button_status,
        stop_media_player,
        targets_for_severity,
        update_runtime_status,
    )

OPTIONS_PATH = Path("/data/options.json")
WWW_DIR = Path(os.environ.get("SEVERITY_BEACON_WWW", "/usr/share/severity_beacon/www"))
# Local-dev fallback: sibling www/ next to this script
if not WWW_DIR.is_dir():
    try:
        _local = Path(__file__).resolve().parent / "www"
    except NameError:
        _local = Path.cwd() / "www"
    if _local.is_dir():
        WWW_DIR = _local

HA_API = os.environ.get("HA_API_BASE", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API_BASE", "http://supervisor").rstrip("/")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
INGRESS_PORT = int(os.environ.get("INGRESS_PORT", "8099"))
INGRESS_HOST = os.environ.get("INGRESS_HOST", "0.0.0.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("severity_beacon")

TIER_KEYS = (
    "none",
    "unclassified",
    "info",
    "warning",
    "average",
    "high",
    "disaster",
)
SEV_TO_KEY: dict[int | None, str] = {
    None: "none",
    0: "unclassified",
    1: "info",
    2: "warning",
    3: "average",
    4: "high",
    5: "disaster",
}
VALID_MODES = frozenset({"steady", "pattern"})
LEGACY_MODE_MAP = {
    "flash": "pattern",
    "fade": "pattern",
    "strobe": "pattern",
}
VALID_TRANSITIONS = frozenset({"snap", "fade"})

# Idle resting colour (0.6+). Was steady green via color_none in ≤0.5.
DEFAULT_BASE_COLOR: list[int] = [255, 255, 255]
DEFAULT_RECOVERY_COLOR: list[int] = [0, 220, 160]
DEFAULT_RECOVERY_HOLD = 3.0
DEFAULT_RECOVERY_FADE = 1.0
DEFAULT_RECOVERY_TRANSITION = "fade"
# Former default none green — used only when detecting unmigrated installs.
_LEGACY_NONE_GREEN: list[int] = [0, 255, 0]

DEFAULT_COLORS: dict[str, list[int]] = {
    "none": list(DEFAULT_BASE_COLOR),
    "none_alt": [200, 200, 200],
    "unclassified": [255, 220, 0],
    "unclassified_alt": [0, 255, 0],
    "info": [0, 120, 255],
    "info_alt": [0, 255, 0],
    "warning": [255, 140, 0],
    "warning_alt": [255, 200, 0],
    "average": [255, 80, 0],
    "average_alt": [255, 140, 0],
    "high": [255, 0, 0],
    "high_alt": [255, 80, 0],
    "disaster": [255, 0, 0],
    "disaster_alt": [255, 140, 0],
}

DEFAULT_MODES: dict[str, str] = {
    "none": "steady",
    "unclassified": "pattern",
    "info": "pattern",
    "warning": "steady",
    "average": "steady",
    "high": "steady",
    "disaster": "pattern",
}

# Window 0 = loop while severity remains max. Non-zero = play N seconds then settle.
DEFAULT_WINDOWS: dict[str, float] = {
    "none": 0.0,
    "unclassified": 3.0,
    "info": 6.0,
    "warning": 0.0,
    "average": 0.0,
    "high": 0.0,
    "disaster": 0.0,
}

LEGACY_PERIOD_KEYS: dict[str, str] = {
    "none": "period_none",
    "unclassified": "unclassified_flash_seconds",
    "warning": "period_warning",
    "average": "period_average",
    "high": "period_high",
    "disaster": "disaster_fade_seconds",
}

DEFAULT_TIMINGS_INFO_HOLD = 3.0
DEFAULT_TIMINGS_INFO_FADE = 3.0

_options_lock = threading.Lock()
_options_cache: dict[str, Any] = {}

_test_lock = threading.Lock()
_test_request: dict[str, Any] | None = None
DEFAULT_TEST_SECONDS = 8.0
MAX_TEST_SECONDS = 15.0

KEY_TO_SEV: dict[str, int | None] = {
    "none": None,
    "clear": None,
    "all-clear": None,
    "base": None,
    "unclassified": 0,
    "info": 1,
    "warning": 2,
    "average": 3,
    "high": 4,
    "disaster": 5,
}

# Sentinel returned when recovery is aborted for a pending Test request
_RECOVERY_ABORTED_TEST = object()
# Sentinel returned when lights or full suppress takes the lights mid-recovery
_RECOVERY_ABORTED_HELD = object()

# Button helpers are watched on this cadence, separate from poll_interval_seconds.
BUTTON_POLL_SECONDS = 1.0

# Main-loop light writes no-op while lights are held. The button thread sets
# this so its own Base restore still goes out.
_force_lights = threading.local()


def load_options() -> dict[str, Any]:
    if not OPTIONS_PATH.is_file():
        raise SystemExit(f"Missing options file: {OPTIONS_PATH}")
    return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))


def write_options_file(opts: dict[str, Any]) -> None:
    OPTIONS_PATH.write_text(json.dumps(opts, indent=2) + "\n", encoding="utf-8")


def set_options_cache(opts: dict[str, Any]) -> None:
    with _options_lock:
        _options_cache.clear()
        _options_cache.update(opts)


def get_options_cache() -> dict[str, Any]:
    with _options_lock:
        if _options_cache:
            return dict(_options_cache)
    if OPTIONS_PATH.is_file():
        opts = load_options()
        set_options_cache(opts)
        return opts
    return {}


def parse_rgb(value: str, fallback: list[int]) -> list[int]:
    try:
        parts = [int(p.strip()) for p in str(value).split(",")]
        if len(parts) != 3 or any(p < 0 or p > 255 for p in parts):
            raise ValueError(value)
        return parts
    except (TypeError, ValueError):
        log.warning("Invalid RGB %r — using %s", value, fallback)
        return list(fallback)


def parse_severities(value: str) -> list[int]:
    return [int(x.strip()) for x in str(value).split(",") if x.strip()]


def parse_seconds(value: Any, fallback: float, minimum: float = 0.0) -> float:
    try:
        n = float(value)
        if n < minimum:
            return minimum
        return n
    except (TypeError, ValueError):
        return fallback


def parse_mode(value: Any, fallback: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in VALID_MODES:
        return mode
    if mode in LEGACY_MODE_MAP:
        return LEGACY_MODE_MAP[mode]
    return fallback


def parse_transition(value: Any, fallback: str = "snap") -> str:
    t = str(value or "").strip().lower()
    if t in VALID_TRANSITIONS:
        return t
    return fallback


def parse_pattern(value: Any, *, default_transition: str = "snap") -> list[dict[str, Any]]:
    """Parse pattern JSON into [{rgb, duration_s, transition, fade_s}, ...]."""
    if value is None:
        return []
    raw: Any = value
    default_fade = 0.5
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Invalid pattern JSON %r", value[:120])
            return []
    if isinstance(raw, dict):
        if raw.get("transition") in VALID_TRANSITIONS:
            default_transition = str(raw["transition"])
        if raw.get("fade_s") is not None:
            default_fade = max(0.05, parse_seconds(raw.get("fade_s"), 0.5, 0.05))
        raw = raw.get("steps") or []
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            if "rgb" in item and isinstance(item["rgb"], (list, tuple)) and len(item["rgb"]) == 3:
                rgb = [int(item["rgb"][0]), int(item["rgb"][1]), int(item["rgb"][2])]
            else:
                rgb = [int(item.get("r", 0)), int(item.get("g", 0)), int(item.get("b", 0))]
            if any(c < 0 or c > 255 for c in rgb):
                continue
            dur = float(item.get("duration_s", item.get("duration", 0.25)))
            transition = parse_transition(item.get("transition"), default_transition)
            fade_s = max(
                0.05,
                parse_seconds(item.get("fade_s", item.get("fade_seconds")), default_fade, 0.05),
            )
            step_targets = parse_entity_list(item.get("targets"))
            steps.append(
                {
                    "rgb": rgb,
                    "duration_s": max(0.0, dur),
                    "transition": transition,
                    "fade_s": fade_s,
                    "targets": step_targets,
                }
            )
        except (TypeError, ValueError):
            continue
    return steps


def http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    method: str | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_method = method or ("POST" if payload is not None else "GET")
    req = urllib.request.Request(url, data=data, method=req_method)
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def supervisor_headers() -> dict[str, str]:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not set")
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}


def resolve_base_rgb(opts: dict[str, Any]) -> list[int]:
    """
    Idle Base colour. New default is white.
    If color_base is unset: keep a customized color_none; otherwise replace the
    old default steady-green none with white (documented in 0.6.0).
    If Supervisor injects the new white color_base default while color_none is a
    customized non-green value, prefer that customized none colour.
    """
    none_rgb: list[int] | None = None
    if opts.get("color_none") not in (None, ""):
        none_rgb = parse_rgb(str(opts.get("color_none")), DEFAULT_BASE_COLOR)

    if opts.get("color_base") not in (None, ""):
        base = parse_rgb(str(opts.get("color_base")), DEFAULT_BASE_COLOR)
        if (
            base == DEFAULT_BASE_COLOR
            and none_rgb is not None
            and none_rgb not in (_LEGACY_NONE_GREEN, DEFAULT_BASE_COLOR)
        ):
            return none_rgb
        return base
    if none_rgb is not None and none_rgb != _LEGACY_NONE_GREEN:
        return none_rgb
    return list(DEFAULT_BASE_COLOR)


def resolve_recovery_rgb(opts: dict[str, Any]) -> list[int]:
    if opts.get("color_recovery") not in (None, ""):
        return parse_rgb(str(opts.get("color_recovery")), DEFAULT_RECOVERY_COLOR)
    return list(DEFAULT_RECOVERY_COLOR)


def load_recovery_settings(opts: dict[str, Any]) -> dict[str, Any]:
    transition = str(opts.get("recovery_transition") or DEFAULT_RECOVERY_TRANSITION).strip().lower()
    if transition not in VALID_TRANSITIONS:
        transition = DEFAULT_RECOVERY_TRANSITION
    return {
        "base": resolve_base_rgb(opts),
        "recovery": resolve_recovery_rgb(opts),
        "hold_s": parse_seconds(opts.get("recovery_hold_seconds"), DEFAULT_RECOVERY_HOLD, 0.0),
        "fade_s": parse_seconds(opts.get("recovery_fade_seconds"), DEFAULT_RECOVERY_FADE, 0.0),
        "transition": transition,
    }


def migrate_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Map legacy modes / periods into steady|pattern + window_*; seed Base/Recovery."""
    out = migrate_lights_options(dict(opts))

    base = resolve_base_rgb(out)
    out["color_base"] = ",".join(str(c) for c in base)
    # Keep color_none aligned with Base so legacy settle paths stay consistent
    out["color_none"] = out["color_base"]
    if out.get("color_recovery") in (None, ""):
        out["color_recovery"] = ",".join(str(c) for c in DEFAULT_RECOVERY_COLOR)
    if out.get("recovery_hold_seconds") in (None, ""):
        out["recovery_hold_seconds"] = DEFAULT_RECOVERY_HOLD
    if out.get("recovery_fade_seconds") in (None, ""):
        out["recovery_fade_seconds"] = DEFAULT_RECOVERY_FADE
    if out.get("recovery_transition") in (None, ""):
        out["recovery_transition"] = DEFAULT_RECOVERY_TRANSITION
    else:
        t = str(out.get("recovery_transition") or "").strip().lower()
        out["recovery_transition"] = t if t in VALID_TRANSITIONS else DEFAULT_RECOVERY_TRANSITION

    for key in TIER_KEYS:
        raw_mode = str(out.get(f"mode_{key}") or "").strip().lower()
        out[f"mode_{key}"] = parse_mode(raw_mode, DEFAULT_MODES[key])

        win_key = f"window_{key}"
        if out.get(win_key) is None or out.get(win_key) == "":
            if raw_mode == "fade":
                out[win_key] = 0.0
            elif key == "info":
                hold = parse_seconds(out.get("info_hold_seconds"), DEFAULT_TIMINGS_INFO_HOLD, 0.0)
                fade = parse_seconds(out.get("info_fade_seconds"), DEFAULT_TIMINGS_INFO_FADE, 0.0)
                out[win_key] = hold + fade if (hold + fade) > 0 else DEFAULT_WINDOWS["info"]
            elif key in LEGACY_PERIOD_KEYS and out.get(LEGACY_PERIOD_KEYS[key]) is not None:
                # Continuous disaster fade defaulted to looping
                if key == "disaster" and raw_mode in ("", "fade"):
                    out[win_key] = 0.0
                else:
                    out[win_key] = parse_seconds(
                        out.get(LEGACY_PERIOD_KEYS[key]), DEFAULT_WINDOWS[key], 0.0
                    )
            else:
                out[win_key] = DEFAULT_WINDOWS[key]

        # Enrich bare legacy step arrays with transition inferred from old mode
        raw_pat = out.get(f"pattern_{key}")
        steps = parse_pattern(raw_pat)
        if steps and raw_mode in ("flash", "strobe", "fade", "pattern"):
            inferred = "snap" if raw_mode in ("flash", "strobe") else "fade"
            needs_enrich = False
            try:
                loaded = (
                    json.loads(raw_pat)
                    if isinstance(raw_pat, str) and raw_pat.strip()
                    else raw_pat
                )
                if isinstance(loaded, list):
                    needs_enrich = not any(
                        isinstance(x, dict) and x.get("transition") in VALID_TRANSITIONS
                        for x in loaded
                    )
            except (TypeError, json.JSONDecodeError):
                needs_enrich = False
            if needs_enrich:
                out[f"pattern_{key}"] = json.dumps(
                    [
                        {
                            "r": s["rgb"][0],
                            "g": s["rgb"][1],
                            "b": s["rgb"][2],
                            "duration_s": s["duration_s"],
                            "transition": inferred,
                            "fade_s": s.get("fade_s", 0.5),
                        }
                        for s in steps
                    ]
                )
    return out


# Keys accepted by config.yaml schema (extras stripped before Supervisor POST)
SCHEMA_OPTION_KEYS = frozenset(
    {
        "zabbix_url",
        "zabbix_api_token",
        "light_entity_id",
        "lights",
        "mute_entities",
        "audio_toggle_entities",
        "lights_toggle_entities",
        "mute_timeout_minutes",
        "escalate_only",
        "audio_enabled",
        "audio_media_player",
        "audio_tts_entity",
        "audio_volume",
        "audio_recovery_chime",
        "quiet_hours_start",
        "quiet_hours_end",
        "notify_service",
        "notify_on_severities",
        "poll_interval_seconds",
        "host_group_id",
        "severities",
        "brightness",
        "color_base",
        "color_recovery",
        "recovery_hold_seconds",
        "recovery_fade_seconds",
        "recovery_transition",
        *[f"color_{k}" for k in TIER_KEYS],
        *[f"color_{k}_alt" for k in TIER_KEYS],
        *[f"mode_{k}" for k in TIER_KEYS],
        *[f"window_{k}" for k in TIER_KEYS],
        *[f"pattern_{k}" for k in TIER_KEYS],
        *[f"targets_{k}" for k in TIER_KEYS],
        *[f"audio_mode_{k}" for k in TIER_KEYS],
        *[f"audio_media_{k}" for k in TIER_KEYS],
        *[f"audio_tts_{k}" for k in TIER_KEYS],
        *[f"audio_tts_read_body_{k}" for k in TIER_KEYS],
        *[f"audio_repeat_{k}" for k in TIER_KEYS],
        *[f"audio_player_{k}" for k in TIER_KEYS],
        *[f"audio_content_type_{k}" for k in TIER_KEYS],
        # Legacy timing keys kept optional in schema for smooth upgrades
        "period_none",
        "unclassified_flash_seconds",
        "info_hold_seconds",
        "info_fade_seconds",
        "period_warning",
        "period_average",
        "period_high",
        "disaster_fade_seconds",
    }
)


def options_for_supervisor(opts: dict[str, Any]) -> dict[str, Any]:
    """Filter to schema keys and coerce modes to steady|pattern."""
    cleaned = migrate_options(opts)
    out: dict[str, Any] = {}
    for key, val in cleaned.items():
        if key not in SCHEMA_OPTION_KEYS:
            continue
        if key.startswith("mode_"):
            out[key] = parse_mode(val, DEFAULT_MODES.get(key[5:], "steady"))
        else:
            out[key] = val
    # Ensure required Base / Recovery + per-tier keys exist
    out.setdefault("color_base", ",".join(str(c) for c in DEFAULT_BASE_COLOR))
    out.setdefault("color_recovery", ",".join(str(c) for c in DEFAULT_RECOVERY_COLOR))
    out.setdefault("recovery_hold_seconds", DEFAULT_RECOVERY_HOLD)
    out.setdefault("recovery_fade_seconds", DEFAULT_RECOVERY_FADE)
    out.setdefault("recovery_transition", DEFAULT_RECOVERY_TRANSITION)
    # Keep none colour synced with Base
    out["color_none"] = out.get("color_base") or out.get("color_none")
    for tier in TIER_KEYS:
        out.setdefault(f"mode_{tier}", DEFAULT_MODES[tier])
        out.setdefault(f"window_{tier}", DEFAULT_WINDOWS[tier])
        out.setdefault(f"color_{tier}", ",".join(str(c) for c in DEFAULT_COLORS[tier]))
        out.setdefault(
            f"color_{tier}_alt",
            ",".join(str(c) for c in DEFAULT_COLORS[f"{tier}_alt"]),
        )
        out.setdefault(f"pattern_{tier}", "")
    return out


def persist_options_via_supervisor(partial: dict[str, Any]) -> dict[str, Any]:
    """
    Merge partial options into current, POST to Supervisor, and write options.json
    so the poller hot-reloads without waiting for a restart.
    """
    current = get_options_cache()
    if not current and OPTIONS_PATH.is_file():
        current = load_options()

    merged = dict(current)
    for key, val in partial.items():
        if key == "zabbix_api_token" and (val is None or str(val).strip() == ""):
            continue
        merged[key] = val

    merged = options_for_supervisor(merged)

    try:
        result = http_json(
            f"{SUPERVISOR_API}/addons/self/options",
            {"options": merged},
            headers=supervisor_headers(),
            timeout=20,
        )
        if isinstance(result, dict) and result.get("result") not in (None, "ok"):
            log.warning("Supervisor options response: %s", result)
    except Exception as exc:  # noqa: BLE001 — still write local file for live reload
        log.error("Supervisor options API failed: %s — writing options.json anyway", exc)

    write_options_file(merged)
    set_options_cache(merged)
    return merged


def zabbix_rpc(api_url: str, token: str, method: str, params: dict[str, Any]) -> Any:
    result = http_json(
        api_url,
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected Zabbix response: {result!r}")
    if "error" in result:
        err = result["error"]
        raise RuntimeError(
            f"Zabbix {method} error: {err.get('data') or err.get('message') or err}"
        )
    return result.get("result")


def enabled_host_ids(api_url: str, token: str, group_id: str) -> list[str]:
    hosts = zabbix_rpc(
        api_url,
        token,
        "host.get",
        {
            "output": ["hostid", "status"],
            "groupids": [group_id],
            "filter": {"status": ["0"]},
        },
    )
    return [h["hostid"] for h in (hosts or [])]


# Trailing abbreviated MAC (no colons) sometimes appended for field reconfig of
# APs/switches. Only strip when the last N chars are hex digits.
HOST_MAC_SUFFIX_LEN = 6


def strip_host_mac_suffix(host: str, suffix_len: int = HOST_MAC_SUFFIX_LEN) -> str:
    """
    Drop a trailing MAC-ref (N hex digits, no colons) from a Zabbix hostname for TTS.

    Only strips when the last ``suffix_len`` characters match ``[0-9A-Fa-f]{N}``.
    Names ending in non-hex tokens (e.g. SERVER, CORE01) are left unchanged.
    """
    name = str(host or "").strip()
    if suffix_len <= 0 or len(name) <= suffix_len:
        return name
    suffix = name[-suffix_len:]
    if not re.fullmatch(rf"[0-9A-Fa-f]{{{suffix_len}}}", suffix):
        return name
    return name[:-suffix_len].rstrip("-_. ")


def normalize_problem_template(name: str) -> str:
    """Collapse volatile tokens so similar problems group for TTS summaries."""
    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"\bPort\s+\d+\b", "Port N", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSFP\s+\d+\b", "SFP N", text, flags=re.IGNORECASE)
    text = re.sub(r"\bWAN\s+\d+\b", "WAN N", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(\.\d+)?\s*%", "N%", text)
    text = re.sub(r"\b\d+\s*[x×]\s*\d+\b", "NxN", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "N", text)
    return text.casefold()


def is_offline_problem(name: str) -> bool:
    """True when the problem is a host-down / unreachable style alert."""
    text = str(name or "").casefold()
    if not text:
        return False
    needles = (
        "unavailable by ping",
        "icmp ping",
        "host is unreachable",
        "host unreachable",
        "not reachable",
        "agent is unavailable",
        "zabbix agent is not available",
    )
    return any(n in text for n in needles) or text.strip() in {"offline", "is offline"}


def classify_host_role(host: dict[str, Any] | None, raw_host: str = "") -> str:
    """
    Speakable device class for summaries (switch / Access Point), or \"\".

    Prefers Zabbix host groups (.../Switch, .../AP); falls back to name tokens
    like -SW- / U7 / U6. Skips Unifi-API (management, not an AP).
    """
    groups: list[str] = []
    if isinstance(host, dict):
        for g in host.get("groups") or []:
            if isinstance(g, dict):
                groups.append(str(g.get("name") or ""))
            elif isinstance(g, str):
                groups.append(g)
        if not raw_host:
            raw_host = str(host.get("name") or host.get("host") or "")

    for gname in groups:
        low = gname.casefold()
        if "unifi-api" in low:
            continue
        # Match leaf segment .../Switch or .../AP
        leaf = low.rsplit("/", 1)[-1].strip()
        if leaf == "switch" or low.endswith("/switch"):
            return "switch"
        if leaf == "ap" or low.endswith("/ap"):
            return "Access Point"

    name = str(raw_host or "")
    if re.search(r"(?i)(?:^|[\s_-])sw(?:[\s_-]|$)|-sw-", name):
        return "switch"
    if re.search(r"(?i)(?:-u[67]\b|-uap\b|u6-|u7-|access\s*point|\bap-)", name):
        return "Access Point"
    return ""


def rewrite_uptime_for_tts(text: str) -> str:
    """
    Speak Zabbix uptime thresholds clearly.

    In uptime context, trailing ``m`` means minutes (not meters), e.g.
    ``uptime < 10m`` → ``uptime less than 10 minutes``.
    Only rewrites near the word uptime; unrelated ``m`` units are left alone.
    """
    raw = str(text or "")
    if not raw or "uptime" not in raw.lower():
        return raw

    def _minutes_word(n: str) -> str:
        try:
            return "minute" if float(n) == 1 else "minutes"
        except ValueError:
            return "minutes"

    def _cmp_words(op: str) -> str:
        return {
            "<": "less than",
            "<=": "less than or equal to",
            ">": "greater than",
            ">=": "greater than or equal to",
            "=": "equal to",
            "==": "equal to",
        }.get(op, op)

    def _repl(match: re.Match[str]) -> str:
        head = match.group(1)  # includes "uptime" through optional comparator spacing
        op = match.group(2) or ""
        num = match.group(3)
        unit = _minutes_word(num)
        if op:
            return f"{head}{_cmp_words(op)} {num} {unit}"
        return f"{head}{num} {unit}"

    # Immediately after "uptime" (optional spaces/parens): optional comparator + Nm
    rewritten = re.sub(
        r"(uptime\b[\s(]*)([<>]=?|=)?\s*(\d+(?:\.\d+)?)\s*m\b",
        _repl,
        raw,
        flags=re.IGNORECASE,
    )
    if rewritten != raw:
        log.debug("TTS uptime rewrite %r → %r", raw, rewritten)
    return rewritten


def split_embedded_host(problem_name: str) -> tuple[str, str]:
    """
    When Zabbix API cannot return hosts, many problem titles still begin with the
    hostname (e.g. \"WoodlandGR-U7L7A7FFB has been restarted…\").
    Returns (raw_host, remaining_problem_text).
    """
    text = str(problem_name or "").strip()
    if not text:
        return "", ""
    # Prefer \"Host: rest\" / \"Host - rest\"
    for sep in (": ", " - ", " – ", " — "):
        if sep in text:
            left, right = text.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right and len(left) > HOST_MAC_SUFFIX_LEN and " " not in left:
                return left, right
    # Else first whitespace token as host when it looks like a device name
    parts = text.split(None, 1)
    if len(parts) == 2:
        token, rest = parts[0].strip(), parts[1].strip()
        if (
            rest
            and len(token) > HOST_MAC_SUFFIX_LEN
            and ("-" in token or any(ch.isdigit() for ch in token))
            and any(ch.isalpha() for ch in token)
        ):
            return token, rest
    return "", text


def _trigger_ids_for_problems(problems: list[dict[str, Any]]) -> list[str]:
    """objectids for trigger-sourced problems (source 0 / unset)."""
    return sorted(
        {
            str(p.get("objectid"))
            for p in problems
            if p.get("objectid") is not None
            and str(p.get("source", "0")) in ("0", "")
            and str(p.get("object", "0")) in ("0", "")
        }
    )


def _apply_hosts_by_trigger(
    problems: list[dict[str, Any]],
    by_trigger: dict[str, list[dict[str, Any]]],
) -> None:
    for problem in problems:
        if problem.get("hosts"):
            continue
        tid = str(problem.get("objectid") or "")
        hosts = by_trigger.get(tid) or []
        if hosts:
            problem["hosts"] = hosts


def enrich_problems_with_hosts(
    api_url: str,
    token: str,
    problems: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Attach host names for TTS.

    problem.get has no selectHosts on many Zabbix versions. Limited API tokens often
    deny trigger.get / event.get but still allow host.get — resolve hosts via
    host.get(triggerids=objectid) per trigger (reliable for ICMP/ping titles that
    do not embed the hostname). Fall back to trigger.get / event.get when allowed,
    then to title parsing in format_alert_bodies.
    """
    if not problems:
        return problems

    out: list[dict[str, Any]] = [dict(p) for p in problems]
    trigger_ids = _trigger_ids_for_problems(out)

    # 1) host.get(triggerids=…) — works with the common read-only problem+host token
    missing_triggers = [
        tid
        for tid in trigger_ids
        if not any(
            p.get("hosts") and str(p.get("objectid") or "") == tid for p in out
        )
    ]
    by_trigger: dict[str, list[dict[str, Any]]] = {}
    host_get_errors = 0
    for tid in missing_triggers:
        try:
            hosts = (
                zabbix_rpc(
                    api_url,
                    token,
                    "host.get",
                    {
                        "triggerids": [tid],
                        "output": ["hostid", "host", "name"],
                        "selectGroups": ["name"],
                    },
                )
                or []
            )
            cleaned = [h for h in hosts if isinstance(h, dict)]
            if cleaned:
                by_trigger[tid] = cleaned
        except Exception as exc:  # noqa: BLE001
            host_get_errors += 1
            log.warning("host.get host enrich failed for trigger %s: %s", tid, exc)
    if by_trigger:
        _apply_hosts_by_trigger(out, by_trigger)
        log.info(
            "TTS host enrich via host.get: %s/%s triggers resolved",
            len(by_trigger),
            len(missing_triggers) or len(trigger_ids),
        )
    elif missing_triggers and host_get_errors == 0:
        log.warning(
            "host.get returned no hosts for triggerids=%s (will try other methods)",
            ",".join(missing_triggers[:8]),
        )

    # 2) trigger.get — batch path when the token allows it
    still_need = [
        tid
        for tid in trigger_ids
        if not any(p.get("hosts") and str(p.get("objectid") or "") == tid for p in out)
    ]
    if still_need:
        try:
            triggers = (
                zabbix_rpc(
                    api_url,
                    token,
                    "trigger.get",
                    {
                        "triggerids": still_need,
                        "output": ["triggerid"],
                        "selectHosts": ["host", "name"],
                    },
                )
                or []
            )
            from_triggers: dict[str, list[dict[str, Any]]] = {}
            for trigger in triggers:
                tid = str(trigger.get("triggerid") or "")
                hosts = trigger.get("hosts") or []
                if tid and isinstance(hosts, list):
                    cleaned = [h for h in hosts if isinstance(h, dict)]
                    if cleaned:
                        from_triggers[tid] = cleaned
            if from_triggers:
                _apply_hosts_by_trigger(out, from_triggers)
                log.info(
                    "TTS host enrich via trigger.get: %s/%s triggers resolved",
                    len(from_triggers),
                    len(still_need),
                )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "trigger.get host enrich unavailable (%s); relying on host.get / title parse",
                exc,
            )

    # 3) event.get fallback when still missing hosts
    need_events = [p for p in out if not p.get("hosts") and p.get("eventid") is not None]
    if need_events:
        eventids = [str(p["eventid"]) for p in need_events]
        try:
            events = (
                zabbix_rpc(
                    api_url,
                    token,
                    "event.get",
                    {
                        "eventids": eventids,
                        "output": ["eventid"],
                        "selectHosts": ["host", "name"],
                    },
                )
                or []
            )
            by_event = {
                str(e.get("eventid") or ""): [
                    h for h in (e.get("hosts") or []) if isinstance(h, dict)
                ]
                for e in events
            }
            attached = 0
            for problem in out:
                if problem.get("hosts"):
                    continue
                eid = str(problem.get("eventid") or "")
                if eid in by_event and by_event[eid]:
                    problem["hosts"] = by_event[eid]
                    attached += 1
            if attached:
                log.info("TTS host enrich via event.get: %s problems resolved", attached)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "event.get host enrich unavailable (%s); title parse may still find a host",
                exc,
            )

    unresolved = [
        str(p.get("name") or p.get("objectid") or "?")
        for p in out
        if not p.get("hosts")
    ]
    if unresolved:
        log.warning(
            "TTS host enrich incomplete for %s problem(s): %s "
            "(will speak problem text; title-embedded host used if present)",
            len(unresolved),
            "; ".join(unresolved[:5]),
        )

    return out


def _problem_speak_parts(problem: dict[str, Any]) -> dict[str, Any]:
    """Resolve host / role / cleaned problem text for one Zabbix problem."""
    problem_name = str(problem.get("name") or "").strip()
    hosts = problem.get("hosts") or []
    host_obj: dict[str, Any] | None = None
    raw_host = ""
    if isinstance(hosts, list) and hosts:
        first = hosts[0]
        if isinstance(first, dict):
            host_obj = first
            raw_host = str(first.get("name") or first.get("host") or "").strip()

    if not raw_host:
        parsed_host, parsed_rest = split_embedded_host(problem_name)
        if parsed_host:
            raw_host = parsed_host
            problem_name = parsed_rest
            log.debug("TTS host parsed from problem title: %r", raw_host)

    host = strip_host_mac_suffix(raw_host)
    for prefix in (raw_host, host):
        if prefix and problem_name.lower().startswith(prefix.lower()):
            rest = problem_name[len(prefix) :].lstrip(" :-–—")
            if rest:
                problem_name = rest
            break

    problem_name = rewrite_uptime_for_tts(problem_name)
    role = classify_host_role(host_obj, raw_host)
    offline = is_offline_problem(problem_name)
    template = normalize_problem_template(problem_name)
    # Offline alerts of the same device class collapse together even across hosts.
    group_key = (template, role if offline and role else "")
    return {
        "problem": problem,
        "raw_host": raw_host,
        "host": host,
        "problem_name": problem_name,
        "role": role,
        "offline": offline,
        "group_key": group_key,
        "eventid": str(problem.get("eventid") or "0"),
    }


def _format_primary_summary(item: dict[str, Any], others: int) -> str:
    """One speakable line; when others>0: \"… along with N others\"."""
    host = str(item.get("host") or "").strip()
    problem_name = str(item.get("problem_name") or "").strip()
    role = str(item.get("role") or "").strip()
    offline = bool(item.get("offline"))

    if offline and host and role:
        base = f"{host} {role} is offline"
    elif offline and host:
        base = f"{host} is offline"
    elif host and problem_name:
        base = f"{host} - {problem_name}"
    elif problem_name:
        base = problem_name
    elif host:
        base = host
    else:
        base = ""

    if not base:
        return ""
    if others > 0:
        return f"{base} along with {others} others"
    return base


def format_alert_bodies(
    problems: list[dict[str, Any]],
    *,
    max_items: int = 3,
    max_len: int = 280,
) -> str:
    """
    Speakable Zabbix alert body for TTS.

    Related problems (same normalized name, and for offline alerts the same host
    role such as switch / Access Point) collapse to one line:

      \"{Host} switch is offline along with 3 others\"
      \"{Host} - High ping response time along with 2 others\"

    Unrelated leftovers become \". And N more.\" Single alerts stay
    \"Host - Problem\". The optional TTS message prefix is added later as
    \"Message - …\". Host strips a trailing 6-char hex MAC ref when present;
    uptime ``m`` → minutes.
    """
    if not problems:
        return ""

    items = [_problem_speak_parts(p) for p in problems]
    for item in items:
        if item["raw_host"] and item["host"] and item["raw_host"] != item["host"]:
            log.debug("TTS host strip %r → %r", item["raw_host"], item["host"])
        if not item["host"] and item["problem_name"]:
            log.warning(
                "TTS speaking without hostname for problem %r (objectid=%s)",
                item["problem_name"],
                (item["problem"] or {}).get("objectid"),
            )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(item["group_key"], []).append(item)

    # Largest related cluster first; ties → newest eventid as primary.
    ranked = sorted(
        groups.values(),
        key=lambda g: (-len(g), -max(int(x["eventid"] or 0) for x in g)),
    )

    parts: list[str] = []
    spoken = 0
    for group in ranked:
        if len(parts) >= max(1, max_items):
            break
        group_sorted = sorted(group, key=lambda x: int(x["eventid"] or 0), reverse=True)
        primary = group_sorted[0]
        others = len(group_sorted) - 1
        line = _format_primary_summary(primary, others)
        if line:
            parts.append(line)
            spoken += len(group_sorted)

    if not parts:
        return ""

    text = ". ".join(parts)
    extra = len(items) - spoken
    if extra > 0:
        text += f". And {extra} more."
    if len(text) > max_len:
        trimmed = text[: max_len - 1].rsplit(" ", 1)[0].rstrip(".,;")
        text = (trimmed or text[: max_len - 1]) + "…"
    return text


def max_open_severity(
    api_url: str,
    token: str,
    severities: list[int],
    host_group_id: str,
) -> tuple[int | None, str]:
    """Highest open problem severity and speakable body, or (None, "") if all-clear."""
    # Keep problem.get params compatible with older Zabbix (no selectHosts).
    params: dict[str, Any] = {
        "acknowledged": False,
        "suppressed": False,
        "severities": severities,
        "output": ["eventid", "objectid", "source", "severity", "name"],
        "sortfield": ["eventid"],
        "sortorder": "DESC",
    }
    if host_group_id:
        hosts = enabled_host_ids(api_url, token, host_group_id)
        if not hosts:
            log.info("No enabled hosts in host group %s — treating as all-clear", host_group_id)
            return None, ""
        params["hostids"] = hosts
        log.debug("%s enabled hosts in host group", len(hosts))

    problems = zabbix_rpc(api_url, token, "problem.get", params) or []
    log.info("%s active problems from Zabbix", len(problems))
    if not problems:
        return None, ""
    max_sev = max(int(p["severity"]) for p in problems)
    top = [p for p in problems if int(p["severity"]) == max_sev]
    top = enrich_problems_with_hosts(api_url, token, top)
    return max_sev, format_alert_bodies(top)


def alert_body_for_severity(
    opts: dict[str, Any],
    severity: int,
) -> str:
    """Fetch speakable bodies for open problems at a given severity (for Test)."""
    api_url = normalize_zabbix_api(str(opts["zabbix_url"]))
    token = str(opts.get("zabbix_api_token") or "").strip()
    if not token:
        return ""
    host_group_id = str(opts.get("host_group_id") or "").strip()
    params: dict[str, Any] = {
        "acknowledged": False,
        "suppressed": False,
        "severities": [int(severity)],
        "output": ["eventid", "objectid", "source", "severity", "name"],
        "sortfield": ["eventid"],
        "sortorder": "DESC",
    }
    if host_group_id:
        hosts = enabled_host_ids(api_url, token, host_group_id)
        if not hosts:
            return ""
        params["hostids"] = hosts
    try:
        problems = zabbix_rpc(api_url, token, "problem.get", params) or []
        problems = enrich_problems_with_hosts(api_url, token, problems or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("alert body fetch failed: %s", exc)
        return ""
    return format_alert_bodies(problems or [])


def lights_output_allowed() -> bool:
    """Severity paints stop while lights are held. Button Base restore opts in."""
    if getattr(_force_lights, "on", False):
        return True
    return not lights_commands_held()


def set_light_rgb(entity_id: str | list[str], rgb: list[int], brightness: int) -> None:
    """Set one or more lights. RGB 0,0,0 (or brightness 0) turns lights off."""
    if not lights_output_allowed():
        log.debug("light command skipped: lights held")
        return
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not set (are we running as an HA add-on?)")
    entities = [entity_id] if isinstance(entity_id, str) else list(entity_id)
    entities = [e for e in entities if e]
    set_lights_rgb(HA_API, SUPERVISOR_TOKEN, entities, rgb, brightness)


def normalize_zabbix_api(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/api_jsonrpc.php"):
        return base
    return f"{base}/api_jsonrpc.php"


def lerp_rgb(a: list[int], b: list[int], t: float) -> list[int]:
    t = max(0.0, min(1.0, t))
    return [int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)]


def tier_key_for(sev: int | None) -> str:
    if sev is None or sev < 0:
        return "none"
    if sev >= 5:
        return "disaster"
    return SEV_TO_KEY.get(sev, "disaster")


def load_colors(opts: dict[str, Any]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    base = resolve_base_rgb(opts)
    out["base"] = base
    out["recovery"] = resolve_recovery_rgb(opts)
    # none tier tracks Base (idle resting colour)
    out["none"] = list(base)
    for key in TIER_KEYS:
        if key == "none":
            alt_key = "none_alt"
            out[alt_key] = parse_rgb(
                str(opts.get(f"color_{alt_key}") or ""),
                DEFAULT_COLORS[alt_key],
            )
            continue
        out[key] = parse_rgb(
            str(opts.get(f"color_{key}") or ""),
            DEFAULT_COLORS[key],
        )
        alt_key = f"{key}_alt"
        legacy_alt = DEFAULT_COLORS.get(alt_key, base)
        if key == "unclassified" and not opts.get("color_unclassified_alt"):
            legacy_alt = base
        if key == "info" and not opts.get("color_info_alt"):
            legacy_alt = base
        out[alt_key] = parse_rgb(
            str(opts.get(f"color_{alt_key}") or ""),
            legacy_alt if isinstance(legacy_alt, list) else DEFAULT_COLORS[alt_key],
        )
    return out


def load_modes(opts: dict[str, Any]) -> dict[str, str]:
    return {
        key: parse_mode(opts.get(f"mode_{key}"), DEFAULT_MODES[key]) for key in TIER_KEYS
    }


def load_windows(opts: dict[str, Any]) -> dict[str, float]:
    windows: dict[str, float] = {}
    for key in TIER_KEYS:
        win_key = f"window_{key}"
        if opts.get(win_key) is not None and opts.get(win_key) != "":
            windows[key] = parse_seconds(opts.get(win_key), DEFAULT_WINDOWS[key], 0.0)
            continue
        raw_mode = str(opts.get(f"mode_{key}") or "").strip().lower()
        if raw_mode == "fade" or (key == "disaster" and raw_mode in ("", "fade", "pattern")):
            if key == "disaster" and opts.get(win_key) is None:
                windows[key] = 0.0
                continue
        if key == "info":
            hold = parse_seconds(opts.get("info_hold_seconds"), DEFAULT_TIMINGS_INFO_HOLD, 0.0)
            fade = parse_seconds(opts.get("info_fade_seconds"), DEFAULT_TIMINGS_INFO_FADE, 0.0)
            windows[key] = hold + fade if (hold + fade) > 0 else DEFAULT_WINDOWS["info"]
        elif key in LEGACY_PERIOD_KEYS:
            if key == "disaster" and raw_mode in ("", "fade"):
                windows[key] = 0.0
            else:
                windows[key] = parse_seconds(
                    opts.get(LEGACY_PERIOD_KEYS[key]), DEFAULT_WINDOWS[key], 0.0
                )
        else:
            windows[key] = DEFAULT_WINDOWS[key]
    return windows


def default_pattern_for(
    key: str,
    colors: dict[str, list[int]],
    opts: dict[str, Any],
) -> list[dict[str, Any]]:
    primary = colors[key]
    alt = colors[f"{key}_alt"]
    if key == "info":
        hold = parse_seconds(opts.get("info_hold_seconds"), DEFAULT_TIMINGS_INFO_HOLD, 0.05)
        fade = parse_seconds(opts.get("info_fade_seconds"), DEFAULT_TIMINGS_INFO_FADE, 0.05)
        return [
            {"rgb": list(primary), "duration_s": hold, "transition": "snap", "fade_s": 0.5},
            {"rgb": list(alt), "duration_s": 0.1, "transition": "fade", "fade_s": fade},
        ]
    if key == "disaster":
        half = max(
            0.2,
            parse_seconds(opts.get("disaster_fade_seconds"), 3.0, 0.2) / 2.0,
        )
        return [
            {"rgb": list(primary), "duration_s": half, "transition": "fade", "fade_s": half},
            {"rgb": list(alt), "duration_s": half, "transition": "fade", "fade_s": half},
        ]
    if key == "unclassified":
        return [
            {"rgb": list(primary), "duration_s": 0.25, "transition": "snap", "fade_s": 0.5},
            {"rgb": list(alt), "duration_s": 0.25, "transition": "snap", "fade_s": 0.5},
        ]
    return [
        {"rgb": list(primary), "duration_s": 0.5, "transition": "snap", "fade_s": 0.5},
        {"rgb": list(alt), "duration_s": 0.5, "transition": "snap", "fade_s": 0.5},
    ]


def load_patterns(
    opts: dict[str, Any], colors: dict[str, list[int]]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for key in TIER_KEYS:
        raw_mode = str(opts.get(f"mode_{key}") or "").strip().lower()
        inferred = "snap"
        if raw_mode in ("fade", "pattern"):
            inferred = "fade"
        elif raw_mode in ("flash", "strobe"):
            inferred = "snap"
        steps = parse_pattern(opts.get(f"pattern_{key}"), default_transition=inferred)
        if not steps:
            steps = default_pattern_for(key, colors, opts)
        out[key] = steps
    return out


def settle_color_for(sev: int | None, colors: dict[str, list[int]]) -> list[int]:
    """Settled RGB after a timed pattern window."""
    if sev is None:
        return colors.get("base") or colors["none"]
    key = tier_key_for(sev)
    if key == "info":
        return colors.get("info_alt") or colors.get("base") or colors["none"]
    return colors[key]


def recovery_wait_interruptible(
    seconds: float,
    opts: dict[str, Any],
    *,
    poll_every: float = 1.0,
) -> Any:
    """
    Sleep up to `seconds`, polling Zabbix for a new alert and watching Test.
    Returns interrupting severity (int), _RECOVERY_ABORTED_TEST, or None if the
    full wait completed with still-clear.
    """
    end = time.monotonic() + max(0.0, seconds)
    next_poll = time.monotonic()
    while time.monotonic() < end:
        if lights_commands_held():
            return _RECOVERY_ABORTED_HELD
        if peek_test_pending():
            return _RECOVERY_ABORTED_TEST
        now = time.monotonic()
        if now >= next_poll:
            next_poll = now + max(0.25, poll_every)
            try:
                sev, _body = poll_once(opts)
            except Exception as exc:  # noqa: BLE001 — keep recovering on poll blips
                log.warning("recovery poll failed: %s", exc)
                sev = None
            if sev is not None:
                return sev
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))
    # Final check before declaring clear
    if lights_commands_held():
        return _RECOVERY_ABORTED_HELD
    if peek_test_pending():
        return _RECOVERY_ABORTED_TEST
    try:
        sev, _body = poll_once(opts)
    except Exception as exc:  # noqa: BLE001
        log.warning("recovery final poll failed: %s", exc)
        return None
    return sev if sev is not None else None


def run_recovery(
    opts: dict[str, Any],
    entity: str | list[str],
    brightness: int,
    recovery: dict[str, Any],
) -> Any:
    """
    Brief Recovery colour after alerts clear, then settle to Base.
    Returns:
      - int severity if a new alert aborted recovery
      - _RECOVERY_ABORTED_TEST if a Test was queued
      - None if recovery finished and Base was applied
    """
    base = list(recovery["base"])
    rec = list(recovery["recovery"])
    hold_s = float(recovery.get("hold_s") or 0.0)
    fade_s = float(recovery.get("fade_s") or 0.0)
    transition = recovery.get("transition") or "fade"

    log.info(
        "recovery start → rgb=%s hold=%.1fs transition=%s fade=%.1fs then base=%s",
        rec,
        hold_s,
        transition,
        fade_s,
        base,
    )
    if lights_commands_held():
        log.info("recovery skipped: lights held")
        return _RECOVERY_ABORTED_HELD

    set_light_rgb(entity, rec, brightness)

    if hold_s > 0:
        interrupted = recovery_wait_interruptible(hold_s, opts)
        if interrupted is not None:
            log.info("recovery aborted during hold (interrupt=%s)", interrupted)
            return interrupted

    if transition == "fade" and fade_s > 0 and rec != base:
        # Chunked fade that can abort on alert/test
        steps = max(4, int(fade_s / 0.15))
        step_sleep = fade_s / steps
        for i in range(1, steps + 1):
            if lights_commands_held():
                return _RECOVERY_ABORTED_HELD
            if peek_test_pending():
                return _RECOVERY_ABORTED_TEST
            try:
                if i == 1 or i % 3 == 0:
                    sev, _body = poll_once(opts)
                else:
                    sev = None
            except Exception:  # noqa: BLE001
                sev = None
            if sev is not None:
                log.info("recovery aborted during fade (severity=%s)", sev)
                return sev
            rgb = lerp_rgb(rec, base, i / steps)
            set_light_rgb(entity, rgb, brightness)
            interrupted = recovery_wait_interruptible(step_sleep, opts, poll_every=1.0)
            if interrupted is not None:
                log.info("recovery aborted during fade wait (interrupt=%s)", interrupted)
                return interrupted
    else:
        set_light_rgb(entity, base, brightness)

    set_light_rgb(entity, base, brightness)
    log.info("recovery done → base rgb=%s", base)
    return None


def sleep_chunk(
    seconds: float,
    *,
    until: float | None = None,
    abort_on_test: bool = True,
    abort_on_controls: bool = False,
) -> bool:
    """
    Sleep up to `seconds`, honouring optional deadline, pending Test, and a
    lights/full-suppress change. Returns True if aborted early.
    """
    end = time.monotonic() + max(0.0, seconds)
    if until is not None:
        end = min(end, until)
    while time.monotonic() < end:
        if abort_on_test and peek_test_pending():
            return True
        if abort_on_controls and (controls_changed() or lights_commands_held()):
            return True
        if until is not None and time.monotonic() >= until:
            return True
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))
    return False


def fade_to(
    entity: str | list[str],
    brightness: int,
    start: list[int],
    target: list[int],
    fade_s: float,
    *,
    until: float | None = None,
    abort_on_test: bool = True,
    abort_on_controls: bool = False,
) -> list[int]:
    """Smooth fade start→target over fade_s; returns last colour set."""
    if fade_s <= 0.05 or start == target:
        set_light_rgb(entity, target, brightness)
        return list(target)
    remaining = fade_s
    if until is not None:
        remaining = min(remaining, max(0.0, until - time.monotonic()))
    if remaining <= 0:
        return list(start)
    steps = max(4, int(remaining / 0.15))
    step_sleep = remaining / steps
    last = list(start)
    for i in range(1, steps + 1):
        if until is not None and time.monotonic() >= until:
            return last
        if abort_on_test and peek_test_pending():
            return last
        if abort_on_controls and (controls_changed() or lights_commands_held()):
            return last
        last = lerp_rgb(start, target, i / steps)
        set_light_rgb(entity, last, brightness)
        if sleep_chunk(
            step_sleep,
            until=until,
            abort_on_test=abort_on_test,
            abort_on_controls=abort_on_controls,
        ):
            return last
    return list(target)


def _step_entities(default_entities: str | list[str], step: dict[str, Any]) -> list[str]:
    base = [default_entities] if isinstance(default_entities, str) else list(default_entities)
    step_targets = step.get("targets") or []
    if step_targets:
        return [str(x) for x in step_targets if x]
    return [e for e in base if e]


def play_pattern_loop(
    entity: str | list[str],
    brightness: int,
    steps: list[dict[str, Any]],
    *,
    until: float | None = None,
    abort_on_test: bool = True,
    abort_on_controls: bool = False,
    prev_rgb: list[int] | None = None,
) -> list[int]:
    """
    Cycle pattern steps until deadline (or forever if until is None — caller
    must bound with until for continuous mode slices).
    Per-step `targets` override which lights receive that step's colour.
    """
    if not steps:
        return list(prev_rgb or [0, 0, 0])
    prev = list(prev_rgb) if prev_rgb is not None else list(steps[0]["rgb"])
    idx = 0
    while True:
        if until is not None and time.monotonic() >= until:
            return prev
        if abort_on_test and peek_test_pending():
            return prev
        if abort_on_controls and (controls_changed() or lights_commands_held()):
            return prev
        step = steps[idx % len(steps)]
        target = list(step["rgb"])
        transition = step.get("transition") or "snap"
        hold = float(step.get("duration_s") or 0.0)
        fade_s = float(step.get("fade_s") or 0.5)
        ents = _step_entities(entity, step)

        if transition == "fade":
            prev = fade_to(
                ents,
                brightness,
                prev,
                target,
                fade_s,
                until=until,
                abort_on_test=abort_on_test,
                abort_on_controls=abort_on_controls,
            )
            if abort_on_controls and (controls_changed() or lights_commands_held()):
                return prev
        else:
            set_light_rgb(ents, target, brightness)
            prev = target

        if hold > 0:
            if sleep_chunk(
                hold,
                until=until,
                abort_on_test=abort_on_test,
                abort_on_controls=abort_on_controls,
            ):
                return prev
        idx += 1
        # Continuous with no deadline: one full cycle per call is fine when
        # wait_until_next_poll re-enters; if until is None, stop after one pass
        # to avoid blocking the poller forever in a single call.
        if until is None and idx >= len(steps):
            return prev


def active_lights_for(opts: dict[str, Any], sev: int | None) -> list[str]:
    registry = registry_lights(opts)
    key = tier_key_for(sev)
    return targets_for_severity(opts, key, registry)


def restore_registry_to_base(opts: dict[str, Any], brightness: int, colors: dict[str, list[int]]) -> None:
    """Send Base even while lights are held. Severity paints do not use this."""
    registry = registry_lights(opts)
    base = colors.get("base") or colors.get("none") or DEFAULT_BASE_COLOR
    if not registry:
        return
    _force_lights.on = True
    try:
        set_light_rgb(registry, base, brightness)
    finally:
        _force_lights.on = False


def full_mute_release_phrase(snap: dict[str, Any]) -> str:
    lights = bool(snap.get("lights_suppressed"))
    audio = bool(snap.get("audio_suppressed"))
    if lights and audio:
        return "Full mute off."
    if lights:
        return "Audio enabled."
    if audio:
        return "Lights enabled."
    return "Lights and audio enabled."


def announce_button(opts: dict[str, Any], message: str) -> None:
    speak_button_status(HA_API, SUPERVISOR_TOKEN, opts, message)


def apply_audio_toggle(opts: dict[str, Any]) -> dict[str, Any]:
    """Sticky alert-audio mute. A second press is the only way it clears."""
    if get_mute_snapshot().get("audio_suppressed"):
        snap = set_audio_suppressed(False)
        clear_audio_fire()
        announce_button(opts, "Audio enabled.")
    else:
        snap = set_audio_suppressed(True)
        clear_audio_fire()
        stop_media_player(HA_API, SUPERVISOR_TOKEN, opts)
        announce_button(opts, "Audio suppressed.")
    update_runtime_status()
    return snap


def apply_lights_toggle(opts: dict[str, Any], brightness: int, colors: dict[str, list[int]]) -> dict[str, Any]:
    """Sticky lights mute. Sets Base once, then leaves the strip for a scene or other command."""
    if get_mute_snapshot().get("lights_suppressed"):
        snap = set_lights_suppressed(False)
        announce_button(opts, "Lights enabled.")
    else:
        snap = set_lights_suppressed(True)
        restore_registry_to_base(opts, brightness, colors)
        announce_button(opts, "Lights suppressed.")
    signal_controls_changed()
    update_runtime_status()
    return snap


def apply_full_mute(opts: dict[str, Any], brightness: int, colors: dict[str, list[int]], *, enable: bool) -> dict[str, Any]:
    if enable:
        cur = get_runtime_status().get("severity")
        timeout = float(opts.get("mute_timeout_minutes") or 0)
        snap = set_muted(True, severity=cur if isinstance(cur, int) else None, timeout_minutes=timeout)
        restore_registry_to_base(opts, brightness, colors)
        clear_audio_fire()
        stop_media_player(HA_API, SUPERVISOR_TOKEN, opts)
        announce_button(opts, "Lights and audio suppressed.")
    else:
        snap = clear_mute()
        announce_button(opts, full_mute_release_phrase(snap))
    signal_controls_changed()
    update_runtime_status()
    return snap


def apply_severity_enter(
    sev: int | None,
    entity: str | list[str],
    brightness: int,
    colors: dict[str, list[int]],
    modes: dict[str, str],
    windows: dict[str, float],
    patterns: dict[str, list[dict[str, Any]]],
    opts: dict[str, Any] | None = None,
) -> None:
    """Run entry animation (if any) and settle for timed pattern windows.

    For all-clear (sev is None) this only applies Base — Recovery is handled
    separately when transitioning from an active alert.
    """
    if lights_commands_held():
        return
    opts = opts or {}
    registry = registry_lights(opts) if opts else (
        [entity] if isinstance(entity, str) else list(entity)
    )
    active = active_lights_for(opts, sev) if opts else (
        [entity] if isinstance(entity, str) else list(entity)
    )
    if not active:
        active = registry

    # Non-targeted registry lights return to Base when an alert starts
    if sev is not None and registry:
        others = [e for e in registry if e not in set(active)]
        if others:
            set_light_rgb(others, colors.get("base") or colors["none"], brightness)

    if sev is None:
        rgb = colors.get("base") or colors["none"]
        set_light_rgb(registry or active, rgb, brightness)
        log.info("enter severity=none → base rgb=%s lights=%s", rgb, registry or active)
        return

    key = tier_key_for(sev)
    mode = modes[key]
    label = str(sev)
    primary = colors[key]

    if mode == "steady":
        set_light_rgb(active, primary, brightness)
        log.info("enter severity=%s → steady rgb=%s lights=%s", label, primary, active)
        return

    steps = patterns.get(key) or default_pattern_for(key, colors, {})
    window = float(windows.get(key, 0.0))

    if window <= 0:
        # Loop until severity changes — seed first colour; continuous play in wait loop
        first_ents = _step_entities(active, steps[0]) if steps else active
        set_light_rgb(first_ents, steps[0]["rgb"] if steps else primary, brightness)
        log.info(
            "enter severity=%s → pattern loop (window=0, %s steps) until resolved",
            label,
            len(steps),
        )
        return

    log.info(
        "enter severity=%s → pattern %s steps for %.1fs then settle",
        label,
        len(steps),
        window,
    )
    deadline = time.monotonic() + window
    play_pattern_loop(
        active,
        brightness,
        steps,
        until=deadline,
        abort_on_test=True,
        abort_on_controls=True,
    )
    if lights_commands_held() or controls_changed():
        log.info("pattern settle skipped: lights held")
        return
    rgb = settle_color_for(sev, colors)
    set_light_rgb(active, rgb, brightness)
    log.info("settle severity=%s → rgb=%s", label, rgb)


def refresh_steady(
    sev: int | None,
    entity: str,
    brightness: int,
    colors: dict[str, list[int]],
    modes: dict[str, str],
    windows: dict[str, float],
) -> None:
    """Re-assert settle/steady colour when severity unchanged (skip looping patterns)."""
    if lights_commands_held():
        return
    if sev is None:
        set_light_rgb(entity, colors.get("base") or colors["none"], brightness)
        return
    key = tier_key_for(sev)
    mode = modes[key]
    if mode == "pattern" and float(windows.get(key, 0.0)) <= 0:
        return
    if mode == "steady":
        set_light_rgb(entity, colors[key], brightness)
        return
    set_light_rgb(entity, settle_color_for(sev, colors), brightness)


def wait_until_next_poll(
    sev: int | None,
    entity: str,
    brightness: int,
    colors: dict[str, list[int]],
    modes: dict[str, str],
    windows: dict[str, float],
    patterns: dict[str, list[dict[str, Any]]],
    interval: int,
) -> None:
    """Sleep for poll interval; window=0 patterns keep cycling through the wait."""
    if sev is None:
        sleep_until(time.monotonic() + interval, abort_on_test=True, abort_on_controls=True)
        return

    key = tier_key_for(sev)
    mode = modes[key]
    window = float(windows.get(key, 0.0))
    deadline = time.monotonic() + interval

    if mode == "pattern" and window <= 0:
        steps = patterns.get(key) or default_pattern_for(key, colors, {})
        play_pattern_loop(
            entity,
            brightness,
            steps,
            until=deadline,
            abort_on_test=True,
            abort_on_controls=True,
            prev_rgb=list(steps[0]["rgb"]),
        )
        return

    sleep_until(deadline, abort_on_test=True, abort_on_controls=True)


def poll_once(opts: dict[str, Any]) -> tuple[int | None, str]:
    api_url = normalize_zabbix_api(str(opts["zabbix_url"]))
    token = str(opts.get("zabbix_api_token") or "").strip()
    if not token:
        raise RuntimeError("zabbix_api_token is empty — set it in the add-on configuration")

    severities = parse_severities(str(opts.get("severities") or "0,1,2,3,4,5"))
    host_group_id = str(opts.get("host_group_id") or "").strip()
    return max_open_severity(api_url, token, severities, host_group_id)


def is_recovery_test_path(raw: str) -> bool:
    return str(raw or "").strip().lower() in ("recovery", "recover")


def parse_test_severity(raw: str) -> int | None:
    text = str(raw or "").strip().lower()
    if is_recovery_test_path(text):
        raise ValueError("use kind=recovery")
    if text in KEY_TO_SEV and text not in ("recovery",):
        return KEY_TO_SEV[text]
    if text in ("", "-1", "null"):
        return None
    try:
        n = int(text)
    except ValueError as exc:
        raise ValueError(f"Unknown severity {raw!r}") from exc
    if n < 0:
        return None
    if n > 5:
        return 5
    return n


def queue_test(
    sev: int | None,
    seconds: float = DEFAULT_TEST_SECONDS,
    *,
    kind: str = "severity",
) -> dict[str, Any]:
    secs = max(1.0, min(MAX_TEST_SECONDS, float(seconds)))
    req = {
        "sev": sev,
        "seconds": secs,
        "kind": kind,
        "queued_at": time.monotonic(),
    }
    with _test_lock:
        global _test_request
        _test_request = req
    label = "recovery" if kind == "recovery" else ("none" if sev is None else str(sev))
    log.info("test queued kind=%s severity=%s for %.1fs", kind, label, secs)
    return {"severity": label, "seconds": secs, "kind": kind}


def take_test_request() -> dict[str, Any] | None:
    with _test_lock:
        global _test_request
        req = _test_request
        _test_request = None
        return req


def peek_test_pending() -> bool:
    with _test_lock:
        return _test_request is not None


def sleep_until(
    deadline: float,
    *,
    abort_on_test: bool = False,
    abort_on_controls: bool = False,
) -> None:
    while time.monotonic() < deadline:
        if abort_on_test and peek_test_pending():
            return
        # Event only. Also treating "already held" as a wake would busy-loop
        # the suppress path, which sleeps here between Zabbix polls.
        if abort_on_controls and controls_changed():
            return
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def run_severity_test(
    sev: int | None,
    seconds: float,
    entity: str,
    brightness: int,
    colors: dict[str, list[int]],
    modes: dict[str, str],
    windows: dict[str, float],
    patterns: dict[str, list[dict[str, Any]]],
    *,
    kind: str = "severity",
    opts: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
) -> None:
    """Preview one severity's current mode (or Recovery→Base) on the light."""
    secs = max(1.0, min(MAX_TEST_SECONDS, float(seconds)))
    deadline = time.monotonic() + secs
    # Ingress Test still previews lights while a channel is held.
    _force_lights.on = True

    if kind == "recovery":
        log.info("TEST start recovery for ≤%.1fs → %s", secs, entity)
        try:
            cfg = recovery or load_recovery_settings(opts or {})
            # Non-polling preview: hold + fade without Zabbix checks
            base = list(cfg["base"])
            rec = list(cfg["recovery"])
            hold_s = min(float(cfg.get("hold_s") or 0.0), max(0.5, secs * 0.6))
            fade_s = float(cfg.get("fade_s") or 0.0)
            transition = cfg.get("transition") or "fade"
            set_light_rgb(entity, rec, brightness)
            sleep_until(time.monotonic() + hold_s, abort_on_test=False)
            if transition == "fade" and fade_s > 0:
                fade_budget = min(fade_s, max(0.2, deadline - time.monotonic()))
                fade_to(
                    entity,
                    brightness,
                    rec,
                    base,
                    fade_budget,
                    until=deadline,
                    abort_on_test=False,
                )
            set_light_rgb(entity, base, brightness)
            sleep_until(deadline, abort_on_test=False)
        except Exception:  # noqa: BLE001
            log.exception("TEST failed recovery")
            raise
        finally:
            _force_lights.on = False
            log.info("TEST done recovery — returning to live polling")
        return

    key = tier_key_for(sev)
    mode = modes[key] if sev is not None else "steady"
    label = "none" if sev is None else str(sev)
    log.info("TEST start severity=%s mode=%s for %.1fs → %s", label, mode, secs, entity)

    # Also fire configured TTS/media for this severity (if audio_enabled)
    if opts and sev is not None:
        try:
            body = ""
            try:
                body = alert_body_for_severity(opts, int(sev))
            except Exception as exc:  # noqa: BLE001
                log.debug("TEST alert body fetch skipped: %s", exc)
            if not body:
                body = f"test host - Test alert for severity {sev}"
            play_severity_audio(
                HA_API,
                SUPERVISOR_TOKEN,
                opts,
                key,
                force=True,
                alert_body=body,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("TEST audio failed: %s", exc)

    try:
        if sev is None:
            set_light_rgb(entity, colors.get("base") or colors["none"], brightness)
            sleep_until(deadline, abort_on_test=False)
        elif mode == "steady":
            set_light_rgb(entity, colors[key], brightness)
            sleep_until(deadline, abort_on_test=False)
        else:
            steps = patterns.get(key) or default_pattern_for(key, colors, {})
            window = float(windows.get(key, 0.0))
            if window <= 0:
                play_pattern_loop(
                    entity,
                    brightness,
                    steps,
                    until=deadline,
                    abort_on_test=False,
                )
            else:
                play_until = time.monotonic() + min(window, secs)
                play_pattern_loop(
                    entity,
                    brightness,
                    steps,
                    until=play_until,
                    abort_on_test=False,
                )
                set_light_rgb(entity, settle_color_for(sev, colors), brightness)
                sleep_until(deadline, abort_on_test=False)
    except Exception:  # noqa: BLE001
        log.exception("TEST failed severity=%s", label)
        raise
    finally:
        _force_lights.on = False
        log.info("TEST done severity=%s — returning to live polling", label)


def reload_runtime(opts: dict[str, Any]) -> tuple:
    opts = migrate_options(opts)
    colors = load_colors(opts)
    modes = load_modes(opts)
    windows = load_windows(opts)
    patterns = load_patterns(opts, colors)
    recovery = load_recovery_settings(opts)
    interval = max(5, int(opts.get("poll_interval_seconds") or 45))
    lights = registry_lights(opts)
    entity = lights[0] if lights else str(opts.get("light_entity_id") or "").strip()
    brightness = int(opts.get("brightness") or 255)
    return colors, modes, windows, patterns, recovery, interval, entity, brightness, lights


# ---------------------------------------------------------------------------
# Ingress HTTP UI
# ---------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class IngressHandler(BaseHTTPRequestHandler):
    server_version = "SeverityBeacon/0.8.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("ingress %s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/api/options", "api/options"):
            opts = get_options_cache()
            safe = dict(opts)
            if safe.get("zabbix_api_token"):
                safe["zabbix_api_token_set"] = True
                safe["zabbix_api_token"] = ""
            else:
                safe["zabbix_api_token_set"] = False
            self._json(200, {"ok": True, "options": safe, "status": get_runtime_status()})
            return

        if path in ("/api/status", "api/status"):
            self._json(200, {"ok": True, "status": get_runtime_status()})
            return

        if path in ("/api/ha/entities", "api/ha/entities"):
            try:
                from urllib.parse import parse_qs

                qs = parse_qs(urlparse(self.path).query)
                domains = qs.get("domain") or []
                domain_list = []
                for d in domains:
                    domain_list.extend([x.strip() for x in d.split(",") if x.strip()])
                if not domain_list:
                    domain_list = ["light", "button", "input_button", "event", "media_player", "tts"]
                states = fetch_ha_states(HA_API, SUPERVISOR_TOKEN)
                entities = filter_entities(states, domain_list)
                self._json(200, {"ok": True, "entities": entities})
            except Exception as exc:  # noqa: BLE001
                log.exception("ha entities failed")
                self._json(500, {"ok": False, "error": str(exc)})
            return

        rel = path.lstrip("/") or "index.html"
        if ".." in rel or rel.startswith("/"):
            self._send(400, b"bad path", "text/plain")
            return
        file_path = (WWW_DIR / rel).resolve()
        try:
            file_path.relative_to(WWW_DIR.resolve())
        except ValueError:
            self._send(403, b"forbidden", "text/plain")
            return
        if not file_path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        data = file_path.read_bytes()
        ctype = MIME.get(file_path.suffix.lower(), "application/octet-stream")
        self._send(200, data, ctype)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        test_prefix = None
        for prefix in ("/api/test/", "api/test/"):
            if path.startswith(prefix):
                test_prefix = prefix
                break
        if test_prefix is not None:
            try:
                sev_raw = path[len(test_prefix) :]
                try:
                    body = self._read_json()
                except Exception:  # noqa: BLE001
                    body = {}
                seconds = body.get("seconds", DEFAULT_TEST_SECONDS)
                if is_recovery_test_path(sev_raw):
                    queued = queue_test(None, float(seconds), kind="recovery")
                    msg = (
                        f"Testing recovery→base for {queued['seconds']:.0f}s "
                        "on the light, then back to live"
                    )
                else:
                    sev = parse_test_severity(sev_raw)
                    queued = queue_test(sev, float(seconds), kind="severity")
                    msg = (
                        f"Testing severity {queued['severity']} for "
                        f"{queued['seconds']:.0f}s on the light, then back to live"
                    )
                self._json(200, {"ok": True, "message": msg, **queued})
            except Exception as exc:  # noqa: BLE001
                log.exception("test queue failed")
                self._json(400, {"ok": False, "error": str(exc)})
            return

        if path in ("/api/mute", "api/mute"):
            try:
                opts = get_options_cache()
                colors = load_colors(opts)
                snap = apply_full_mute(opts, int(opts.get("brightness") or 255), colors, enable=True)
                self._json(200, {"ok": True, "mute": snap, "message": "Lights and audio suppressed."})
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(exc)})
            return

        if path in ("/api/unmute", "api/unmute"):
            try:
                opts = get_options_cache()
                colors = load_colors(opts)
                snap = apply_full_mute(opts, int(opts.get("brightness") or 255), colors, enable=False)
                self._json(200, {"ok": True, "mute": snap, "message": full_mute_release_phrase(snap)})
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(exc)})
            return

        if path in ("/api/audio/toggle", "api/audio/toggle"):
            try:
                opts = get_options_cache()
                snap = apply_audio_toggle(opts)
                held = bool(snap.get("audio_suppressed"))
                self._json(
                    200,
                    {
                        "ok": True,
                        "mute": snap,
                        "message": "Audio suppressed." if held else "Audio enabled.",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(exc)})
            return

        if path in ("/api/lights/toggle", "api/lights/toggle"):
            try:
                opts = get_options_cache()
                colors = load_colors(opts)
                snap = apply_lights_toggle(opts, int(opts.get("brightness") or 255), colors)
                held = bool(snap.get("lights_suppressed"))
                self._json(
                    200,
                    {
                        "ok": True,
                        "mute": snap,
                        "message": "Lights suppressed." if held else "Lights enabled.",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(exc)})
            return

        if path not in ("/api/options", "api/options"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            body = self._read_json()
            partial = body.get("options")
            if not isinstance(partial, dict):
                raise ValueError("body.options must be an object")
            merged = persist_options_via_supervisor(partial)
            safe = dict(merged)
            if safe.get("zabbix_api_token"):
                safe["zabbix_api_token_set"] = True
                safe["zabbix_api_token"] = ""
            self._json(
                200,
                {
                    "ok": True,
                    "options": safe,
                    "status": get_runtime_status(),
                    "restart_hint": False,
                    "message": "Saved; poller reloads options each cycle",
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("options save failed")
            self._json(400, {"ok": False, "error": str(exc)})


def start_ingress_server() -> ThreadingHTTPServer:
    if not WWW_DIR.is_dir():
        log.warning("WWW dir missing at %s — Ingress UI will 404", WWW_DIR)
    server = ThreadingHTTPServer((INGRESS_HOST, INGRESS_PORT), IngressHandler)
    thread = threading.Thread(target=server.serve_forever, name="ingress", daemon=True)
    thread.start()
    log.info("Ingress UI listening on %s:%s (www=%s)", INGRESS_HOST, INGRESS_PORT, WWW_DIR)
    return server


def main() -> int:
    opts = migrate_options(load_options())
    set_options_cache(opts)
    write_options_file(opts)  # persist migrated shape for Ingress
    load_mute_state()
    start_ingress_server()

    (
        colors,
        modes,
        windows,
        patterns,
        recovery,
        interval,
        entity,
        brightness,
        lights,
    ) = reload_runtime(opts)
    if not lights and not entity:
        raise SystemExit("lights / light_entity_id is required")
    if not lights and entity:
        lights = [entity]

    log.info(
        "Polling Zabbix every %ss → lights %s via %s "
        "(multi-light, mute, audio, lights, base/recovery, Ingress 0.8.2)",
        interval,
        lights,
        HA_API,
    )
    log.info(
        "Base rgb=%s Recovery rgb=%s hold=%.1fs fade=%.1fs transition=%s",
        recovery["base"],
        recovery["recovery"],
        recovery["hold_s"],
        recovery["fade_s"],
        recovery["transition"],
    )
    last_sev: int | None | object = object()
    last_mtime: float | None = None
    problem_count = 0
    was_lights_held = lights_commands_held()

    def on_mute_press(eid: str) -> None:
        snap = get_mute_snapshot()
        turning_on = not snap.get("muted")
        apply_full_mute(opts, brightness, colors, enable=turning_on)
        log.info("mute button %s → %s", eid, "muted" if turning_on else "unmuted")

    def on_audio_press(eid: str) -> None:
        snap = apply_audio_toggle(opts)
        log.info(
            "audio button %s → %s",
            eid,
            "muted" if snap.get("audio_suppressed") else "enabled",
        )

    def on_lights_press(eid: str) -> None:
        snap = apply_lights_toggle(opts, brightness, colors)
        log.info(
            "lights button %s → %s",
            eid,
            "muted" if snap.get("lights_suppressed") else "enabled",
        )

    def button_groups() -> list[tuple[list[str], Any]]:
        current = get_options_cache() or opts
        return [
            (parse_entity_list(current.get("mute_entities")), on_mute_press),
            (parse_entity_list(current.get("audio_toggle_entities")), on_audio_press),
            (parse_entity_list(current.get("lights_toggle_entities")), on_lights_press),
        ]

    def watch_buttons() -> None:
        log.info(
            "Button helpers polled every %.0fs; Zabbix stays on its own interval",
            BUTTON_POLL_SECONDS,
        )
        while True:
            try:
                if check_mute_timeout():
                    signal_controls_changed()
                if SUPERVISOR_TOKEN:
                    poll_button_groups(HA_API, SUPERVISOR_TOKEN, button_groups())
            except Exception as exc:  # noqa: BLE001
                log.debug("button poll failed: %s", exc)
            time.sleep(BUTTON_POLL_SECONDS)

    threading.Thread(target=watch_buttons, name="buttons", daemon=True).start()

    while True:
        try:
            if OPTIONS_PATH.is_file():
                mtime = OPTIONS_PATH.stat().st_mtime
                if last_mtime is None or mtime != last_mtime:
                    opts = migrate_options(load_options())
                    set_options_cache(opts)
                    (
                        colors,
                        modes,
                        windows,
                        patterns,
                        recovery,
                        interval,
                        entity,
                        brightness,
                        lights,
                    ) = reload_runtime(opts)
                    if not lights and entity:
                        lights = [entity]
                    if last_mtime is not None:
                        log.info(
                            "options.json changed — reloaded modes/colors/patterns/recovery"
                        )
                    last_mtime = mtime

            # Presses are handled on the button thread. Clear the wake so this
            # iteration's Zabbix wait is not skipped, then honour the new hold.
            consume_controls_changed()
            held_now = lights_commands_held()
            if was_lights_held and not held_now:
                last_sev = object()  # beacon may paint severity colours again
            was_lights_held = held_now

            test_req = take_test_request()
            if test_req is not None:
                run_severity_test(
                    test_req.get("sev"),
                    float(test_req.get("seconds") or DEFAULT_TEST_SECONDS),
                    lights[0] if lights else entity,
                    brightness,
                    colors,
                    modes,
                    windows,
                    patterns,
                    kind=str(test_req.get("kind") or "severity"),
                    opts=opts,
                    recovery=recovery,
                )
                if lights_commands_held():
                    restore_registry_to_base(opts, brightness, colors)
                last_sev = object()
                continue

            sev, alert_body = poll_once(opts)
            # Estimate problem count for status (None → 0; int → at least 1)
            problem_count = 0 if sev is None else max(1, problem_count)
            label = "none" if sev is None else str(sev)
            update_runtime_status(
                severity=sev,
                last_poll=time.time(),
                lights=lights,
                problem_count=problem_count,
            )

            mute_snap = get_mute_snapshot()
            muted = bool(mute_snap.get("muted"))
            muted_at = mute_snap.get("muted_at_severity")

            # Auto-unmute when a higher severity arrives. Sticky audio/lights holds stay.
            if muted and isinstance(sev, int) and isinstance(muted_at, int) and sev > muted_at:
                log.info("higher severity %s > muted_at %s — auto-unmute", sev, muted_at)
                clear_mute()
                muted = False
                clear_audio_fire()
                if not lights_commands_held():
                    last_sev = object()

            if get_mute_snapshot().get("lights_suppressed") and not muted:
                # Lights mute set Base once on the press, then stopped commanding the strip.
                if sev is None and isinstance(last_sev, int):
                    if opts.get("audio_recovery_chime") and not alert_audio_blocked():
                        play_severity_audio(
                            HA_API, SUPERVISOR_TOKEN, opts, "none", force=True
                        )
                    last_sev = None
                elif sev != last_sev:
                    if isinstance(sev, int):
                        clear_audio_fire()
                        play_severity_audio(
                            HA_API,
                            SUPERVISOR_TOKEN,
                            opts,
                            tier_key_for(sev),
                            force=False,
                            alert_body=alert_body,
                        )
                        maybe_notify(HA_API, SUPERVISOR_TOKEN, opts, sev, label)
                    last_sev = sev
                sleep_until(
                    time.monotonic() + min(2.0, float(interval)),
                    abort_on_controls=True,
                )
                continue

            if muted:
                # Stay on Base while muted; still run recovery when problems clear.
                # A sticky lights hold keeps the strip untouched, including Recovery.
                if sev is None and isinstance(last_sev, int):
                    clear_mute()
                    if get_mute_snapshot().get("lights_suppressed"):
                        if opts.get("audio_recovery_chime") and not alert_audio_blocked():
                            play_severity_audio(
                                HA_API, SUPERVISOR_TOKEN, opts, "none", force=True
                            )
                        last_sev = None
                        sleep_until(
                            time.monotonic() + min(2.0, float(interval)),
                            abort_on_controls=True,
                        )
                        continue
                    result = run_recovery(opts, lights, brightness, recovery)
                    if result is _RECOVERY_ABORTED_HELD:
                        last_sev = None
                        continue
                    if opts.get("audio_recovery_chime") and not alert_audio_blocked():
                        play_severity_audio(
                            HA_API, SUPERVISOR_TOKEN, opts, "none", force=True
                        )
                    if result is _RECOVERY_ABORTED_TEST:
                        last_sev = object()
                        continue
                    last_sev = None if not isinstance(result, int) else result
                    if isinstance(result, int):
                        apply_severity_enter(
                            result, lights, brightness, colors, modes, windows, patterns, opts=opts
                        )
                else:
                    restore_registry_to_base(opts, brightness, colors)
                    if isinstance(last_sev, object) and last_sev is not None and not isinstance(last_sev, int):
                        pass
                    elif last_sev != sev and isinstance(sev, int):
                        last_sev = sev  # track severity while muted without lighting
                    sleep_until(
                        time.monotonic() + min(2.0, float(interval)),
                        abort_on_controls=True,
                    )
                continue

            escalate_only = bool(opts.get("escalate_only"))
            severity_changed = sev != last_sev
            should_enter = severity_changed and (
                not escalate_only
                or last_sev is object()
                or (isinstance(sev, int) and (not isinstance(last_sev, int) or sev > last_sev))
                or sev is None
            )
            # escalate_only still allows clears and first paint
            if escalate_only and severity_changed and isinstance(sev, int) and isinstance(last_sev, int):
                if sev < last_sev:
                    should_enter = True  # still update when severity drops
                elif sev == last_sev:
                    should_enter = False

            if should_enter or (severity_changed and not escalate_only):
                prior_was_alert = isinstance(last_sev, int)
                if sev is None and prior_was_alert:
                    result = run_recovery(opts, lights, brightness, recovery)
                    if result is _RECOVERY_ABORTED_HELD:
                        last_sev = None
                        continue
                    if opts.get("audio_recovery_chime") and not alert_audio_blocked():
                        play_severity_audio(
                            HA_API, SUPERVISOR_TOKEN, opts, "none", force=True
                        )
                    if result is _RECOVERY_ABORTED_TEST:
                        last_sev = object()
                        continue
                    if isinstance(result, int):
                        clear_audio_fire()
                        apply_severity_enter(
                            result, lights, brightness, colors, modes, windows, patterns, opts=opts
                        )
                        body = alert_body_for_severity(opts, result) or alert_body
                        play_severity_audio(
                            HA_API,
                            SUPERVISOR_TOKEN,
                            opts,
                            tier_key_for(result),
                            force=True,
                            alert_body=body,
                        )
                        maybe_notify(HA_API, SUPERVISOR_TOKEN, opts, result, str(result))
                        last_sev = result
                    else:
                        clear_audio_fire()
                        last_sev = None
                else:
                    clear_audio_fire()
                    apply_severity_enter(
                        sev, lights, brightness, colors, modes, windows, patterns, opts=opts
                    )
                    if sev is not None:
                        play_severity_audio(
                            HA_API,
                            SUPERVISOR_TOKEN,
                            opts,
                            tier_key_for(sev),
                            force=True,
                            alert_body=alert_body,
                        )
                        maybe_notify(HA_API, SUPERVISOR_TOKEN, opts, sev, label)
                    last_sev = sev
            else:
                active = active_lights_for(opts, sev if isinstance(sev, (int, type(None))) else None)
                refresh_steady(sev, active or lights, brightness, colors, modes, windows)
                if isinstance(sev, int):
                    play_severity_audio(
                        HA_API,
                        SUPERVISOR_TOKEN,
                        opts,
                        tier_key_for(sev),
                        force=False,
                        alert_body=alert_body,
                    )
                log.debug("max_severity=%s unchanged — refresh/continue", label)

            wait_until_next_poll(
                last_sev if isinstance(last_sev, (int, type(None))) else sev,
                active_lights_for(opts, last_sev if isinstance(last_sev, (int, type(None))) else sev)
                or lights,
                brightness,
                colors,
                modes,
                windows,
                patterns,
                interval,
            )
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            RuntimeError,
            OSError,
            ValueError,
        ) as exc:
            log.error("%s", exc)
            sleep_until(time.monotonic() + interval, abort_on_controls=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.info("stopped")
        raise SystemExit(0)

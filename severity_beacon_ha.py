#!/usr/bin/env python3
"""
Severity_Beacon → Home Assistant (simple path)

Polls Zabbix problem.get (same idea as github.com/sgevents-sam/Severity_Beacon)
and sets light.koti_office_strip_led.

Defaults match Severity_Beacon filters, plus Not classified + Information:
  - acknowledged=false, suppressed=false
  - severities 0,1,2,3,4,5
  - highest open severity wins
  - no matching problems → green

Per-severity colors (HA_MODE=api; full RGB + animations):
  none → green 0,255,0
  0    → soft yellow; flash yellow/green then settle yellow
  1    → blue hold, fade to green
  2    → orange 255,140,0
  3    → deep orange 255,80,0
  4    → red 255,0,0
  5    → continuous red↔orange fade during poll wait

HA_MODE=webhook: approximates to green / orange / red via existing automation
(cannot do blue/yellow/fade without light.turn_on). Prefer HA_MODE=api for the
full scale.

API key: set ZABBIX_API_TOKEN in severity_beacon_ha.env (see .env.example).
Do not invent or commit a real token.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "severity_beacon_ha.env"

ZABBIX_URL = os.environ.get("ZABBIX_URL", "https://zabbix.example.com").rstrip("/")
ZABBIX_API = os.environ.get("ZABBIX_API", f"{ZABBIX_URL}/api_jsonrpc.php")
HA_BASE = os.environ.get("HA_BASE", "http://homeassistant.local").rstrip("/")
ENTITY = os.environ.get("HA_ENTITY", "light.koti_office_strip_led")
WEBHOOK_ID = os.environ.get("HA_WEBHOOK_ID", "zabbix_led_status")
INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "45"))
HOST_GROUP_ID = os.environ.get("ZABBIX_HOST_GROUP_ID", "").strip()
SEVERITIES = [
    int(x) for x in os.environ.get("ZABBIX_SEVERITIES", "0,1,2,3,4,5").split(",") if x.strip()
]

RGB = {
    "none": [0, 255, 0],
    "unclassified": [255, 220, 0],
    "info": [0, 120, 255],
    "warning": [255, 140, 0],
    "average": [255, 80, 0],
    "high": [255, 0, 0],
    "disaster": [255, 0, 0],
    "disaster_alt": [255, 140, 0],
}

FLASH_SECONDS = float(os.environ.get("UNCLASSIFIED_FLASH_SECONDS", "3"))
INFO_HOLD = float(os.environ.get("INFO_HOLD_SECONDS", "3"))
INFO_FADE = float(os.environ.get("INFO_FADE_SECONDS", "3"))
DISASTER_FADE = float(os.environ.get("DISASTER_FADE_SECONDS", "3"))


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def require_zabbix_token() -> str:
    token = os.environ.get("ZABBIX_API_TOKEN", "").strip()
    if not token or token in {"YOUR_TOKEN_HERE", "YOUR_ZABBIX_API_TOKEN"}:
        print(
            "Set ZABBIX_API_TOKEN in "
            f"{ENV_FILE} (key name: ZABBIX_API_TOKEN=YOUR_TOKEN_HERE).",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def http_json(url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def zabbix_rpc(token: str, method: str, params: dict[str, Any]) -> Any:
    result = http_json(
        ZABBIX_API,
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
        raise RuntimeError(f"Zabbix {method} error: {err.get('data') or err.get('message') or err}")
    return result.get("result")


def enabled_host_ids(token: str, group_id: str) -> list[str]:
    hosts = zabbix_rpc(
        token,
        "host.get",
        {
            "output": ["hostid", "status"],
            "groupids": [group_id],
            "filter": {"status": ["0"]},
        },
    )
    return [h["hostid"] for h in (hosts or [])]


def max_open_severity(token: str) -> int | None:
    """Return highest open problem severity, or None if none (all clear)."""
    params: dict[str, Any] = {
        "acknowledged": False,
        "suppressed": False,
        "severities": SEVERITIES,
        "output": ["eventid", "severity", "name"],
    }
    if HOST_GROUP_ID:
        hosts = enabled_host_ids(token, HOST_GROUP_ID)
        if not hosts:
            print("No enabled hosts in host group; treating as all-clear.")
            return None
        params["hostids"] = hosts

    problems = zabbix_rpc(token, "problem.get", params) or []
    if not problems:
        return None
    return max(int(p["severity"]) for p in problems)


def lerp_rgb(a: list[int], b: list[int], t: float) -> list[int]:
    t = max(0.0, min(1.0, t))
    return [int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)]


def set_led_via_webhook(bucket: str, sev: int | None) -> None:
    # Existing automation only knows green / orange / red.
    if bucket == "green":
        payload = {"severity": "OK", "status": "RESOLVED", "value": "0"}
    elif bucket == "red":
        payload = {"severity": "Disaster" if (sev or 0) >= 5 else "High", "status": "PROBLEM", "value": "1"}
    else:
        payload = {"severity": "Warning", "status": "PROBLEM", "value": "1"}
    url = f"{HA_BASE}/api/webhook/{WEBHOOK_ID}"
    http_json(url, payload)


def set_led_rgb(rgb: list[int]) -> None:
    token = os.environ.get("HA_TOKEN", "").strip()
    if not token or token == "YOUR_HA_LONG_LIVED_TOKEN":
        raise RuntimeError("HA_TOKEN required for mode=api (or use mode=webhook)")
    http_json(
        f"{HA_BASE}/api/services/light/turn_on",
        {"entity_id": ENTITY, "rgb_color": rgb, "brightness": 255},
        headers={"Authorization": f"Bearer {token}"},
    )


def webhook_bucket(sev: int | None) -> str:
    """Map severity to the legacy green/orange/red webhook buckets."""
    if sev is None or sev < 0:
        return "green"
    if sev >= 4:
        return "red"
    return "orange"


def steady_rgb(sev: int | None) -> list[int]:
    if sev is None or sev < 0:
        return RGB["none"]
    if sev == 0:
        return RGB["unclassified"]
    if sev == 1:
        return RGB["none"]  # info settles green
    if sev == 2:
        return RGB["warning"]
    if sev == 3:
        return RGB["average"]
    if sev == 4:
        return RGB["high"]
    return RGB["disaster"]


def anim_unclassified_flash() -> None:
    yellow, green = RGB["unclassified"], RGB["none"]
    deadline = time.monotonic() + FLASH_SECONDS
    use_yellow = True
    while time.monotonic() < deadline:
        set_led_rgb(yellow if use_yellow else green)
        use_yellow = not use_yellow
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def anim_info_hold_fade() -> None:
    blue, green = RGB["info"], RGB["none"]
    set_led_rgb(blue)
    time.sleep(INFO_HOLD)
    steps = max(6, int(INFO_FADE / 0.2))
    for i in range(1, steps + 1):
        set_led_rgb(lerp_rgb(blue, green, i / steps))
        time.sleep(INFO_FADE / steps)


def anim_disaster_fade_until(until: float) -> None:
    a, b = RGB["disaster"], RGB["disaster_alt"]
    half = max(0.2, DISASTER_FADE / 2.0)
    steps = max(4, int(half / 0.15))
    step_sleep = half / steps
    going_to_b = True
    while time.monotonic() < until:
        start, end = (a, b) if going_to_b else (b, a)
        for i in range(1, steps + 1):
            if time.monotonic() >= until:
                return
            set_led_rgb(lerp_rgb(start, end, i / steps))
            time.sleep(min(step_sleep, max(0.0, until - time.monotonic())))
        going_to_b = not going_to_b


def apply_enter_api(sev: int | None) -> str:
    label = "none" if sev is None else str(sev)
    if sev is None or sev < 0:
        set_led_rgb(RGB["none"])
        return f"green {RGB['none']}"
    if sev == 0:
        anim_unclassified_flash()
        set_led_rgb(RGB["unclassified"])
        return f"yellow settle {RGB['unclassified']}"
    if sev == 1:
        anim_info_hold_fade()
        set_led_rgb(RGB["none"])
        return f"info→green {RGB['none']}"
    if sev in (2, 3, 4):
        rgb = steady_rgb(sev)
        set_led_rgb(rgb)
        return f"steady {rgb}"
    set_led_rgb(RGB["disaster"])
    return f"disaster fade {RGB['disaster']}↔{RGB['disaster_alt']}"


def once(token: str, mode: str, last_sev: Any = object()) -> tuple[str, int | None]:
    sev = max_open_severity(token)
    label = "none" if sev is None else str(sev)

    if mode == "api":
        if sev != last_sev:
            detail = apply_enter_api(sev)
            print(f"max_severity={label} → enter {detail} ({ENTITY})")
        else:
            if sev != 5:
                set_led_rgb(steady_rgb(sev))
            print(f"max_severity={label} → refresh ({ENTITY})")
        return label, sev

    bucket = webhook_bucket(sev)
    set_led_via_webhook(bucket, sev)
    print(f"max_severity={label} → webhook {bucket} ({ENTITY})")
    return bucket, sev


def main(argv: list[str]) -> int:
    load_env_file(ENV_FILE)
    global ZABBIX_URL, ZABBIX_API, HA_BASE, ENTITY, WEBHOOK_ID, INTERVAL, HOST_GROUP_ID, SEVERITIES
    global FLASH_SECONDS, INFO_HOLD, INFO_FADE, DISASTER_FADE
    ZABBIX_URL = os.environ.get("ZABBIX_URL", ZABBIX_URL).rstrip("/")
    ZABBIX_API = os.environ.get("ZABBIX_API", f"{ZABBIX_URL}/api_jsonrpc.php")
    HA_BASE = os.environ.get("HA_BASE", HA_BASE).rstrip("/")
    ENTITY = os.environ.get("HA_ENTITY", ENTITY)
    WEBHOOK_ID = os.environ.get("HA_WEBHOOK_ID", WEBHOOK_ID)
    INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", str(INTERVAL)))
    HOST_GROUP_ID = os.environ.get("ZABBIX_HOST_GROUP_ID", "").strip()
    SEVERITIES = [
        int(x) for x in os.environ.get("ZABBIX_SEVERITIES", "0,1,2,3,4,5").split(",") if x.strip()
    ]
    FLASH_SECONDS = float(os.environ.get("UNCLASSIFIED_FLASH_SECONDS", "3"))
    INFO_HOLD = float(os.environ.get("INFO_HOLD_SECONDS", "3"))
    INFO_FADE = float(os.environ.get("INFO_FADE_SECONDS", "3"))
    DISASTER_FADE = float(os.environ.get("DISASTER_FADE_SECONDS", "3"))

    mode = os.environ.get("HA_MODE", "webhook").strip().lower()
    cmd = (argv[1] if len(argv) > 1 else "run").lower()

    if cmd == "probe-url":
        body = http_json(
            ZABBIX_API,
            {"jsonrpc": "2.0", "method": "apiinfo.version", "params": {}, "id": 1},
        )
        print(f"Zabbix reachable: {ZABBIX_API} → version {body.get('result') if isinstance(body, dict) else body}")
        return 0

    token = require_zabbix_token()

    if cmd == "once":
        once(token, mode)
        return 0

    if cmd != "run":
        print(f"Usage: {argv[0]} [run|once|probe-url]", file=sys.stderr)
        return 2

    print(f"Polling {ZABBIX_API} every {INTERVAL}s → HA {mode} @ {HA_BASE}")
    last_sev: Any = object()
    while True:
        try:
            _, sev = once(token, mode, last_sev)
            entered = sev != last_sev
            last_sev = sev
            if mode == "api" and sev == 5:
                anim_disaster_fade_until(time.monotonic() + INTERVAL)
            else:
                # After entry animations, wait the poll interval
                if not (mode == "api" and entered and sev in (0, 1)):
                    time.sleep(INTERVAL)
                else:
                    # 0/1 animations already consumed time; still wait full interval before next poll
                    time.sleep(INTERVAL)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            time.sleep(INTERVAL)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(0)

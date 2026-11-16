#!/usr/bin/env python3
"""
Severity_Beacon → Home Assistant (simple path)

Polls Zabbix problem.get (same idea as github.com/sgevents-sam/Severity_Beacon)
and sets light.koti_office_strip_led green / orange / red.

Defaults match Severity_Beacon filters:
  - acknowledged=false, suppressed=false
  - severities 2,3,4,5 (warning / average / high / disaster)
  - highest open severity wins
  - no matching problems → green

Colors (office strip; solid, no WERMA flash):
  green  = no problems
  orange = max severity 0–3 (info / warning / average / not classified)
  red    = max severity 4–5 (high / disaster)

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

ZABBIX_URL = os.environ.get("ZABBIX_URL", "https://zabbix.koti.tech").rstrip("/")
ZABBIX_API = os.environ.get("ZABBIX_API", f"{ZABBIX_URL}/api_jsonrpc.php")
HA_BASE = os.environ.get("HA_BASE", "http://10.203.3.120").rstrip("/")
ENTITY = os.environ.get("HA_ENTITY", "light.koti_office_strip_led")
WEBHOOK_ID = os.environ.get("HA_WEBHOOK_ID", "zabbix_led_status")
INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "45"))
HOST_GROUP_ID = os.environ.get("ZABBIX_HOST_GROUP_ID", "").strip()  # optional, like Severity_Beacon -g
SEVERITIES = [int(x) for x in os.environ.get("ZABBIX_SEVERITIES", "2,3,4,5").split(",") if x.strip()]

RGB = {
    "green": [0, 255, 0],
    "orange": [255, 140, 0],
    "red": [255, 0, 0],
}


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


def color_for_severity(sev: int | None) -> str:
    if sev is None or sev < 0:
        return "green"
    if sev >= 4:
        return "red"
    return "orange"


def set_led_via_webhook(color: str, sev: int | None) -> None:
    # Reuse existing automation.zabbix_led_status (no HA long-lived token needed).
    if color == "green":
        payload = {"severity": "OK", "status": "RESOLVED", "value": "0"}
    elif color == "red":
        payload = {"severity": "Disaster" if (sev or 0) >= 5 else "High", "status": "PROBLEM", "value": "1"}
    else:
        payload = {"severity": "Warning", "status": "PROBLEM", "value": "1"}
    url = f"{HA_BASE}/api/webhook/{WEBHOOK_ID}"
    http_json(url, payload)


def set_led_via_ha_api(color: str) -> None:
    token = os.environ.get("HA_TOKEN", "").strip()
    if not token or token == "YOUR_HA_LONG_LIVED_TOKEN":
        raise RuntimeError("HA_TOKEN required for mode=api (or use mode=webhook)")
    rgb = RGB[color]
    http_json(
        f"{HA_BASE}/api/services/light/turn_on",
        {"entity_id": ENTITY, "rgb_color": rgb, "brightness": 255},
        headers={"Authorization": f"Bearer {token}"},
    )


def set_led(color: str, sev: int | None, mode: str) -> None:
    if mode == "api":
        set_led_via_ha_api(color)
    else:
        set_led_via_webhook(color, sev)


def once(token: str, mode: str) -> str:
    sev = max_open_severity(token)
    color = color_for_severity(sev)
    set_led(color, sev, mode)
    label = "none" if sev is None else str(sev)
    print(f"max_severity={label} → {color} ({ENTITY})")
    return color


def main(argv: list[str]) -> int:
    load_env_file(ENV_FILE)
    # Re-read after env file (defaults were captured earlier for some constants)
    global ZABBIX_URL, ZABBIX_API, HA_BASE, ENTITY, WEBHOOK_ID, INTERVAL, HOST_GROUP_ID, SEVERITIES
    ZABBIX_URL = os.environ.get("ZABBIX_URL", ZABBIX_URL).rstrip("/")
    ZABBIX_API = os.environ.get("ZABBIX_API", f"{ZABBIX_URL}/api_jsonrpc.php")
    HA_BASE = os.environ.get("HA_BASE", HA_BASE).rstrip("/")
    ENTITY = os.environ.get("HA_ENTITY", ENTITY)
    WEBHOOK_ID = os.environ.get("HA_WEBHOOK_ID", WEBHOOK_ID)
    INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", str(INTERVAL)))
    HOST_GROUP_ID = os.environ.get("ZABBIX_HOST_GROUP_ID", "").strip()
    SEVERITIES = [int(x) for x in os.environ.get("ZABBIX_SEVERITIES", "2,3,4,5").split(",") if x.strip()]

    mode = os.environ.get("HA_MODE", "webhook").strip().lower()
    cmd = (argv[1] if len(argv) > 1 else "run").lower()

    if cmd == "probe-url":
        # Reachability only — no real API key required for apiinfo.version
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
    last = None
    while True:
        try:
            color = once(token, mode)
            last = color
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
        time.sleep(INTERVAL)
        _ = last
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(0)

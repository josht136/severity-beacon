#!/usr/bin/env python3
"""
Severity Beacon Lights — Home Assistant Supervisor add-on.

Polls Zabbix problem.get using the same filters as
https://github.com/sgevents-sam/Severity_Beacon and sets an HA RGB light
via the Supervisor Core API proxy (SUPERVISOR_TOKEN).

Severity_Beacon semantics mirrored here:
  - problem.get with acknowledged=false, suppressed=false
  - optional host group → enabled hosts (status=0) filter
  - severities default 2,3,4,5 (warning / average / high / disaster)
  - highest open severity wins
  - no matching problems → "none" color (green by default)

WERMA flash patterns are not used; HA lights get solid RGB from the
configurable color_* options (defaults match the office strip mapping:
none→green, low→orange, high→red).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OPTIONS_PATH = Path("/data/options.json")
HA_API = os.environ.get("HA_API_BASE", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("severity_beacon")


def load_options() -> dict[str, Any]:
    if not OPTIONS_PATH.is_file():
        raise SystemExit(f"Missing options file: {OPTIONS_PATH}")
    return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))


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


def http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(url, data=data, method=method)
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


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


def max_open_severity(
    api_url: str,
    token: str,
    severities: list[int],
    host_group_id: str,
) -> int | None:
    """Highest open problem severity, or None if all-clear."""
    params: dict[str, Any] = {
        "acknowledged": False,
        "suppressed": False,
        "severities": severities,
        "output": ["eventid", "severity", "name"],
        "sortfield": ["eventid"],
        "sortorder": "DESC",
    }
    if host_group_id:
        hosts = enabled_host_ids(api_url, token, host_group_id)
        if not hosts:
            log.info("No enabled hosts in host group %s — treating as all-clear", host_group_id)
            return None
        params["hostids"] = hosts
        log.debug("%s enabled hosts in host group", len(hosts))

    problems = zabbix_rpc(api_url, token, "problem.get", params) or []
    log.info("%s active problems from Zabbix", len(problems))
    if not problems:
        return None
    return max(int(p["severity"]) for p in problems)


def color_bucket(sev: int | None, high_threshold: int) -> str:
    if sev is None or sev < 0:
        return "none"
    if sev >= high_threshold:
        return "high"
    return "low"


def set_light_rgb(entity_id: str, rgb: list[int], brightness: int) -> None:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN is not set (are we running as an HA add-on?)")
    http_json(
        f"{HA_API}/services/light/turn_on",
        {
            "entity_id": entity_id,
            "rgb_color": rgb,
            "brightness": brightness,
        },
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
    )


def normalize_zabbix_api(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/api_jsonrpc.php"):
        return base
    return f"{base}/api_jsonrpc.php"


def run_once(opts: dict[str, Any], colors: dict[str, list[int]], last_bucket: str | None) -> str:
    api_url = normalize_zabbix_api(str(opts["zabbix_url"]))
    token = str(opts.get("zabbix_api_token") or "").strip()
    if not token:
        raise RuntimeError("zabbix_api_token is empty — set it in the add-on configuration")

    severities = parse_severities(str(opts.get("severities") or "2,3,4,5"))
    host_group_id = str(opts.get("host_group_id") or "").strip()
    threshold = int(opts.get("high_severity_threshold") or 4)
    entity = str(opts["light_entity_id"]).strip()
    brightness = int(opts.get("brightness") or 255)

    sev = max_open_severity(api_url, token, severities, host_group_id)
    bucket = color_bucket(sev, threshold)
    rgb = colors[bucket]
    set_light_rgb(entity, rgb, brightness)

    label = "none" if sev is None else str(sev)
    if bucket != last_bucket:
        log.info("max_severity=%s → %s rgb=%s entity=%s", label, bucket, rgb, entity)
    else:
        log.debug("max_severity=%s → %s (unchanged)", label, bucket)
    return bucket


def main() -> int:
    opts = load_options()
    colors = {
        "none": parse_rgb(str(opts.get("color_none") or "0,255,0"), [0, 255, 0]),
        "low": parse_rgb(str(opts.get("color_low") or "255,140,0"), [255, 140, 0]),
        "high": parse_rgb(str(opts.get("color_high") or "255,0,0"), [255, 0, 0]),
    }
    interval = max(5, int(opts.get("poll_interval_seconds") or 45))
    entity = str(opts.get("light_entity_id") or "").strip()
    if not entity:
        raise SystemExit("light_entity_id is required")

    log.info(
        "Polling Zabbix every %ss → light %s via %s",
        interval,
        entity,
        HA_API,
    )
    last: str | None = None
    while True:
        try:
            # Re-read options so UI changes apply without full container recreate
            # when Supervisor rewrites /data/options.json (restart still preferred).
            if OPTIONS_PATH.is_file():
                opts = load_options()
                colors = {
                    "none": parse_rgb(str(opts.get("color_none") or "0,255,0"), [0, 255, 0]),
                    "low": parse_rgb(str(opts.get("color_low") or "255,140,0"), [255, 140, 0]),
                    "high": parse_rgb(str(opts.get("color_high") or "255,0,0"), [255, 0, 0]),
                }
                interval = max(5, int(opts.get("poll_interval_seconds") or 45))
            last = run_once(opts, colors, last)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, OSError, ValueError) as exc:
            log.error("%s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.info("stopped")
        raise SystemExit(0)

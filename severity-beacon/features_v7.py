"""Severity Beacon 0.7 helpers: multi-light, mute, audio, HA entity queries."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("severity_beacon")

MUTE_PATH = Path("/data/mute.json")
TIER_KEYS = (
    "none",
    "unclassified",
    "info",
    "warning",
    "average",
    "high",
    "disaster",
)

_mute_lock = threading.Lock()
# Set when full suppress or lights suppress changes, so the Zabbix loop can
# stop painting without waiting for poll_interval_seconds.
_controls_event = threading.Event()
_mute_state: dict[str, Any] = {
    "muted": False,
    "muted_at_severity": None,
    "muted_until": None,
    "lights_suppressed": False,
    "audio_suppressed": False,
    "updated_at": None,
}
_button_last_seen: dict[str, str] = {}
_audio_last_fire: dict[str, float] = {}
_runtime_status: dict[str, Any] = {
    "severity": None,
    "muted": False,
    "muted_at_severity": None,
    "lights_suppressed": False,
    "audio_suppressed": False,
    "last_poll": None,
    "problem_count": 0,
    "lights": [],
}


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


def parse_entity_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]


def registry_lights(opts: dict[str, Any]) -> list[str]:
    lights = parse_entity_list(opts.get("lights"))
    if not lights:
        legacy = str(opts.get("light_entity_id") or "").strip()
        if legacy:
            lights = [legacy]
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for e in lights:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def targets_for_severity(opts: dict[str, Any], tier_key: str, registry: list[str]) -> list[str]:
    raw = opts.get(f"targets_{tier_key}")
    targets = parse_entity_list(raw)
    if not targets:
        return list(registry)
    reg_set = set(registry)
    filtered = [t for t in targets if t in reg_set or not reg_set]
    return filtered or list(registry)


def migrate_lights_options(opts: dict[str, Any]) -> dict[str, Any]:
    out = dict(opts)
    lights = registry_lights(out)
    out["lights"] = ",".join(lights)
    if lights and not str(out.get("light_entity_id") or "").strip():
        out["light_entity_id"] = lights[0]
    elif lights:
        out["light_entity_id"] = lights[0]
    if out.get("mute_entities") is None:
        out["mute_entities"] = ""
    if out.get("audio_toggle_entities") is None:
        out["audio_toggle_entities"] = ""
    if out.get("lights_toggle_entities") is None:
        out["lights_toggle_entities"] = ""
    if out.get("mute_timeout_minutes") in (None, ""):
        out["mute_timeout_minutes"] = 0
    if out.get("escalate_only") in (None, ""):
        out["escalate_only"] = False
    if out.get("audio_enabled") in (None, ""):
        out["audio_enabled"] = False
    if out.get("audio_media_player") is None:
        out["audio_media_player"] = ""
    if out.get("audio_volume") in (None, ""):
        out["audio_volume"] = 0.5
    if out.get("audio_recovery_chime") in (None, ""):
        out["audio_recovery_chime"] = False
    if out.get("quiet_hours_start") is None:
        out["quiet_hours_start"] = ""
    if out.get("quiet_hours_end") is None:
        out["quiet_hours_end"] = ""
    if out.get("notify_service") is None:
        out["notify_service"] = ""
    if out.get("notify_on_severities") in (None, ""):
        out["notify_on_severities"] = "4,5"
    for key in TIER_KEYS:
        if out.get(f"targets_{key}") is None:
            out[f"targets_{key}"] = ""
        if out.get(f"audio_mode_{key}") in (None, ""):
            out[f"audio_mode_{key}"] = "none"
        if out.get(f"audio_media_{key}") is None:
            out[f"audio_media_{key}"] = ""
        if out.get(f"audio_tts_{key}") is None:
            out[f"audio_tts_{key}"] = ""
        if out.get(f"audio_tts_read_body_{key}") in (None, ""):
            # Default on for real alert tiers so TTS says what broke
            out[f"audio_tts_read_body_{key}"] = key != "none"
        if out.get(f"audio_repeat_{key}") in (None, ""):
            out[f"audio_repeat_{key}"] = 0
    return out


def load_mute_state() -> dict[str, Any]:
    global _mute_state
    with _mute_lock:
        if MUTE_PATH.is_file():
            try:
                data = json.loads(MUTE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    _mute_state.update(data)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("mute state load failed: %s", exc)
        _mute_state.setdefault("lights_suppressed", False)
        _mute_state.setdefault("audio_suppressed", False)
        return dict(_mute_state)


def save_mute_state(state: dict[str, Any]) -> None:
    global _mute_state
    with _mute_lock:
        _mute_state.update(state)
        _mute_state["updated_at"] = time.time()
        try:
            MUTE_PATH.parent.mkdir(parents=True, exist_ok=True)
            MUTE_PATH.write_text(json.dumps(_mute_state, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("mute state save failed: %s", exc)


def get_mute_snapshot() -> dict[str, Any]:
    with _mute_lock:
        return dict(_mute_state)


def set_muted(muted: bool, severity: int | None = None, timeout_minutes: float = 0) -> dict[str, Any]:
    state = {
        "muted": bool(muted),
        "muted_at_severity": severity if muted else None,
        "muted_until": None,
    }
    if muted and timeout_minutes and timeout_minutes > 0:
        state["muted_until"] = time.time() + timeout_minutes * 60.0
    save_mute_state(state)
    log.info(
        "mute → %s (at_severity=%s until=%s)",
        state["muted"],
        state["muted_at_severity"],
        state["muted_until"],
    )
    return get_mute_snapshot()


def clear_mute() -> dict[str, Any]:
    return set_muted(False)


def set_audio_suppressed(suppressed: bool) -> dict[str, Any]:
    save_mute_state({"audio_suppressed": bool(suppressed)})
    log.info("audio_suppressed → %s", bool(suppressed))
    return get_mute_snapshot()


def set_lights_suppressed(suppressed: bool) -> dict[str, Any]:
    save_mute_state({"lights_suppressed": bool(suppressed)})
    log.info("lights_suppressed → %s", bool(suppressed))
    return get_mute_snapshot()


def alert_audio_blocked(snap: dict[str, Any] | None = None) -> bool:
    """True when live alert speech should stay silent. Button confirmations bypass this."""
    state = snap if snap is not None else get_mute_snapshot()
    return bool(state.get("muted") or state.get("audio_suppressed"))


def lights_commands_held(snap: dict[str, Any] | None = None) -> bool:
    """True when the beacon must not paint severity colours."""
    state = snap if snap is not None else get_mute_snapshot()
    return bool(state.get("muted") or state.get("lights_suppressed"))


def check_mute_timeout() -> bool:
    """Return True if mute was cleared due to timeout."""
    snap = get_mute_snapshot()
    until = snap.get("muted_until")
    if snap.get("muted") and until and time.time() >= float(until):
        clear_mute()
        log.info("mute timeout expired — unmuted")
        return True
    return False


def update_runtime_status(**kwargs: Any) -> None:
    _runtime_status.update(kwargs)
    snap = get_mute_snapshot()
    _runtime_status["muted"] = bool(snap.get("muted"))
    _runtime_status["muted_at_severity"] = snap.get("muted_at_severity")
    _runtime_status["lights_suppressed"] = bool(snap.get("lights_suppressed"))
    _runtime_status["audio_suppressed"] = bool(snap.get("audio_suppressed"))


def get_runtime_status() -> dict[str, Any]:
    snap = get_mute_snapshot()
    out = dict(_runtime_status)
    out["muted"] = bool(snap.get("muted"))
    out["muted_at_severity"] = snap.get("muted_at_severity")
    out["muted_until"] = snap.get("muted_until")
    out["lights_suppressed"] = bool(snap.get("lights_suppressed"))
    out["audio_suppressed"] = bool(snap.get("audio_suppressed"))
    return out


def ha_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def ha_call_service(
    ha_api: str,
    token: str,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> None:
    http_json(
        f"{ha_api}/services/{domain}/{service}",
        data,
        headers=ha_headers(token),
        timeout=20,
    )


def set_lights_rgb(
    ha_api: str,
    token: str,
    entity_ids: list[str],
    rgb: list[int],
    brightness: int,
) -> None:
    if not entity_ids:
        return
    rgb_i = [int(c) for c in rgb]
    is_black = rgb_i == [0, 0, 0] or brightness <= 0
    for entity_id in entity_ids:
        try:
            if is_black:
                ha_call_service(ha_api, token, "light", "turn_off", {"entity_id": entity_id})
            else:
                ha_call_service(
                    ha_api,
                    token,
                    "light",
                    "turn_on",
                    {
                        "entity_id": entity_id,
                        "rgb_color": rgb_i,
                        "brightness": max(1, int(brightness)),
                    },
                )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            log.warning("light %s failed: %s", entity_id, exc)


def fetch_ha_states(ha_api: str, token: str) -> list[dict[str, Any]]:
    data = http_json(f"{ha_api}/states", headers=ha_headers(token), timeout=30)
    return data if isinstance(data, list) else []


def signal_controls_changed() -> None:
    """Wake the Zabbix loop after a lights or full-suppress change."""
    _controls_event.set()


def controls_changed() -> bool:
    return _controls_event.is_set()


def consume_controls_changed() -> bool:
    if not _controls_event.is_set():
        return False
    _controls_event.clear()
    return True


def fetch_entity_states(ha_api: str, token: str, entity_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch only the listed entities. Used by the 1s button loop."""
    out: list[dict[str, Any]] = []
    for eid in entity_ids:
        url = f"{ha_api}/states/{urllib.parse.quote(eid, safe='._')}"
        try:
            data = http_json(url, headers=ha_headers(token), timeout=4)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                log.debug("button state %s failed: %s", eid, exc)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.debug("button state %s failed: %s", eid, exc)
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def filter_entities(
    states: list[dict[str, Any]],
    domains: list[str] | None = None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if not eid or "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        if domains and domain not in domains:
            continue
        attrs = st.get("attributes") or {}
        name = str(attrs.get("friendly_name") or eid)
        out.append({"entity_id": eid, "name": name, "domain": domain, "state": str(st.get("state"))})
    out.sort(key=lambda x: x["entity_id"])
    return out


def poll_mute_buttons(
    ha_api: str,
    token: str,
    mute_entities: list[str],
    on_press: Callable[[str], None],
    states: list[dict[str, Any]] | None = None,
) -> None:
    """Detect edge presses on button/input_button/event/binary_sensor entities."""
    global _button_last_seen
    if not mute_entities:
        return
    if states is None:
        try:
            states = fetch_ha_states(ha_api, token)
        except Exception as exc:  # noqa: BLE001
            log.debug("mute poll states failed: %s", exc)
            return
    by_id = {str(s.get("entity_id")): s for s in states}
    for eid in mute_entities:
        st = by_id.get(eid)
        if not st:
            continue
        domain = eid.split(".", 1)[0]
        state = str(st.get("state") or "")
        last_changed = str(st.get("last_changed") or "")
        prev = _button_last_seen.get(eid)
        if prev is None:
            _button_last_seen[eid] = last_changed
            continue
        if last_changed == prev:
            continue
        _button_last_seen[eid] = last_changed
        # Treat state changes as presses; for binary_sensor only rising to on
        if domain == "binary_sensor" and state.lower() not in ("on", "true", "1"):
            continue
        if domain == "event" and state in ("unknown", "unavailable", ""):
            continue
        on_press(eid)


def poll_button_groups(
    ha_api: str,
    token: str,
    groups: list[tuple[list[str], Callable[[str], None]]],
) -> None:
    """Edge-detect button lists by reading only those entities."""
    wanted: list[str] = []
    seen: set[str] = set()
    for entities, _callback in groups:
        for eid in entities:
            if eid not in seen:
                seen.add(eid)
                wanted.append(eid)
    if not wanted:
        return
    states = fetch_entity_states(ha_api, token, wanted)
    if not states and wanted:
        return
    for entities, callback in groups:
        poll_mute_buttons(ha_api, token, entities, callback, states=states)


def in_quiet_hours(opts: dict[str, Any], now: time.struct_time | None = None) -> bool:
    start = str(opts.get("quiet_hours_start") or "").strip()
    end = str(opts.get("quiet_hours_end") or "").strip()
    if not start or not end:
        return False
    try:
        sh, sm = [int(x) for x in start.split(":", 1)]
        eh, em = [int(x) for x in end.split(":", 1)]
    except ValueError:
        return False
    t = now or time.localtime()
    cur = t.tm_hour * 60 + t.tm_min
    a = sh * 60 + sm
    b = eh * 60 + em
    if a == b:
        return False
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def build_tts_message(
    opts: dict[str, Any],
    tier_key: str,
    *,
    alert_body: str | None = None,
) -> str:
    """Compose TTS as \"Message - Host - Problem\" when reading the alert body."""
    custom = str(opts.get(f"audio_tts_{tier_key}") or "").strip().rstrip(" .-")
    read_body = _as_bool(
        opts.get(f"audio_tts_read_body_{tier_key}"),
        default=(tier_key != "none"),
    )
    body = str(alert_body or "").strip()
    if read_body and body:
        # body is already \"Host - Problem\" (or problem-only / test fallback)
        if custom:
            return f"{custom} - {body}"
        return body
    if custom:
        return custom
    if body:
        return body
    return f"Home Assistant alert: {tier_key}"


def play_severity_audio(
    ha_api: str,
    token: str,
    opts: dict[str, Any],
    tier_key: str,
    *,
    force: bool = False,
    alert_body: str | None = None,
) -> None:
    enabled = opts.get("audio_enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        log.debug("audio skipped: audio_enabled is off")
        return
    # force=True (Ingress Test) still plays while muted / in quiet hours.
    # Button confirmations use speak_button_status and do not come through here.
    if not force and alert_audio_blocked():
        log.info("audio skipped: muted (unmute or use Test)")
        return
    if (
        not force
        and in_quiet_hours(opts)
        and tier_key not in ("high", "disaster")
    ):
        log.debug("audio skipped: quiet hours (tier=%s)", tier_key)
        return
    mode = str(opts.get(f"audio_mode_{tier_key}") or "none").strip().lower()
    if mode in ("", "none"):
        log.debug("audio skipped: mode none for %s", tier_key)
        return
    repeat = float(opts.get(f"audio_repeat_{tier_key}") or 0)
    now = time.time()
    last = _audio_last_fire.get(tier_key, 0.0)
    if not force and repeat > 0 and (now - last) < repeat:
        return
    if not force and repeat <= 0 and last > 0 and tier_key in _audio_last_fire:
        # play-once until severity changes (caller clears on severity change)
        return

    player = str(opts.get(f"audio_player_{tier_key}") or opts.get("audio_media_player") or "").strip()
    volume = float(opts.get("audio_volume") or 0.5)
    volume = max(0.0, min(1.0, volume))

    try:
        if player and volume >= 0:
            try:
                ha_call_service(
                    ha_api,
                    token,
                    "media_player",
                    "volume_set",
                    {"entity_id": player, "volume_level": volume},
                )
            except Exception:  # noqa: BLE001
                pass

        if mode == "tts":
            message = build_tts_message(opts, tier_key, alert_body=alert_body)
            tts_entity = str(opts.get("audio_tts_entity") or "").strip()
            if not player:
                log.warning(
                    "TTS skipped: set Default media player (e.g. media_player from HASS.Agent)"
                )
                return
            if not tts_entity:
                # Fall back to first common Google Translate entity if present — still prefer UI value
                log.warning(
                    "TTS skipped: set TTS entity (e.g. tts.google_translate_en_com)"
                )
                return
            # Modern HA: tts.speak — entity_id is the TTS engine; media_player_entity_id is where it plays
            payload = {
                "entity_id": tts_entity,
                "media_player_entity_id": player,
                "message": message,
            }
            log.info("TTS speak → engine=%s player=%s msg=%r", tts_entity, player, message[:120])
            ha_call_service(ha_api, token, "tts", "speak", payload)
        elif mode in ("media", "repeat_media"):
            media_id = str(opts.get(f"audio_media_{tier_key}") or "").strip()
            if not media_id or not player:
                log.warning("audio media mode needs media id + media_player")
                return
            content_type = str(opts.get(f"audio_content_type_{tier_key}") or "music")
            ha_call_service(
                ha_api,
                token,
                "media_player",
                "play_media",
                {
                    "entity_id": player,
                    "media_content_id": media_id,
                    "media_content_type": content_type,
                },
            )
        else:
            log.warning("unknown audio mode %r for %s", mode, tier_key)
            return
        _audio_last_fire[tier_key] = now
        log.info("audio played tier=%s mode=%s player=%s", tier_key, mode, player or "-")
    except Exception as exc:  # noqa: BLE001
        log.warning("audio failed: %s", exc)


def stop_media_player(ha_api: str, token: str, opts: dict[str, Any]) -> None:
    """Stop the configured player so a mute press cuts alert audio already playing."""
    player = str(opts.get("audio_media_player") or "").strip()
    if not player or not token:
        return
    try:
        ha_call_service(ha_api, token, "media_player", "media_stop", {"entity_id": player})
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.debug("media_stop failed: %s", exc)


def speak_button_status(ha_api: str, token: str, opts: dict[str, Any], message: str) -> None:
    """Speak a button result even when alert audio is muted."""
    player = str(opts.get("audio_media_player") or "").strip()
    tts_entity = str(opts.get("audio_tts_entity") or "").strip()
    if not player or not tts_entity or not token:
        log.info("button speech skipped (set media player and TTS entity): %s", message)
        return
    try:
        volume = float(opts.get("audio_volume") or 0.5)
    except (TypeError, ValueError):
        volume = 0.5
    volume = max(0.0, min(1.0, volume))
    try:
        ha_call_service(
            ha_api,
            token,
            "media_player",
            "volume_set",
            {"entity_id": player, "volume_level": volume},
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        pass
    try:
        log.info("button TTS → %s", message)
        ha_call_service(
            ha_api,
            token,
            "tts",
            "speak",
            {
                "entity_id": tts_entity,
                "media_player_entity_id": player,
                "message": message,
            },
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.warning("button speech failed: %s", exc)


def clear_audio_fire(tier_key: str | None = None) -> None:
    if tier_key is None:
        _audio_last_fire.clear()
    else:
        _audio_last_fire.pop(tier_key, None)


def maybe_notify(
    ha_api: str,
    token: str,
    opts: dict[str, Any],
    sev: int | None,
    label: str,
) -> None:
    service = str(opts.get("notify_service") or "").strip()
    if not service or sev is None:
        return
    allowed = []
    for part in str(opts.get("notify_on_severities") or "4,5").split(","):
        part = part.strip()
        if part.isdigit():
            allowed.append(int(part))
    if sev not in allowed:
        return
    # notify_service like "notify.mobile_app_phone" → domain=notify service=mobile_app_phone
    if "." not in service:
        log.warning("notify_service should be domain.service, got %s", service)
        return
    domain, svc = service.split(".", 1)
    try:
        ha_call_service(
            ha_api,
            token,
            domain,
            svc,
            {
                "title": "Severity Beacon",
                "message": f"Zabbix alert severity {sev} ({label})",
            },
        )
        log.info("notify sent via %s for severity %s", service, sev)
    except Exception as exc:  # noqa: BLE001
        log.warning("notify failed: %s", exc)

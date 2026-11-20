# Severity Beacon Lights (Home Assistant add-on)

Polls Zabbix `problem.get` using [Severity_Beacon](https://github.com/sgevents-sam/Severity_Beacon) semantics and sets a Home Assistant RGB light (not a WERMA serial beacon).

**Graphical settings:** after install, open **Add-ons → Severity Beacon Lights → Open Web UI** for **Base & Recovery**, per-severity **Steady** or **Pattern** modes, colour pickers, and pattern steps (snap/fade + window). YAML Configuration still works for connection fields and RGB strings.

## Behavior (defaults)

| State | Default |
|-------|---------|
| **Base** (no problems) | White `255,255,255` |
| **Recovery** (after clear) | Soft cyan-green `0,220,160` ~3s, then fade to Base |
| `0` Not classified | Pattern yellow↔green snap, window 3s |
| `1` Information | Pattern blue → fade green, window ~6s |
| `2` Warning | Steady orange `255,140,0` |
| `3` Average | Steady deep orange `255,80,0` |
| `4` High | Steady red `255,0,0` |
| `5` Disaster | Pattern red↔orange fade, window **0** (until cleared) |

Highest open severity wins. A new alert during Recovery aborts Recovery and shows the alert.

Zabbix filters (Severity_Beacon-aligned):

- `acknowledged=false`, `suppressed=false`
- Severities default `0,1,2,3,4,5`
- Optional host group → enabled hosts only

Inside HAOS the add-on calls Core via `http://supervisor/core/api` with `SUPERVISOR_TOKEN` (`homeassistant_api: true`) and serves Ingress on port `8099`.

## Config options (summary)

| Option | Notes |
|--------|--------|
| `zabbix_url` / `zabbix_api_token` | Zabbix base URL + API bearer |
| `light_entity_id` | e.g. `light.koti_office_strip_led` |
| `poll_interval_seconds` | Default `45` |
| `host_group_id` | Optional Severity_Beacon `-g` |
| `severities` | Default `0,1,2,3,4,5` |
| `mute_entities` | Full mute button helpers |
| `audio_toggle_entities` | Audio-only mute helpers. Stays off until pressed again |
| `lights_toggle_entities` | Lights-only mute helpers. Base once, then the strip is free for a scene |
| `brightness` | Default `255` |
| `color_base` / `color_recovery` | Idle + recovery RGB |
| `recovery_hold_seconds` / `recovery_fade_seconds` / `recovery_transition` | Recovery timing |
| `mode_*` | Per-tier: `steady` / `pattern` |
| `color_*` / `color_*_alt` | Primary + alt/settle RGB `R,G,B` |
| `window_*` | Pattern window seconds; **`0` = until resolved** |
| `pattern_*` | JSON colour-step lists (transition + fade_s) |

See [DOCS.md](DOCS.md) for the full option map and Ingress usage.

# Severity Beacon Lights (Home Assistant add-on)

**Do not install this add-on on the live Home Assistant host yet.**  
Snapshot the HA VM first. This repository only contains the add-on source for later local/custom install.

Polls Zabbix `problem.get` using [Severity_Beacon](https://github.com/sgevents-sam/Severity_Beacon) semantics and sets a Home Assistant RGB light (not a WERMA serial beacon).

## Behavior

| Condition | Default color |
|-----------|----------------|
| No matching open problems | Green `0,255,0` |
| Max severity &lt; 4 (warning / average / …) | Orange `255,140,0` |
| Max severity ≥ 4 (high / disaster) | Red `255,0,0` |

Zabbix filters (Severity_Beacon-aligned):

- `acknowledged=false`, `suppressed=false`
- Severities default `2,3,4,5`
- Optional host group → enabled hosts only
- Highest open severity wins

Inside HAOS the add-on calls Core via `http://supervisor/core/api` with `SUPERVISOR_TOKEN` (`homeassistant_api: true`).

## Config options (summary)

| Option | Type | Notes |
|--------|------|--------|
| `zabbix_url` | url | Zabbix base URL |
| `zabbix_api_token` | password | API bearer token |
| `light_entity_id` | str | e.g. `light.koti_office_strip_led` |
| `poll_interval_seconds` | int | Default `45` |
| `host_group_id` | optional str | Severity_Beacon `-g` |
| `severities` | str | Default `2,3,4,5` |
| `brightness` | int | Default `255` |
| `color_none` / `color_low` / `color_high` | str | RGB `R,G,B` |
| `high_severity_threshold` | int | Default `4` |

## Install later (after VM snapshot)

See [DOCS.md](DOCS.md). Short version:

1. Snapshot the Home Assistant VM.
2. Add this repo folder as a **local** Supervisor add-on repository (or copy `addon/severity-beacon` under `/addons`).
3. Install **Severity Beacon Lights**, fill options, start manually (`boot: manual`).

**Do not install or restart production HA for add-on testing until you decide to.**

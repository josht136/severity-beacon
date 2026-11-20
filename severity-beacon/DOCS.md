# Severity Beacon Lights — documentation

## What it does

1. Every `poll_interval_seconds`, call Zabbix JSON-RPC `problem.get`.
2. Take the maximum `severity` among unacknowledged, unsuppressed problems (optionally limited to a host group).
3. Drive `light.turn_on` with RGB via the Supervisor Core API proxy:

   `POST http://supervisor/core/api/services/light/turn_on`  
   `Authorization: Bearer $SUPERVISOR_TOKEN`

**State machine (0.6+):**

- **Base** — idle colour when there are no open problems.
- **Alert** — when a problem is active, play that severity’s Steady/Pattern (highest wins).
- **Recovery** — when alerts clear (max severity → none), briefly show Recovery colour, then return to Base. A new alert during recovery aborts recovery and shows the alert.

Inspired by [sgevents-sam/Severity_Beacon](https://github.com/sgevents-sam/Severity_Beacon).

---

## Graphical settings (Ingress)

1. **Settings → Add-ons → Severity Beacon Lights**
2. Click **Open Web UI** (Ingress)

### Base & Recovery (global)

- **Base colour** — idle resting colour (default white)
- **Recovery colour** — brief colour after alerts clear (default soft cyan-green)
- **Recovery hold (s)** — how long to hold Recovery
- **Then to Base** — Snap or Fade; **Recovery fade (s)** is shown only when Fade is selected
- **Test recovery** — saves, previews recovery→base on the LED (~8s)

### Per severity (0–5)

- **Mode** — Steady | Pattern
- **Steady** — single colour picker only
- **Pattern** — colour steps (default 2), per-step **snap** or **fade** (fade time shown only for fade), **Add step**, and **Window (s)**
  - **Window `0`** — keep playing the pattern while this severity remains the active maximum
  - **Window `N`** — play for N seconds, then settle
- **Test** — saves current settings, previews that severity on the LED for ~8s (`POST /api/test/{0–5}`), then resumes live Zabbix polling (hard-capped ≤15s)

Save → options persist via the Supervisor API; the poller hot-reloads `options.json`.

---

## Severity → LED mapping (defaults)

| State | Behavior |
|-------|----------|
| **Base** (no problems) | White `255,255,255` |
| **Recovery** (alerts just cleared) | Soft cyan-green `0,220,160` for ~3s, fade ~1s to Base |
| `0` Not classified | Pattern: yellow↔green snap, window 3s |
| `1` Information | Pattern: blue hold → fade to green, window ~6s |
| `2` Warning | Steady orange |
| `3` Average | Steady deep orange |
| `4` High | Steady red |
| `5` Disaster | Pattern: red↔orange fade, **window 0** (until cleared) |

**Breaking default (0.6.0):** idle colour is white instead of the old steady green `color_none`. If you had customized `color_none` away from green, that value is kept as Base on upgrade. Set Base to green in Ingress if you want the previous look.

Legacy modes `flash` / `fade` / `strobe` from older versions are accepted and mapped to `pattern` on load.

---

## Install from GitHub (updates)

Home Assistant only discovers an add-on when `config.yaml` is in a top-level folder of the git repository. This repo uses `severity-beacon/`.

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories**
2. Add this repository’s GitHub URL.
3. Install **Severity Beacon Lights**. The first build can take several minutes.
4. On the add-on page, turn on **Auto update**, then start it.
5. Open **Open Web UI** for Base/Recovery, modes, colours, and the mute buttons.

Supervisor checks the GitHub `config.yaml` version. With **Auto update** on, a higher version is installed for you. Check for updates from the add-on store menu if you want it immediately.

Do not also run the local-folder copy (`local_severity_beacon`). Two copies will both drive the same lights.

### Local folder (no GitHub)

1. Copy `severity-beacon` to `/addons/severity_beacon` on the HA host.
2. **Settings → Add-ons → Add-on store → ⋮ → Check for updates** / refresh local add-ons.
3. Install / update **Severity Beacon Lights**. Local copies do not follow the GitHub version. Use **Rebuild** after you replace the files.

### Non-add-on “simple path”

Repo root still has `severity_beacon_ha.py` + `severity_beacon_ha.env.example` and related packages. Those are independent of this add-on.

---

## Configuration reference

### Connection (Configuration tab or Ingress)

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `zabbix_url` | yes | `https://zabbix.example.com` | Zabbix base URL |
| `zabbix_api_token` | yes | _(empty)_ | Zabbix API token |
| `light_entity_id` | yes | `light.koti_office_strip_led` | Target light |
| `poll_interval_seconds` | no | `45` | Poll period (5–3600) |
| `host_group_id` | no | _(empty)_ | Optional host group filter |
| `severities` | no | `0,1,2,3,4,5` | Comma-separated severity ints |
| `brightness` | no | `255` | Light brightness |

### Base & Recovery

| Key | Default | Description |
|-----|---------|-------------|
| `color_base` | `255,255,255` | Idle RGB when no problems |
| `color_recovery` | `0,220,160` | Recovery RGB after clear |
| `recovery_hold_seconds` | `3` | Hold Recovery colour |
| `recovery_fade_seconds` | `1` | Fade duration when transition is fade |
| `recovery_transition` | `fade` | `fade` or `snap` to Base |

`color_none` remains in the schema and is kept aligned with `color_base` for upgrades.

### Per-tier alert (prefer Ingress)

| Key pattern | Description |
|-------------|-------------|
| `mode_<tier>` | `steady` \| `pattern` (legacy `flash`/`fade`/`strobe` → pattern) |
| `color_<tier>` / `color_<tier>_alt` | Primary RGB and settle/alt defaults `"R,G,B"` |
| `window_<tier>` | Pattern play window in seconds; **`0` = until resolved** |
| `pattern_<tier>` | JSON `[{r,g,b,duration_s,transition,fade_s},…]` |

Alert tiers: `unclassified`, `info`, `warning`, `average`, `high`, `disaster`.

Legacy timing keys (`period_*`, `unclassified_flash_seconds`, `info_hold_seconds`, `info_fade_seconds`, `disaster_fade_seconds`) are still accepted and mapped into `window_*` / default patterns when `window_*` is unset.

### What is **not** configurable

- Zabbix filters: `acknowledged=false`, `suppressed=false`
- “Highest severity wins” selection logic
- Core API path `http://supervisor/core/api` (Supervisor injects token)

---

## Mute buttons (0.8)

Assign Home Assistant `button`, `input_button`, or `event` helpers in the Ingress **Mute buttons** panel. Zigbee short, double, and long press are device triggers, so point each gesture at its own helper and put only that helper in the list.

| List | Press |
|------|--------|
| Mute buttons | Full mute. Lights return to Base on every poll and alert audio stops. A higher severity, a clear, or the timeout can release it. |
| Audio only | Alert audio stays off until the same button is pressed again. Lights keep following severity. |
| Lights only | The strip goes to Base once, then the beacon stops sending light commands so a scene can hold. Press again to resume severity colours. |

Every one of those presses speaks a short line through the configured media player and TTS entity, including when alert audio is muted.

## Logs & troubleshooting

- Add-on logs: **Settings → Add-ons → Severity Beacon Lights → Log**
- Look for `recovery start` / `recovery done → base` / `recovery aborted` lines after clears
- Ingress blank / 404 → rebuild after upgrading to 0.6.0+; confirm **Open Web UI** on the add-on page
- `zabbix_api_token is empty` → set it in Configuration or Ingress (blank keeps existing)
- Light does not change → confirm entity supports `rgb_color`, and `homeassistant_api: true`

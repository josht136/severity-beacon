# Changelog

## 0.8.2

- Desk-button helpers (full suppress, audio only, lights only) are checked every second on their own loop. A press speaks and applies within about that second, instead of waiting for the Zabbix poll.
- Zabbix still uses `poll_interval_seconds`. An in-progress pattern or recovery stops painting when lights or full suppress is on.

## 0.8.1

- Spoken confirmations and the Ingress status line say **suppressed** instead of muted: "Lights and audio suppressed.", "Audio suppressed.", "Lights suppressed."

## 0.8.0

- **Audio mute** — a button toggles alert audio only. It stays off until that same button is pressed again. A higher severity does not turn it back on.
- **Lights mute** — a button sets the strip to Base once, then the beacon stops sending light commands so a scene can hold. Press again and severity colours resume. No Recovery flash while lights are muted.
- **Spoken confirmations** — every full-mute, audio, or lights press speaks what changed, including when alert audio is muted.
- New options: `audio_toggle_entities`, `lights_toggle_entities`. Ingress actions `POST /api/audio/toggle` and `POST /api/lights/toggle`.

## 0.7.4

- TTS hostname strip: only drop the trailing 6 characters when they are hex
  digits (abbreviated MAC, no colons). Non-hex endings (e.g. ``SERVER``,
  ``CORE01``) keep the full hostname.

## 0.7.3

- Multi-problem TTS summarization: related alerts collapse to one line
  (``{Host} switch is offline along with 3 others``,
  ``{Host} Access Point is offline along with 20 others``,
  or ``{Host} - {Problem} along with N others``)
- Group by normalized problem name; offline alerts also split by host role
  from Zabbix groups (``…/Switch``, ``…/AP``) or name tokens (``-SW-``, ``U7``)
- ``host.get`` enrichment now includes host groups for role detection

## 0.7.2

- Fix TTS missing hostname for problems like **High ping response time** (title has no host)
- Resolve hosts via ``host.get(triggerids=…)`` when ``trigger.get`` / ``event.get`` are denied
- Clear warning logs when host enrichment fails; still speak the problem text

## 0.7.1

- Per-severity **Read Zabbix alert body** checkbox for TTS
- TTS format: **Message - Host - Problem** (host drops trailing 6-char hex MAC ref when present)
- Uptime thresholds like ``uptime < 10m`` spoken as **minutes** (not meters)
- Ingress Test plays TTS even while muted; unmute no longer required to preview audio

## 0.7.0

- **Multi-light registry** — configure many lights/groups; per-severity and per-pattern-step target pills
- **Mute buttons** — assign one or more `button` / `input_button` / `event` entities; mute → Base; higher severity auto-unmutes; clear while muted → Recovery → Base; optional mute timeout; `POST /api/mute` + `/api/unmute`
- **Black = off** — RGB `0,0,0` (or brightness 0) calls `light.turn_off`
- **Audio** — global media_player/TTS/volume, per-severity none|media|tts|repeat_media, quiet hours, optional recovery chime
- **Notify** — optional `notify.*` service on configured severities (default 4,5)
- **Escalate only** — option to avoid re-firing same-severity patterns/audio
- Ingress live status line; HA entity autocomplete via `/api/ha/entities`

## 0.6.0

- **Base colour** — idle resting state when no problems are open (default **white** `255,255,255`). Replaces the old steady-green `color_none` default; documented breaking default change. Customized non-green `color_none` values are kept as Base on upgrade.
- **Recovery** — when max severity drops from an alert → none, briefly show Recovery colour, then settle to Base. A new alert during recovery aborts and shows that severity.
- Ingress: global **Base & Recovery** panel (Base / Recovery colour pickers, hold seconds, snap|fade to Base with fade duration only when fade). Per-severity Steady/Pattern for 0–5 unchanged. **Test recovery** button.
- New options: `color_base`, `color_recovery`, `recovery_hold_seconds` (default 3), `recovery_fade_seconds` (default 1), `recovery_transition` (`fade`|`snap`). Defaults: Recovery soft cyan-green `0,220,160`.

## 0.5.0

- Modes simplified to **Steady** | **Pattern** (legacy flash/fade/strobe map → pattern)
- Steady: single colour picker only (no multi-color / pattern UI)
- Pattern: default 2 colour steps; per-step snap or fade (fade time only when fade); Add step; **window** (`0` = loop until this severity clears / higher wins)
- Engine: solid RGB for steady; cycle steps with snap/fade; highest-severity-wins unchanged; Test buttons preview current mode
- Removed `multicolor_flash_*` from schema; added `window_*`; legacy period timing keys kept optional for upgrades

## 0.4.0

- Every severity tier (none, 0–5) has a mode: `steady` | `flash` | `fade` | `strobe` | `pattern`
- Ingress UI: per-tier mode selector, primary + alt colors, period, multi-color toggle, pattern maker
- **Test** button per severity → `POST /api/test/{none|0–5}` previews on the LED (~8s, max 15s) then returns to live polling
- Engine plays the configured mode for whatever max severity is active (including 2 / 3 / 4 / none)
- Defaults preserve 0.3 behavior: none steady green; 0 flash; 1 pattern (hold+fade); 2–4 steady; 5 fade
- New options: `mode_*`, `color_*_alt` (except existing disaster alt), `pattern_*` / `multicolor_flash_*` for all tiers, `period_*` for none/warning/average/high

## 0.3.0

- Ingress graphical settings UI (Open Web UI on the add-on page)
  - HTML color pickers + hex/RGB fields for every severity color
  - Pattern maker: ordered color stops with duration (add / remove / reorder)
  - Multi-color flash toggles for severities 0, 1, and 5
- Engine plays multi-stop patterns for sev 0 / 1 / 5; 2–4 and none stay steady
- New options: `pattern_unclassified`, `pattern_info`, `pattern_disaster`,
  `multicolor_flash_unclassified`, `multicolor_flash_info`, `multicolor_flash_disaster`
- Options saved via Supervisor API + `options.json` hot-reload (no restart required for LED colors/patterns)
- Backward compatible with existing `color_*` and `*_seconds` fields

## 0.2.0

- Per-severity LED scale with entry animations (state machine; polls wait for animations)
  - none → steady green
  - 0 → yellow/green flash, settle soft yellow
  - 1 → blue hold, fade to green
  - 2 / 3 / 4 → steady orange / deep orange / red
  - 5 → continuous red↔orange fade
- Default `severities` now `0,1,2,3,4,5` (includes Not classified)
- New Settings options: `color_unclassified`, `color_info`, `color_warning`,
  `color_average`, `color_disaster`, `color_disaster_alt`, plus timing options
  `unclassified_flash_seconds`, `info_hold_seconds`, `info_fade_seconds`,
  `disaster_fade_seconds`
- Removed bucket options `color_low` and `high_severity_threshold` (replaced by per-severity colors)

## 0.1.1

- Default `severities` now `1,2,3,4,5` (includes Information)
- Fixes missed alerts such as switch `Link down` (Zabbix severity 1)

## 0.1.0

- Initial Supervisor add-on scaffold
- Zabbix `problem.get` polling (Severity_Beacon filters)
- Sets HA light RGB via Supervisor Core API (`SUPERVISOR_TOKEN`)
- Configurable colors, poll interval, optional host group

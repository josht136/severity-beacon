# Severity Beacon Lights — documentation

## ⚠️ Do not install yet

This add-on is **source only** in the `homeass` git tree. Do **not**:

- Add this repository to the live HA at `10.203.3.120` yet
- Install or start the add-on on production
- Restart / reconfigure the production Home Assistant VM for add-on testing

**Snapshot the VM first**, then install when you are ready.

---

## What it does

1. Every `poll_interval_seconds`, call Zabbix JSON-RPC `problem.get`.
2. Take the maximum `severity` among unacknowledged, unsuppressed problems (optionally limited to a host group).
3. Map that severity to an RGB color and call Home Assistant `light.turn_on` through the Supervisor Core API proxy:

   `POST http://supervisor/core/api/services/light/turn_on`  
   `Authorization: Bearer $SUPERVISOR_TOKEN`

Inspired by [sgevents-sam/Severity_Beacon](https://github.com/sgevents-sam/Severity_Beacon). Physical WERMA flash timing is not reproduced; colors are solid on the configured HA light.

### Severity mapping (defaults)

| Max severity | Bucket | Default RGB |
|--------------|--------|-------------|
| _(none)_ | `none` | `0,255,0` green |
| `0`–`3` (below threshold) | `low` | `255,140,0` orange |
| `≥ 4` | `high` | `255,0,0` red |

Override via `color_*` and `high_severity_threshold`. Severity_Beacon’s default watched severities are warning(2), average(3), high(4), disaster(5) — mirrored by `severities: "2,3,4,5"`.

---

## Install after snapshot (local / custom repository)

### Option A — Supervisor custom repository (preferred when this tree is a git remote)

1. Snapshot the HA VM.
2. Publish or mount this repository somewhere the Supervisor can fetch (or use Option B).
3. In HA: **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
4. Add the repository URL that contains root-level `repository.yaml` and `addon/severity-beacon/config.yaml`.
5. Refresh the store → install **Severity Beacon Lights**.
6. Configure options (Zabbix URL, API token, light entity).
7. **Start** manually (boot is `manual` so it will not auto-start until you change that).

### Option B — Local add-ons folder (no remote)

On HAOS / Supervised hosts with Samba or SSH access to the config share:

1. Snapshot the HA VM.
2. Copy the folder `addon/severity-beacon` to `/addons/severity_beacon` (or `/addons/severity-beacon`) on the host.
3. **Settings → Add-ons → Add-on store → ⋮ → Check for updates** / refresh local add-ons.
4. Install from **Local add-ons**, configure, start manually.

Build happens on the HA host (see `Dockerfile` + `build.yaml`). First install may take several minutes.

### Option C — Keep using the non-add-on “simple path”

If you only need polling without a Supervisor add-on, the repo root still has:

- `severity_beacon_ha.py` + `severity_beacon_ha.env.example`
- `packages/zabbix_severity_beacon.yaml` (HA package / REST sensor)
- webhook automation `ha-automation-zabbix-led.yaml`

Those paths are independent of this add-on.

---

## Configuration reference

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `zabbix_url` | yes | `https://zabbix.example.com` | Zabbix base URL (`/api_jsonrpc.php` appended if missing) |
| `zabbix_api_token` | yes | _(empty)_ | Password field; Zabbix API token |
| `light_entity_id` | yes | `light.koti_office_strip_led` | Target light |
| `poll_interval_seconds` | no | `45` | Poll period (5–3600) |
| `host_group_id` | no | _(empty)_ | Optional host group filter |
| `severities` | no | `2,3,4,5` | Comma-separated severity ints |
| `brightness` | no | `255` | Light brightness |
| `color_none` | no | `0,255,0` | RGB when all-clear |
| `color_low` | no | `255,140,0` | RGB for severities below threshold |
| `color_high` | no | `255,0,0` | RGB for severities ≥ threshold |
| `high_severity_threshold` | no | `4` | Cutover to `color_high` |

---

## Logs & troubleshooting

- Add-on logs: **Settings → Add-ons → Severity Beacon Lights → Log**
- `zabbix_api_token is empty` → set the password field and save / restart the add-on
- Zabbix HTTP / RPC errors → check URL reachability from the HA host and token permissions
- Light does not change → confirm entity supports `rgb_color`, and that `homeassistant_api` is enabled (already set in `config.yaml`)

---

## Safety reminder

Nothing in this documentation authorizes installing on the live system today. Build and review locally; install only after your VM snapshot.

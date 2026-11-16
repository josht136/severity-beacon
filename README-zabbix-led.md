# Zabbix → Home Assistant LED strip

Office Strip LED (`light.koti_office_strip_led`) turns green / orange / red from Zabbix alerts via a **Home Assistant webhook** (no HA long-lived token in Zabbix).

## Entity

| Field | Value |
|--------|--------|
| entity_id | `light.koti_office_strip_led` |
| friendly_name | Strip LED |
| Other lights (not used) | `light.desk_lamp`, `light.innr_rb_282_c` |

## Webhook (preferred)

Automation already created in HA: **Zabbix LED status** (`automation.zabbix_led_status`).

- **URL:** `http://10.203.3.120/api/webhook/zabbix_led_status`
- **Method:** `POST`
- **Auth:** none (local-only webhook)
- **Content-Type:** `application/json`

### Payload fields

| Field | Purpose |
|--------|---------|
| `status` | `OK` / `RESOLVED` / `PROBLEM` (also accepts `Status`) |
| `severity` | Zabbix severity name or number (also `Severity`) |
| `value` | `0` = recovered, `1` = problem (also `Value`) |

### Color mapping

| Condition | Color | RGB |
|-----------|--------|-----|
| `status` OK/resolved/recover, or `value` `0`, or severity contains ok/resolved | Green | `0,255,0` |
| High / Disaster / Critical / `4` / `5` | Red | `255,0,0` |
| Everything else (Warning, Average, Info, Not classified, …) | Orange | `255,140,0` |

### Curl tests (no token)

```bash
# Orange
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"severity":"Warning","status":"PROBLEM","value":"1"}' \
  http://10.203.3.120/api/webhook/zabbix_led_status

# Red
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"severity":"High","status":"PROBLEM","value":"1"}' \
  http://10.203.3.120/api/webhook/zabbix_led_status

# Green (recovery)
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"severity":"High","status":"RESOLVED","value":"0"}' \
  http://10.203.3.120/api/webhook/zabbix_led_status
```

Direct light tests (token required) are in `test-led-colors.sh` — use `HA_TOKEN` env var / placeholder only.

## Zabbix setup

1. **Media type** → type **Webhook**. Import or paste from `zabbix-media-type-ha-led.json`.
2. Script parameters (typical):

   | Name | Value |
   |------|--------|
   | `url` | `http://10.203.3.120/api/webhook/zabbix_led_status` |
   | `severity` | `{TRIGGER.SEVERITY}` |
   | `status` | `{TRIGGER.STATUS}` |
   | `value` | `{EVENT.VALUE}` |

3. Attach the media type to a user (or use an action that sends to that media).
4. Ensure the Zabbix server/proxy can reach `10.203.3.120:80`.
5. **Recovery:** use the same media type so recoveries send `status=RESOLVED` / `value=0` → green.

If your Zabbix version uses different macros, map them into the JSON body keys `severity`, `status`, and `value`.

## Automation YAML (backup / paste)

See `ha-automation-zabbix-led.yaml`. Re-create via UI (**Settings → Automations**) or:

`POST /api/config/automation/config/zabbix_led_status` with that config (Bearer token).

## Security notes

- Prefer this webhook so the HA long-lived access token never lives in Zabbix.
- Webhook is `local_only: true` (LAN / same network as HA).
- If you pasted a long-lived token into chat for setup: **revoke/refresh it** in HA (**Profile → Long-lived access tokens**) after config.
- Do not commit real tokens; helpers use `YOUR_HA_LONG_LIVED_TOKEN` / `$HA_TOKEN`.

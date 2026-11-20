# Zabbix → Home Assistant LED strip

Office Strip LED (`light.koti_office_strip_led`) shows Zabbix health as **green / orange / red**.

Inspired by [Severity_Beacon](https://github.com/sgevents-sam/Severity_Beacon) (WERMA serial beacon): poll Zabbix `problem.get`, take the **highest** open severity, drive a light. Here the light is HA instead of WERMA.

---

## Simple / current (recommended)

**Local poller** in this repo: `severity_beacon_ha.py`  
Polls `https://zabbix.example.com` every 45s and updates the LED via the existing HA webhook (no HA long-lived token required).

### Where to paste the Zabbix API key

1. Copy the example env file:
   ```bash
   cp /home/koti/homeass/severity_beacon_ha.env.example /home/koti/homeass/severity_beacon_ha.env
   ```
2. Edit **`/home/koti/homeass/severity_beacon_ha.env`**
3. Set this exact key (replace the placeholder with your real token):
   ```bash
   ZABBIX_API_TOKEN=YOUR_TOKEN_HERE
   ```
4. Create a **read-only** Zabbix API token in Zabbix UI → **Users → API tokens** (or your user → API tokens), then paste it over `YOUR_TOKEN_HERE`.

Keep the token only in `severity_beacon_ha.env` (gitignored). Do not put it in chat or commit it.

### How to enable / run

```bash
cd /home/koti/homeass

# Optional: confirm Zabbix URL is reachable (no real key needed)
python3 severity_beacon_ha.py probe-url

# One-shot after you paste the key
python3 severity_beacon_ha.py once

# Continuous poll (primary)
python3 severity_beacon_ha.py run
```

Leave `HA_MODE=webhook` (default) so the script POSTs to  
`http://homeassistant.local/api/webhook/zabbix_led_status`  
which is already handled by automation **Zabbix LED status**.

Optional: keep it running in the background, e.g. `tmux` / `screen`, or a user cron/systemd unit that runs `python3 /home/koti/homeass/severity_beacon_ha.py run`.

### Color mapping

**Add-on (recommended):** full per-severity scale + animations — all colors/timings in add-on Settings. See `severity-beacon/DOCS.md`.

**Simple poller `HA_MODE=api`:** same scale (env timings optional). **`HA_MODE=webhook`:** approximates to green / orange / red only.

| Max severity | Add-on / api mode | Webhook approx |
|--------------|-------------------|----------------|
| none | Steady green `0,255,0` | green |
| 0 | Flash yellow/green → soft yellow | orange |
| 1 | Blue hold → fade green | orange |
| 2 | Steady orange `255,140,0` | orange |
| 3 | Steady deep orange `255,80,0` | orange |
| 4 | Steady red `255,0,0` | red |
| 5 | Red↔orange fade | red |

Poll filters: `acknowledged=false`, `suppressed=false`, severities `0,1,2,3,4,5`. Optional host-group filter: `ZABBIX_HOST_GROUP_ID`.

### Verify (3 steps)

1. **URL:** `python3 severity_beacon_ha.py probe-url` → prints Zabbix version (expect `7.x`).
2. **Auth + LED:** after pasting the key, `python3 severity_beacon_ha.py once` → prints `max_severity=… → green|orange|red` and the strip changes.
3. **HA UI:** confirm `light.koti_office_strip_led` color, or  
   `./test-led-colors.sh webhook` to exercise green/orange/red via the webhook path.

---

## Optional: HA-native REST poll (still simple — no add-on)

If you prefer Home Assistant itself to poll Zabbix (no Python process), use the package under `packages/`:

1. **Paste API key on HAOS** in **`/config/secrets.yaml`**:
   ```yaml
   zabbix_api_token: YOUR_TOKEN_HERE
   zabbix_api_authorization: Bearer YOUR_TOKEN_HERE
   ```
   (File editor / Samba / Studio Code Server → `secrets.yaml`)

2. Copy `packages/zabbix_severity_beacon.yaml` → `/config/packages/zabbix_severity_beacon.yaml`  
   and ensure `configuration.yaml` has:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Check config → Restart HA.

Entities added: `sensor.zabbix_max_severity`, `sensor.zabbix_led_color`, automation `zabbix_severity_beacon_led`.

Use **either** the Python poller **or** the HA REST package as the primary poller — not both at once — to avoid fighting over the LED.

---

## Legacy: Zabbix → HA webhook (inbound)

Cloud Zabbix cannot reach a private Home Assistant address on the LAN, so inbound webhooks from cloud Zabbix are not the primary path. The webhook automation may remain for local tests / LAN Zabbix:

- Automation: `automation.zabbix_led_status`
- URL: `http://homeassistant.local/api/webhook/zabbix_led_status`
- Helper media type: `zabbix-media-type-ha-led.json`

---

## Security

- **Zabbix API key** stays in `severity_beacon_ha.env` and/or HA `/config/secrets.yaml` only.
- Prefer webhook mode so an HA long-lived token is not required for the poller.
- If an old HA long-lived token was pasted in chat for setup: **revoke it** in HA → **Profile → Long-lived access tokens**.
- Placeholders only in repo files: `YOUR_TOKEN_HERE` / `YOUR_HA_LONG_LIVED_TOKEN`.

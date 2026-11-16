#!/usr/bin/env bash
# Direct light.turn_on color checks (needs token). Prefer webhook tests below for day-to-day.
#
# !!! ROTATE: never hardcode a live long-lived token here. Export HA_TOKEN for one-off tests,
# then revoke/refresh that token in Home Assistant (Profile → Long-lived access tokens).
#
set -euo pipefail

HA_BASE="${HA_BASE:-http://10.203.3.120}"
ENTITY="${ENTITY:-light.koti_office_strip_led}"
WEBHOOK_URL="${WEBHOOK_URL:-$HA_BASE/api/webhook/zabbix_led_status}"

usage() {
  cat <<'EOF'
Usage:
  ./test-led-colors.sh webhook          # no token; exercises Zabbix→HA path
  HA_TOKEN=... ./test-led-colors.sh api # direct light.turn_on green/orange/red
EOF
}

set_via_api() {
  local r="$1" g="$2" b="$3" label="$4"
  if [[ -z "${HA_TOKEN:-}" || "$HA_TOKEN" == "YOUR_HA_LONG_LIVED_TOKEN" ]]; then
    echo "Set HA_TOKEN to a long-lived access token (placeholder refused)." >&2
    exit 1
  fi
  echo "API → $label rgb($r,$g,$b)"
  curl -sS -X POST \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\":\"$ENTITY\",\"rgb_color\":[$r,$g,$b],\"brightness\":255}" \
    "$HA_BASE/api/services/light/turn_on" >/dev/null
  sleep 1
  curl -sS -H "Authorization: Bearer $HA_TOKEN" "$HA_BASE/api/states/$ENTITY" \
    | python3 -c "import json,sys; s=json.load(sys.stdin); a=s['attributes']; print(' ', s['state'], a.get('rgb_color'))"
}

set_via_webhook() {
  local payload="$1" label="$2"
  echo "Webhook → $label  $payload"
  curl -sS -o /dev/null -w "  HTTP %{http_code}\n" -X POST \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "$WEBHOOK_URL"
  sleep 1
}

case "${1:-}" in
  api)
    set_via_api 0 255 0 green
    set_via_api 255 140 0 orange
    set_via_api 255 0 0 red
    ;;
  webhook)
    set_via_webhook '{"severity":"Warning","status":"PROBLEM","value":"1"}' orange
    set_via_webhook '{"severity":"High","status":"PROBLEM","value":"1"}' red
    set_via_webhook '{"severity":"High","status":"RESOLVED","value":"0"}' green
    echo "Visually confirm Strip LED colors; use HA UI or HA_TOKEN=... ./test-led-colors.sh api to read state."
    ;;
  *)
    usage
    exit 1
    ;;
esac

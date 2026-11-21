#!/usr/bin/env bash
# Sync the official Windows x64 Hub fingerprint to an unmodified AGT headless.
set -euo pipefail

: "${AGT_ADMIN_PASSWORD:?Set AGT_ADMIN_PASSWORD (or AGT API key when no separate admin password exists)}"
AGT_BASE_URL="${AGT_BASE_URL:-http://127.0.0.1:8045}"
FINGERPRINT_URL="${FINGERPRINT_URL:-https://raw.githubusercontent.com/ayatinene/antigravity-hub-fingerprint/main/latest.json}"

for command in curl jq; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing command: $command" >&2; exit 1; }
done

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
chmod 700 "$workdir"

curl -fsSL --retry 3 --connect-timeout 10 --max-time 30 \
  "$FINGERPRINT_URL" -o "$workdir/latest.json"

if ! jq -e '
  (.version | type == "string" and test("^2\\.[0-9]+\\.[0-9]+$")) and
  (.cl | type == "string" and test("^[0-9]{8,12}$")) and
  .os_type == "windows" and .arch == "amd64"
' "$workdir/latest.json" >/dev/null; then
  echo "Invalid or unsupported fingerprint data" >&2
  exit 1
fi

version="$(jq -r '.version' "$workdir/latest.json")"
cl="$(jq -r '.cl' "$workdir/latest.json")"

curl -fsS --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $AGT_ADMIN_PASSWORD" \
  "$AGT_BASE_URL/api/system/hub/fingerprint" > "$workdir/current.json"

if jq -e --arg version "$version" --arg cl "$cl" \
  '.version == $version and .cl == $cl' "$workdir/current.json" >/dev/null; then
  echo "Already current: Hub $version / CL $cl"
  exit 0
fi

curl -fsS --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $AGT_ADMIN_PASSWORD" \
  "$AGT_BASE_URL/api/config" > "$workdir/config.json"

jq -e --arg version "$version" --arg cl "$cl" '
  if (.proxy | type) != "object" then error("AGT configuration lacks proxy object") else
    {config: (. | .proxy.hub_version = $version | .proxy.hub_cl = $cl)}
  end
' "$workdir/config.json" > "$workdir/payload.json"

curl -fsS --connect-timeout 5 --max-time 30 \
  -X POST \
  -H "Authorization: Bearer $AGT_ADMIN_PASSWORD" \
  -H "Content-Type: application/json" \
  --data-binary "@$workdir/payload.json" \
  "$AGT_BASE_URL/api/config" > /dev/null

curl -fsS --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $AGT_ADMIN_PASSWORD" \
  "$AGT_BASE_URL/api/system/hub/fingerprint" > "$workdir/verified.json"

if ! jq -e --arg version "$version" --arg cl "$cl" \
  '.version == $version and .cl == $cl and .source == "custom"' \
  "$workdir/verified.json" >/dev/null; then
  echo "AGT did not apply the requested fingerprint; inspect its logs" >&2
  exit 1
fi

echo "Synced AGT Hub $version / CL $cl (Windows x64)"

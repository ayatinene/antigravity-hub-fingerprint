# Antigravity Hub Fingerprint

Automatically track Google's official **Antigravity Hub (2.x)** releases and extract the genuine **Windows x64 version + Piper changelist (CL)** pair for consumers such as [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager).

No Google account or OAuth credentials are needed. This repository does **not** distribute Google's installers or binaries.

## How it works

1. GitHub Actions checks Google's public Hub `/releases` endpoint every 6 hours (also manually via **Actions → Update official Hub fingerprint → Run workflow**).
2. On a new release, it downloads the **official Windows x64 Hub installer** from Google Cloud Storage and unpacks `language_server.exe` with `7z` on a Linux runner. The executable is **never run**.
3. It scans the embedded `changelist <CL> with baseline` build metadata (falling back to the existing AGT `changelist <CL>` pattern). If the CL is absent or ambiguous, the job fails and leaves the published metadata unchanged.
4. If successful, Actions commits `latest.json` and an immutable-version-named record under `history/` to `main`. The installer and language server are deleted with the temporary job workspace.

Updates are skipped when Google's `version + execution_id` pair has not changed. A smaller version or regressed CL is rejected.

## Data

**Latest fingerprint:** [`latest.json`](latest.json) (created by the first successful Actions run)

Raw URL for scripts:

```text
https://raw.githubusercontent.com/ayatinene/antigravity-hub-fingerprint/main/latest.json
```

Example schema (illustrative; actual values always come from the extraction workflow):

```json
{
  "version": "2.33.0",
  "cl": "1023160026",
  "os_type": "windows",
  "arch": "amd64",
  "execution_id": "<official release execution id>",
  "installer_sha256": "<SHA-256 of downloaded installer>",
  "source_url": "https://storage.googleapis.com/antigravity-public/antigravity-hub/<version>-<execution_id>/windows-x64/Antigravity-x64.exe"
}
```

`installer_sha256` records the digest of the file actually downloaded from the official source; it is **not** a separately published Google checksum or proof of Authenticode verification.

## Sync to stock AGT in Docker / headless mode

AGT's Hub User-Agent currently uses the Windows x64 fingerprint (`os_type=windows; arch=amd64`) even on Linux ARM64. This is why the workflow extracts **Windows x64** rather than a Linux ARM64 binary.

AGT v5.0.4-beta.18 already exposes these authenticated management endpoints:

- `GET /api/system/hub/fingerprint` — inspect the active fingerprint.
- `GET /api/config` — read the current complete config.
- `POST /api/config` — persist config changes and apply `proxy.hub_version` and `proxy.hub_cl` immediately.

[`scripts/sync_agt.sh`](scripts/sync_agt.sh) uses those APIs and requires only `curl` and `jq` on your server. It first checks the current fingerprint, writes the **two Hub fields only** (preserving all other fields), and verifies the applied result. It does **not** modify AGT or require a custom AGT image.

```bash
# AGT management endpoint must be reachable from where the script runs.
export AGT_BASE_URL='http://127.0.0.1:8045'
export AGT_ADMIN_PASSWORD='YOUR_ADMIN_PASSWORD'
bash scripts/sync_agt.sh
```

If AGT has no separate admin password, `AGT_ADMIN_PASSWORD` should contain its configured primary API key instead. The stock admin API requires a valid admin credential even if the proxy API is in off mode.

For unattended updates, store the credential in a **0600-permission** environment file for a root-owned systemd service/timer (or a private cron job). Run the script periodically; it exits without writing anything when AGT already has the published fingerprint. Keep the management API bound to localhost or a trusted network, and never put its password in the repository.

> **Caution:** AGT's `POST /api/config` updates the complete configuration object, so avoid saving settings simultaneously in the Web UI while this script runs. The script fetches and modifies the current object, rather than replacing it with a partial configuration.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_fingerprint.py  # Requires 7z and network access
```

The extraction workflow runs on Linux x64. Linux ARM64 is only a consumer of the JSON; Wine and a desktop environment are not needed anywhere in this setup.

## Sources

- [Google's Hub release catalog](https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/releases)
- [Official Antigravity download page](https://antigravity.google/download/)
- [AGT Hub fingerprint extractor / resolver](https://github.com/lbjlaq/Antigravity-Manager/blob/beta/src-tauri/src/modules/hub_version.rs)

Not affiliated with Google or the Antigravity-Manager project.
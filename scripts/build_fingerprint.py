#!/usr/bin/env python3
"""Publish a Windows x64 Antigravity Hub version/CL pair from Google's installer.

Requires Python 3.11+ and 7z. The Windows binary is unpacked, read, and discarded;
it is never executed or checked into Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

RELEASES_URL = (
    "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/releases"
)
DOWNLOAD_ROOT = "https://storage.googleapis.com/antigravity-public/antigravity-hub"
MAX_DOWNLOAD_BYTES = 700 * 1024 * 1024
VERSION_RE = re.compile(r"2\.[0-9]+\.[0-9]+\Z")
EXECUTION_ID_RE = re.compile(r"[0-9]{8,24}\Z")
CL_RE = re.compile(rb"changelist\s+([0-9]{8,12})\b", re.IGNORECASE)
BASELINE_CL_RE = re.compile(
    rb"changelist\s+([0-9]{8,12})\s+with\s+baseline\b", re.IGNORECASE
)


def get_latest_release() -> tuple[str, str]:
    request = Request(RELEASES_URL, headers={"User-Agent": "antigravity-hub-fingerprint/1.0"})
    with urlopen(request, timeout=30) as response:
        data = response.read(2 * 1024 * 1024)
        if response.read(1):
            raise ValueError("Release list is unexpectedly large")
    releases = json.loads(data)
    if not isinstance(releases, list) or not releases:
        raise ValueError("Official /releases returned no releases")

    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        version = release.get("version")
        execution_id = release.get("execution_id")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            continue
        if not isinstance(execution_id, str) or not EXECUTION_ID_RE.fullmatch(execution_id):
            continue
        candidates.append((version, execution_id))
    if not candidates:
        raise ValueError("No valid Hub 2.x release/version pair in official /releases")
    return max(candidates, key=lambda item: tuple(int(i) for i in item[0].split(".")))


def download_installer(url: str, destination: Path) -> str:
    hasher = hashlib.sha256()
    transferred = 0
    request = Request(url, headers={"User-Agent": "antigravity-hub-fingerprint/1.0"})
    with urlopen(request, timeout=120) as response, destination.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            transferred += len(chunk)
            if transferred > MAX_DOWNLOAD_BYTES:
                raise ValueError("Installer exceeds maximum permitted size")
            stream.write(chunk)
            hasher.update(chunk)
    if transferred < 1_000_000:
        raise ValueError(f"Installer unexpectedly small ({transferred} bytes)")
    return hasher.hexdigest()


def list_7z_entries(archive: Path) -> list[str]:
    result = subprocess.run(
        ["7z", "l", "-slt", str(archive)],
        check=True,
        text=True,
        capture_output=True,
    )
    # Headers also have a `Path =` line. Only inspect archive item records.
    sections = re.split(r"(?m)^-{10,}\s*$", result.stdout, maxsplit=1)
    if len(sections) < 2:
        raise ValueError(f"Cannot read 7z contents of {archive.name}")
    return re.findall(r"(?m)^Path = (.+)$", sections[1])


def pick_entry(paths: list[str], suffixes: tuple[str, ...]) -> str | None:
    matches = []
    for path in paths:
        normalized = path.replace("\\", "/").lower()
        if any(normalized.endswith(suffix) for suffix in suffixes):
            matches.append(path)
    if len(matches) > 1:
        raise ValueError(f"Ambiguous archive entries: {matches}")
    return matches[0] if matches else None


def extract_7z_entry(archive: Path, entry: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["7z", "e", "-y", f"-o{output_dir}", str(archive), entry],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    # `e` flattens archive paths and writes only the requested item.
    extracted = output_dir / Path(entry.replace("\\", "/")).name
    if not extracted.is_file():
        raise FileNotFoundError(f"7z did not extract {entry!r}")
    return extracted


def extract_language_server(installer: Path, working_dir: Path) -> Path:
    outer_entries = list_7z_entries(installer)
    nested = pick_entry(outer_entries, ("/app-64.7z", "app-64.7z"))
    if nested:
        app_archive = extract_7z_entry(installer, nested, working_dir / "nested")
        app_entries = list_7z_entries(app_archive)
        server = pick_entry(app_entries, ("/bin/language_server.exe", "language_server.exe"))
        if not server:
            raise ValueError("Official nested app archive has no language_server.exe")
        return extract_7z_entry(app_archive, server, working_dir / "binary")

    # Some installer layouts contain the server directly instead of app-64.7z.
    server = pick_entry(outer_entries, ("/bin/language_server.exe", "language_server.exe"))
    if server:
        return extract_7z_entry(installer, server, working_dir / "binary")
    raise ValueError("Installer has neither app-64.7z nor language_server.exe")


def extract_cl(binary: Path) -> str:
    baseline: set[str] = set()
    candidates: set[str] = set()
    with binary.open("rb") as stream:
        previous = b""
        while chunk := stream.read(4 * 1024 * 1024):
            data = previous + chunk
            baseline.update(match.decode("ascii") for match in BASELINE_CL_RE.findall(data))
            candidates.update(match.decode("ascii") for match in CL_RE.findall(data))
            previous = data[-256:]
    found = baseline if baseline else candidates
    if len(found) != 1:
        raise ValueError(f"Expected one verifiable Piper CL, found: {sorted(found)}")
    return found.pop()


def read_previous(path: Path) -> dict | None:
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Existing latest.json is not a JSON object")
    return document


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_name(f".{path.name}.tmp")
    try:
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(target, path)
    finally:
        target.unlink(missing_ok=True)


def run(output: Path, history_dir: Path) -> bool:
    version, execution_id = get_latest_release()
    previous = read_previous(output)
    if previous:
        old_version = previous.get("version")
        if not isinstance(old_version, str) or not VERSION_RE.fullmatch(old_version):
            raise ValueError("Existing latest.json contains an invalid version")
        if tuple(map(int, version.split("."))) < tuple(map(int, old_version.split("."))):
            raise ValueError(f"Refusing release downgrade from {old_version} to {version}")
        if (
            old_version == version
            and previous.get("execution_id") == execution_id
            and isinstance(previous.get("cl"), str)
            and re.fullmatch(r"[0-9]{8,12}", previous["cl"])
        ):
            print(f"Unchanged: {version} ({execution_id}), CL {previous['cl']}")
            return False

    url = (
        f"{DOWNLOAD_ROOT}/{version}-{execution_id}/windows-x64/Antigravity-x64.exe"
    )
    print(f"Fetching official Windows x64 Hub {version} ({execution_id})", flush=True)
    with tempfile.TemporaryDirectory(prefix="hub-fingerprint-") as temporary:
        workdir = Path(temporary)
        installer = workdir / "Antigravity-x64.exe"
        installer_sha256 = download_installer(url, installer)
        language_server = extract_language_server(installer, workdir)
        cl = extract_cl(language_server)

    if previous and previous.get("cl", "").isdigit():
        old_cl = int(previous["cl"])
        if int(cl) < old_cl:
            raise ValueError(f"Refusing CL regression from {old_cl} to {cl}")

    metadata = {
        "version": version,
        "cl": cl,
        "os_type": "windows",
        "arch": "amd64",
        "execution_id": execution_id,
        "installer_sha256": installer_sha256,
        "source_url": url,
    }
    history_path = history_dir / f"{version}-{execution_id}.json"
    write_json_atomic(history_path, metadata)
    write_json_atomic(output, metadata)
    print(f"Published: antigravity/hub/{version} (windows/amd64, cl={cl})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("latest.json"))
    parser.add_argument("--history-dir", type=Path, default=Path("history"))
    args = parser.parse_args()
    try:
        run(args.output, args.history_dir)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
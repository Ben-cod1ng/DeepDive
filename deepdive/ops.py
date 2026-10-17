"""Local setup, health, and offline backup/restore commands."""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import urllib.request
import zipfile
from contextlib import contextmanager, closing
from pathlib import Path

from dotenv import dotenv_values
from .config import ROOT, Settings


def initialize(root=ROOT):
    root = Path(root)
    env = root / ".env"
    if not env.exists():
        shutil.copyfile(root / ".env.example", env)
    values = dotenv_values(env)
    secret_dir = root / ".secrets"
    secret_dir.mkdir(exist_ok=True)
    secret_dir.chmod(0o700)
    token_path = secret_dir / "api_token"
    token = values.get("DEEPDIVE_API_TOKEN") or (token_path.read_text().strip() if token_path.exists() else secrets.token_urlsafe(32))
    if token_path.exists() and token_path.read_text().strip() != token:
        raise ValueError("API token differs between .env and .secrets/api_token; reconcile these files before startup")
    token_path.write_text(token, encoding="utf-8")
    # Compose bind-mounts these individual files under a different container UID.
    # The host directory is private; mounted files must be readable by that UID.
    token_path.chmod(0o644)
    contents = env.read_text(encoding="utf-8")
    line = "DEEPDIVE_API_TOKEN=" + token
    if re.search(r"(?m)^DEEPDIVE_API_TOKEN=.*$", contents):
        contents = re.sub(r"(?m)^DEEPDIVE_API_TOKEN=.*$", line, contents)
    else:
        contents += "\n" + line + "\n"
    env.write_text(contents, encoding="utf-8")
    env.chmod(0o600)
    admin = secret_dir / "grafana_admin"
    if not admin.exists():
        admin.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        admin.chmod(0o644)
    print("Local configuration ready. Credentials are in .env and .secrets; no values printed.")


@contextmanager
def offline_lock(data):
    handle = (data / "service.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def backup(data, output):
    data, output = Path(data).resolve(), Path(output).resolve()
    if not (data / "deepdive.sqlite3").is_file():
        raise ValueError("No research database found")
    if output.is_relative_to(data) or output.exists():
        raise ValueError("Choose a new backup file outside the data directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with offline_lock(data), tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        hashes = {}
        for source in data.rglob("*"):
            if source.is_symlink():
                raise ValueError("Backup refuses symbolic links")
            if not source.is_file() or source.name == "service.lock" or source.name.endswith(("-wal", "-shm")):
                continue
            relative = source.relative_to(data)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite3":
                with closing(sqlite3.connect(source)) as origin, closing(sqlite3.connect(destination)) as target:
                    origin.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Database integrity check failed")
            else:
                shutil.copyfile(source, destination)
            hashes[relative.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
        (staging / "backup-manifest.json").write_text(json.dumps({"format": 1, "files": hashes}, indent=2))
        temporary = output.with_suffix(output.suffix + ".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in staging.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(staging).as_posix())
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def restore(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore requires an empty destination; existing research is never overwritten")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("backup-manifest.json"))
        if manifest.get("format") != 1:
            raise ValueError("Unknown backup format")
        expected = set(manifest["files"]) | {"backup-manifest.json"}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Unexpected or duplicate backup entries")
        # Validate the entire archive before writing anything.
        for name, digest in manifest["files"].items():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination) or target == destination:
                raise ValueError("Backup path escapes destination")
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Backup checksum mismatch")
        destination.mkdir(parents=True, exist_ok=True)
        for name in manifest["files"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))


def health():
    settings = Settings.load()
    url = os.getenv("DEEPDIVE_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/ready"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + settings.api_token})
    with urllib.request.urlopen(request, timeout=3) as response:
        if json.load(response)["status"] != "ready":
            raise RuntimeError("API is not ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("health")
    command = sub.add_parser("backup")
    command.add_argument("output")
    command.add_argument("--data", default=None)
    command = sub.add_parser("restore")
    command.add_argument("archive")
    command.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize()
        elif args.command == "health":
            health()
        elif args.command == "backup":
            backup(args.data or Settings.load().data, args.output)
            print("Backup created and checksummed.")
        else:
            restore(args.archive, args.destination)
            print("Restored into the empty destination. Configure DEEPDIVE_DATA_DIR before startup.")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

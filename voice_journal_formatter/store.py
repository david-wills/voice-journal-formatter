"""Filesystem and state handling.

The hazards this module exists to handle:

- Cloud-synced vaults (iCloud, Dropbox) materialize files incrementally, so a
  naive watcher reads half a transcript.
- A crash mid-write leaves a truncated note in the vault.
- Two overlapping runs process the same file twice.
- A permanently failing file retries forever.
- iCloud evicts files to save space; reading an evicted file from a background
  process fails with EDEADLK instead of downloading it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_LOCK_SECONDS = 6 * 60 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, content: str) -> None:
    """Write a file so readers never observe a partial note.

    Writes to a temp file in the same directory, then renames. Rename within a
    filesystem is atomic, so the note either exists complete or not at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(content.rstrip() + "\n")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def request_download(path: Path) -> None:
    """Ask iCloud to download an evicted file so a later run can read it.

    Best effort: `brctl` only exists on macOS, and a vault that isn't in iCloud
    never gets here.
    """
    brctl = shutil.which("brctl")
    if brctl is None:
        return
    subprocess.run([brctl, "download", str(path)], capture_output=True, check=False)


def is_stable(path: Path, min_age_seconds: int, settle_seconds: float = 1.0) -> bool:
    """Report whether a file looks fully written.

    Two independent checks: the file is older than `min_age_seconds`, and its
    size and mtime are unchanged across a short interval. Cheap, and enough to
    avoid reading a transcript mid-sync.
    """
    first = path.stat()
    if time.time() - first.st_mtime < min_age_seconds:
        return False
    time.sleep(settle_seconds)
    try:
        second = path.stat()
    except FileNotFoundError:
        return False
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


def move_without_overwrite(source: Path, destination: Path) -> Path:
    """Move a file, renaming on collision so nothing is ever clobbered."""
    from .naming import unique_path

    destination.parent.mkdir(parents=True, exist_ok=True)
    final = unique_path(destination)
    shutil.move(str(source), str(final))
    return final


class Lock:
    """A PID lockfile with stale-lock recovery.

    A scheduled run that outlives its interval would otherwise overlap with the
    next one. If a previous run was killed without cleaning up, the lock is
    reclaimed once it is older than STALE_LOCK_SECONDS.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> "Lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle_fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - self._path.stat().st_mtime
            if age <= STALE_LOCK_SECONDS:
                raise RuntimeError(
                    f"Another run appears to be active (lock: {self._path})"
                ) from None
            self._path.unlink(missing_ok=True)
            handle_fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n{now_iso()}\n")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._path.unlink(missing_ok=True)


class State:
    """Durable run state: the install marker and per-file failure counts."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._path = state_dir / "state.json"
        self._data = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tmp.replace(self._path)

    def install_marker(self) -> float:
        """Timestamp before which existing files are ignored.

        Without this, installing the tool on a vault with years of captures would
        process all of them at once on first run.
        """
        if "installed_at_epoch" not in self._data:
            self.reset_install_marker()
        return float(self._data["installed_at_epoch"])

    def reset_install_marker(self) -> None:
        self._data["installed_at_epoch"] = time.time()
        self._data["installed_at"] = now_iso()
        self.save()

    def attempts(self, filename: str) -> int:
        return int(self._data.get("failures", {}).get(filename, {}).get("attempts", 0))

    def record_failure(self, filename: str, error: str, max_chars: int) -> int:
        failures = self._data.setdefault("failures", {})
        entry = failures.setdefault(filename, {"attempts": 0})
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_error"] = error[-max_chars:]
        entry["last_attempt"] = now_iso()
        self.save()
        return int(entry["attempts"])

    def clear_failure(self, filename: str) -> None:
        failures = self._data.get("failures", {})
        if filename in failures:
            del failures[filename]
            self.save()

    def last_error(self, filename: str) -> str:
        return str(
            self._data.get("failures", {}).get(filename, {}).get("last_error", "")
        )

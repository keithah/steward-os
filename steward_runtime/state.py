"""Private runtime path and atomic JSON helpers."""

from __future__ import annotations

import errno
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """Locations for mutable Steward state outside the repository checkout."""

    root: Path
    scoreboard_json: Path
    scoreboard_markdown: Path
    judgment_json: Path
    label_ledger_jsonl: Path
    label_state_json: Path
    watchdog_state_json: Path
    locks: Path

    @classmethod
    def from_environment(cls) -> "RuntimePaths":
        root = Path.home() / ".hermes" / "steward-os"
        return cls(
            root=root,
            scoreboard_json=root / "scoreboard.json",
            scoreboard_markdown=root / "scoreboard.md",
            judgment_json=root / "judgment.json",
            label_ledger_jsonl=root / "label-ledger.jsonl",
            label_state_json=root / "label-state.json",
            watchdog_state_json=root / "watchdog-state.json",
            locks=root / "locks",
        )


def label_state_lock_path(state_path: Path) -> Path:
    """Return the lock shared by every whole-document label-state writer."""
    return state_path.parent / "locks" / "label-state.lock"


def _fsync_parent_directory(parent: Path) -> None:
    """Durably record a replacement in *parent* when directory fsync exists."""
    unsupported = {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as error:
        if error.errno in unsupported:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in unsupported:
                raise
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, value: object) -> None:
    """Atomically replace *path* with a JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as temporary:
        json.dump(value, temporary, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
    _fsync_parent_directory(path.parent)

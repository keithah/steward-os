#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class ReviewError(Exception):
    pass


_ORIGIN_PATTERNS = (
    re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)\.git"),
    re.compile(r"git@github\.com:([^/\s]+)/([^/\s]+)\.git"),
    re.compile(r"ssh://git@github\.com/([^/\s]+)/([^/\s]+)\.git"),
)


def _require_keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be an object")
    keys = set(value)
    if any(not isinstance(key, str) or not key.strip() for key in keys):
        raise ReviewError(f"{label} contains a blank key")
    unknown = keys - allowed
    if unknown:
        raise ReviewError(f"{label} contains unknown keys: {', '.join(sorted(unknown))}")
    missing = required - keys
    if missing:
        raise ReviewError(f"{label} is missing keys: {', '.join(sorted(missing))}")


def _require_nonblank_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} must be a non-blank string")


def _require_string_list(value, label):
    if not isinstance(value, list):
        raise ReviewError(f"{label} must be a list")
    for index, item in enumerate(value):
        _require_nonblank_string(item, f"{label}[{index}]")


def _is_inside(path, directory):
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _git(repo_dir: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _repository_name(origin: str) -> str:
    for pattern in _ORIGIN_PATTERNS:
        match = pattern.fullmatch(origin)
        if match:
            return "/".join(match.groups())
    raise ReviewError("origin URL must be a supported GitHub repository URL")


def _origin_repository(repo_dir: Path) -> str:
    try:
        return _repository_name(_git(repo_dir, "remote", "get-url", "origin"))
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"git command failed: {message}") from error


def load_config(path: Path, repo_dir: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot read configuration: {error}") from error

    _require_keys(config, {"repository", "paths", "review"}, {"repository", "paths", "review"}, "configuration")

    repository = config["repository"]
    _require_keys(repository, {"id", "base_ref"}, {"id", "base_ref"}, "repository")
    _require_nonblank_string(repository["id"], "repository.id")
    _require_nonblank_string(repository["base_ref"], "repository.base_ref")
    if repository["id"] != _origin_repository(repo_dir):
        raise ReviewError("repository.id must match Git origin")

    paths = config["paths"]
    _require_keys(paths, {"report_root", "manifest_root"}, {"report_root", "manifest_root"}, "paths")
    reviewed_checkout = repo_dir.resolve()
    roots = {}
    for name in ("report_root", "manifest_root"):
        _require_nonblank_string(paths[name], f"paths.{name}")
        root = Path(paths[name])
        if not root.is_absolute():
            raise ReviewError(f"paths.{name} must be absolute")
        resolved_root = root.resolve()
        if _is_inside(resolved_root, reviewed_checkout):
            raise ReviewError(f"paths.{name} must be outside reviewed checkout")
        roots[name] = resolved_root
    if roots["report_root"] == roots["manifest_root"]:
        raise ReviewError("report_root and manifest_root must be different")

    review = config["review"]
    review_keys = {
        "sensitive_paths",
        "visual_paths",
        "deep_paths",
        "execute_contributor_code",
        "sandbox_available",
        "commands",
    }
    _require_keys(review, review_keys, review_keys, "review")
    for name in ("sensitive_paths", "visual_paths", "deep_paths"):
        _require_string_list(review[name], f"review.{name}")
    for name in ("execute_contributor_code", "sandbox_available"):
        if not isinstance(review[name], bool):
            raise ReviewError(f"review.{name} must be a boolean")
    if not isinstance(review["commands"], list):
        raise ReviewError("review.commands must be a list")
    command_ids = set()
    for index, command in enumerate(review["commands"]):
        label = f"review.commands[{index}]"
        _require_keys(command, {"id", "command", "execution"}, {"id", "command", "execution"}, label)
        _require_nonblank_string(command["id"], f"{label}.id")
        _require_nonblank_string(command["command"], f"{label}.command")
        if command["id"] in command_ids:
            raise ReviewError("review.commands command ids must be unique")
        command_ids.add(command["id"])
        if command["execution"] not in {"safe", "sandbox", "disabled"}:
            raise ReviewError(f"{label}.execution must be safe, sandbox, or disabled")

    return config


def git_state(repo_dir: Path, base_ref: str) -> dict:
    try:
        if _git(repo_dir, "status", "--porcelain"):
            raise ReviewError("working tree is dirty")
        repository = _origin_repository(repo_dir)
        head_sha = _git(repo_dir, "rev-parse", "HEAD")
        base_sha = _git(repo_dir, "rev-parse", base_ref)
        merge_base_sha = _git(repo_dir, "merge-base", "HEAD", base_ref)
        changed_paths = _git(repo_dir, "diff", "--name-only", merge_base_sha, "HEAD").splitlines()
        return {
            "repository": repository,
            "branch": _git(repo_dir, "branch", "--show-current"),
            "head_sha": head_sha,
            "base_sha": base_sha,
            "merge_base_sha": merge_base_sha,
            "changed_paths": changed_paths,
        }
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"git command failed: {message}") from error


def _sanitize_branch(branch: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip(".-")
    if not sanitized:
        raise ReviewError("branch must contain a filename-safe character")
    return sanitized


def write_manifest(manifest_root: Path, manifest: dict) -> Path:
    owner, repository = manifest["repository"].split("/", 1)
    destination = (
        manifest_root
        / f"{owner}__{repository}"
        / f"branch-{_sanitize_branch(manifest['branch'])}"
        / f"{manifest['head_sha']}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as temporary_file:
            json.dump(manifest, temporary_file, sort_keys=True)
            temporary_file.write("\n")
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


def _config_revision(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        repo_dir = args.repo_dir.resolve()
        config = load_config(args.config, repo_dir)
        manifest = git_state(repo_dir, config["repository"]["base_ref"])
        manifest.update(
            {
                "base_ref": config["repository"]["base_ref"],
                "config_revision": _config_revision(config),
                "commands": [],
                "skipped_checks": [],
                "status": "ready",
            }
        )
        write_manifest(Path(config["paths"]["manifest_root"]), manifest)
    except ReviewError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

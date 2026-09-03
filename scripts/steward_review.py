#!/usr/bin/env python3
import argparse
import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


class ReviewError(Exception):
    pass


_ORIGIN_PATTERNS = (
    re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?"),
    re.compile(r"git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?"),
    re.compile(r"ssh://git@github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?"),
)
_CAPTURE_LIMIT = 16_384


def _require_keys(value, allowed, required, label):
    """Run a Steward review helper."""
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
    """Run a Steward review helper."""
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} must be a non-blank string")


def _require_string_list(value, label):
    """Run a Steward review helper."""
    if not isinstance(value, list):
        raise ReviewError(f"{label} must be a list")
    for index, item in enumerate(value):
        _require_nonblank_string(item, f"{label}[{index}]")


def _is_inside(path, directory):
    """Run a Steward review helper."""
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _git(repo_dir: Path, *args: str) -> str:
    """Run a Steward review helper."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _repository_name(origin: str) -> str:
    """Run a Steward review helper."""
    for pattern in _ORIGIN_PATTERNS:
        match = pattern.fullmatch(origin)
        if match:
            return "/".join(match.groups())
    raise ReviewError("origin URL must be a supported GitHub repository URL")


def _origin_repository(repo_dir: Path) -> str:
    """Run a Steward review helper."""
    try:
        return _repository_name(_git(repo_dir, "remote", "get-url", "origin"))
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"git command failed: {message}") from error


def resolve_config_path(config_dir: Path, repo_dir: Path) -> Path:
    """Run a Steward review helper."""
    repository = _origin_repository(repo_dir)
    owner, name = repository.split("/", 1)
    path = config_dir / f"{owner}__{name}.json"
    if not path.is_file():
        raise ReviewError(f"configuration file is missing: {path}")
    return path


def load_config(path: Path, repo_dir: Path) -> dict:
    """Run a Steward review helper."""
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
        "command_timeout_seconds",
        "safe_commands_execute_reviewed_code",
        "commands",
    }
    _require_keys(review, review_keys, review_keys, "review")
    for name in ("sensitive_paths", "visual_paths", "deep_paths"):
        _require_string_list(review[name], f"review.{name}")
    for name in (
        "execute_contributor_code",
        "sandbox_available",
        "safe_commands_execute_reviewed_code",
    ):
        if not isinstance(review[name], bool):
            raise ReviewError(f"review.{name} must be a boolean")
    timeout = review["command_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3_600:
        raise ReviewError("review.command_timeout_seconds must be an integer from 1 to 3600")
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
        if (
            command["execution"] == "sandbox"
            and review["execute_contributor_code"]
            and review["sandbox_available"]
        ):
            raise ReviewError("sandbox runtime is not integrated")
        if command["execution"] == "safe" and review["safe_commands_execute_reviewed_code"]:
            raise ReviewError("safe commands that execute reviewed code require a sandbox runtime")

    return config


def git_state(repo_dir: Path, base_ref: str) -> dict:
    """Run a Steward review helper."""
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


def post_command_state(repo_dir: Path, initial_state: dict) -> dict:
    """Run a Steward review helper."""
    try:
        reasons = []
        if _git(repo_dir, "rev-parse", "HEAD") != initial_state["head_sha"]:
            reasons.append("HEAD changed after quality commands")
        if _git(repo_dir, "rev-parse", initial_state["base_ref"]) != initial_state["base_sha"]:
            reasons.append("base ref changed after quality commands")
        if _git(repo_dir, "merge-base", "HEAD", initial_state["base_ref"]) != initial_state["merge_base_sha"]:
            reasons.append("merge base changed after quality commands")
        if _git(repo_dir, "status", "--porcelain"):
            reasons.append("working tree changed after quality commands")
        if reasons:
            return {"status": "failed", "reason": "; ".join(reasons)}
        return {"status": "passed", "reason": ""}
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        return {"status": "failed", "reason": f"git revalidation failed: {message}"}


def select_lane(changed_paths: list[str], review: dict) -> str:
    """Run a Steward review helper."""
    def matches(patterns: list[str]) -> bool:
        """Run a Steward review helper."""
        return any(
            fnmatch.fnmatchcase(path, pattern)
            for path in changed_paths
            for pattern in patterns
        )

    if matches(review["visual_paths"]):
        return "visual"
    if matches(review["deep_paths"]) or matches(review["sensitive_paths"]):
        return "deep"
    return "fast"


def _bounded_text(value: str) -> tuple[str, bool]:
    """Return a UTF-8 byte-bounded string and whether it was truncated."""
    encoded = value.encode("utf-8")
    if len(encoded) <= _CAPTURE_LIMIT:
        return value, False
    return encoded[:_CAPTURE_LIMIT].decode("utf-8", errors="ignore"), True


def _append_bounded_text(value: str, suffix: str) -> tuple[str, bool]:
    """Append a diagnostic while preserving the UTF-8 evidence byte cap."""
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) >= _CAPTURE_LIMIT:
        return _bounded_text(suffix)
    prefix_limit = _CAPTURE_LIMIT - len(suffix_bytes)
    prefix = value.encode("utf-8")[:prefix_limit].decode("utf-8", errors="ignore")
    combined = prefix + suffix
    return combined, len(prefix.encode("utf-8")) < len(value.encode("utf-8"))


def _read_capped_output(handle) -> tuple[str, bool]:
    """Read bounded UTF-8 output from a temporary command-output file."""
    handle.seek(0, os.SEEK_END)
    observed_bytes = handle.tell()
    handle.seek(0)
    return handle.read(_CAPTURE_LIMIT).decode("utf-8", errors="ignore"), observed_bytes > _CAPTURE_LIMIT


def _terminate_process_group(process: subprocess.Popen, signal_number: int) -> None:
    """Run a Steward review helper."""
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        pass


def _run_command(repo_dir: Path, command: str, timeout_seconds: int) -> dict:
    """Run a Steward review helper."""
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=repo_dir,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.communicate(timeout=timeout_seconds)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process, signal.SIGTERM)
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process, signal.SIGKILL)
                process.communicate()
            exit_code = None
        stdout, stdout_truncated = _read_capped_output(stdout_file)
        stderr, stderr_truncated = _read_capped_output(stderr_file)
    if timed_out:
        stderr, timeout_truncated = _append_bounded_text(
            stderr, f"\ncommand timed out after {timeout_seconds} seconds"
        )
        stderr_truncated = stderr_truncated or timeout_truncated
    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
    }


def run_commands(repo_dir: Path, review: dict) -> tuple[list[dict], list[dict]]:
    """Run a Steward review helper."""
    commands = []
    skipped_checks = []
    for configured in review["commands"]:
        execution = configured["execution"]
        if execution == "disabled":
            skipped_checks.append(
                {"id": configured["id"], "reason": "disabled by configuration"}
            )
            continue
        if execution == "sandbox" and not (
            review["execute_contributor_code"] and review["sandbox_available"]
        ):
            skipped_checks.append(
                {"id": configured["id"], "reason": "sandbox execution unavailable"}
            )
            continue
        evidence = _run_command(
            repo_dir, configured["command"], review["command_timeout_seconds"]
        )
        commands.append(
            {
                "id": configured["id"],
                "execution": execution,
                "status": "passed" if evidence["exit_code"] == 0 else "failed",
                **evidence,
            }
        )
    return commands, skipped_checks


def _sanitize_branch(branch: str) -> str:
    """Run a Steward review helper."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip(".-")
    if not sanitized:
        raise ReviewError("branch must contain a filename-safe character")
    return sanitized


def write_manifest(manifest_root: Path, manifest: dict) -> Path:
    """Run a Steward review helper."""
    owner, repository = manifest["repository"].split("/", 1)
    branch_segment = _sanitize_branch(manifest["branch"]) if manifest["branch"] else manifest["head_sha"]
    destination = (
        manifest_root
        / f"{owner}__{repository}"
        / f"branch-{branch_segment}"
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
    """Run a Steward review helper."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    """Run a Steward review helper."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True, type=Path)
    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument("--config", type=Path)
    config_group.add_argument("--config-dir", type=Path)
    args = parser.parse_args()
    try:
        repo_dir = args.repo_dir.resolve()
        config_path = (args.config or resolve_config_path(args.config_dir, repo_dir)).resolve()
        if _is_inside(config_path, repo_dir):
            raise ReviewError("configuration path must be outside the reviewed checkout")
        config = load_config(config_path, repo_dir)
        manifest = git_state(repo_dir, config["repository"]["base_ref"])
        manifest.update(
            {
                "base_ref": config["repository"]["base_ref"],
                "config_revision": _config_revision(config),
                "status": "ready",
                "lane": select_lane(manifest["changed_paths"], config["review"]),
            }
        )
        manifest["commands"], manifest["skipped_checks"] = run_commands(
            repo_dir, config["review"]
        )
        manifest["post_command_state"] = post_command_state(repo_dir, manifest)
        if (
            any(command["status"] == "failed" for command in manifest["commands"])
            or manifest["post_command_state"]["status"] == "failed"
        ):
            manifest["status"] = "blocked"
        manifest_path = write_manifest(Path(config["paths"]["manifest_root"]), manifest)
        print(manifest_path)
        if manifest["status"] == "blocked":
            return 1
    except ReviewError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

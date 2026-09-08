#!/usr/bin/env python3
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


class ReviewError(Exception):
    pass


_ROLES = ("primary", "adversarial")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_PROMPT_DIFF_BYTES = 256 * 1024
_ARTIFACT_KEYS = {
    "repository",
    "head_sha",
    "base_sha",
    "merge_base_sha",
    "config_revision",
    "role",
    "provider",
    "model",
    "status",
    "findings",
    "probes",
    "limitations",
}


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _require_string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} missing or invalid")
    return value


def _require_hash(value, label: str, pattern: re.Pattern) -> str:
    value = _require_string(value, label)
    if not pattern.fullmatch(value):
        raise ReviewError(f"{label} invalid")
    return value


def _sanitize_branch(branch: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip(".-")
    if not segment:
        raise ReviewError("branch invalid")
    return segment


def load_context(repo_dir: Path, manifest_path: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot read manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise ReviewError("manifest must be an object")
    if manifest.get("status") != "ready":
        raise ReviewError("manifest must be ready")

    repository = _require_string(manifest.get("repository"), "repository")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise ReviewError("repository invalid")
    branch = _require_string(manifest.get("branch"), "branch")
    report_root_value = manifest.get("report_root")
    if report_root_value is None:
        raise ReviewError("report_root missing")
    report_root = Path(_require_string(report_root_value, "report_root"))
    if not report_root.is_absolute():
        raise ReviewError("report_root must be absolute")
    report_root = report_root.resolve()
    if _inside(report_root, repo_dir):
        raise ReviewError("report_root must be outside reviewed checkout")

    reviewers = manifest.get("required_reviewers")
    if not isinstance(reviewers, dict) or set(reviewers) != set(_ROLES):
        raise ReviewError("required_reviewers invalid")
    for role in _ROLES:
        reviewer = reviewers[role]
        if not isinstance(reviewer, dict) or set(reviewer) != {"provider", "model"}:
            raise ReviewError(f"{role} reviewer contract invalid")
        _require_string(reviewer["provider"], f"{role} provider")
        _require_string(reviewer["model"], f"{role} model")

    return {
        "repo_dir": repo_dir,
        "repository": repository,
        "branch": branch,
        "head_sha": _require_hash(manifest.get("head_sha"), "head_sha", _SHA_PATTERN),
        "base_sha": _require_hash(manifest.get("base_sha"), "base_sha", _SHA_PATTERN),
        "merge_base_sha": _require_hash(
            manifest.get("merge_base_sha"), "merge_base_sha", _SHA_PATTERN
        ),
        "config_revision": _require_hash(
            manifest.get("config_revision"), "config_revision", _REVISION_PATTERN
        ),
        "report_root": report_root,
        "reviewers": reviewers,
    }


def _prompt(role: str, context: dict) -> str:
    bindings = {
        key: context[key]
        for key in (
            "repository",
            "branch",
            "head_sha",
            "base_sha",
            "merge_base_sha",
            "config_revision",
        )
    }
    bindings["role"] = role
    return json.dumps(
        {
            "instructions": (
                "Perform a read-only review using the supplied committed diff only. Do not "
                "execute reviewed code or repository tests. Make no GitHub writes and do not "
                "create, alter, or delete any GitHub object. Do not invoke network write APIs. "
                "Emit exactly one JSON object and no markdown or prose. Its exact keys must be: "
                "repository, head_sha, base_sha, merge_base_sha, config_revision, role, provider, "
                "model, status, findings, probes, limitations. The provider and model must be the "
                "requested values. Bind the object to the supplied bindings."
            ),
            "bindings": bindings,
            "diff": context["diff"],
        },
        sort_keys=True,
    )


def committed_diff(context: dict) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=80",
                f"{context['base_sha']}...{context['head_sha']}",
            ],
            cwd=context["repo_dir"],
            text=True,
            capture_output=True,
        )
    except OSError as error:
        raise ReviewError(f"cannot generate committed diff: {error}") from error
    if completed.returncode:
        raise ReviewError("cannot generate committed diff")
    if len(completed.stdout.encode("utf-8")) > _MAX_PROMPT_DIFF_BYTES:
        raise ReviewError("diff exceeds prompt limit")
    return completed.stdout


def ensure_private_report_root(report_root: Path) -> None:
    try:
        report_root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise ReviewError(f"cannot create report_root: {error}") from error
    try:
        root_stat = report_root.stat()
    except OSError as error:
        raise ReviewError(f"cannot inspect report_root: {error}") from error
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) & 0o077
    ):
        raise ReviewError("report_root must be owner-private")


def parse_only_json(output: str, role: str) -> dict:
    if not output.strip():
        raise ReviewError(f"{role} artifact missing")
    try:
        artifact = json.loads(output)
    except json.JSONDecodeError as error:
        raise ReviewError(f"{role} reviewer must emit exactly one JSON object") from error
    if not isinstance(artifact, dict):
        raise ReviewError(f"{role} reviewer must emit exactly one JSON object")
    return artifact


def validate_artifact(artifact: dict, role: str, reviewer: dict, context: dict) -> None:
    if set(artifact) != _ARTIFACT_KEYS:
        raise ReviewError(f"{role} artifact schema mismatch")
    for key in ("repository", "head_sha", "base_sha", "merge_base_sha", "config_revision"):
        if artifact[key] != context[key]:
            raise ReviewError(f"{key} mismatch")
    if artifact["role"] != role:
        raise ReviewError("role mismatch")
    if artifact["provider"] != reviewer["provider"]:
        raise ReviewError("provider mismatch")
    if artifact["model"] != reviewer["model"]:
        raise ReviewError("model mismatch")
    _require_string(artifact["status"], "artifact status")
    for key in ("findings", "probes", "limitations"):
        if not isinstance(artifact[key], list):
            raise ReviewError(f"{key} must be a list")


def run_reviewer(role: str, reviewer: dict, context: dict, hermes_bin: str) -> dict:
    prompt_dir = Path(tempfile.mkdtemp(prefix="steward-llm-review-"))
    os.chmod(prompt_dir, 0o700)
    prompt_path = prompt_dir / "review-prompt.json"
    try:
        prompt_path.write_text(_prompt(role, context))
        os.chmod(prompt_path, 0o600)
        command = [
            hermes_bin,
            "chat",
            "--safe-mode",
            "--toolsets",
            ",",
            "--max-turns",
            "1",
            "--provider",
            reviewer["provider"],
            "--model",
            reviewer["model"],
            "--reasoning",
            "high",
            "--quiet",
            "--oneshot",
            "--query-file",
            str(prompt_path),
        ]
        completed = subprocess.run(
            command,
            cwd=prompt_dir,
            text=True,
            capture_output=True,
        )
        if completed.returncode:
            raise ReviewError(f"{role} reviewer failed")
        artifact = parse_only_json(completed.stdout, role)
        validate_artifact(artifact, role, reviewer, context)
        return artifact
    except OSError as error:
        raise ReviewError(f"{role} reviewer failed: {error}") from error
    finally:
        prompt_path.unlink(missing_ok=True)
        prompt_dir.rmdir()


def _artifact_path(context: dict, role: str) -> Path:
    owner, repository = context["repository"].split("/", 1)
    return (
        context["report_root"]
        / f"{owner}__{repository}"
        / f"branch-{_sanitize_branch(context['branch'])}"
        / context["head_sha"]
        / f"{role}.json"
    )


def persist_artifacts(context: dict, artifacts: dict) -> list[Path]:
    destinations = {role: _artifact_path(context, role) for role in _ROLES}
    for destination in destinations.values():
        destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    try:
        for role, destination in destinations.items():
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent, prefix=f".{role}.", suffix=".tmp"
            )
            temporary_path = Path(temporary_name)
            temporary_paths.append(temporary_path)
            with os.fdopen(descriptor, "w") as temporary_file:
                os.fchmod(descriptor, 0o600)
                json.dump(artifacts[role], temporary_file, sort_keys=True)
                temporary_file.write("\n")
            temporary_path.replace(destination)
        return list(destinations.values())
    except OSError as error:
        raise ReviewError(f"cannot persist artifacts: {error}") from error
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--hermes-bin", default="hermes")
    args = parser.parse_args()
    try:
        repo_dir = args.repo_dir.resolve()
        if not repo_dir.is_dir():
            raise ReviewError("repo-dir must be a directory")
        context = load_context(repo_dir, args.manifest.resolve())
        ensure_private_report_root(context["report_root"])
        context["diff"] = committed_diff(context)
        artifacts = {
            role: run_reviewer(role, context["reviewers"][role], context, args.hermes_bin)
            for role in _ROLES
        }
        for role in _ROLES:
            if role not in artifacts:
                raise ReviewError(f"{role} artifact missing")
        for path in persist_artifacts(context, artifacts):
            print(path)
    except ReviewError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

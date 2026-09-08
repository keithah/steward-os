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
_INSTRUCTION_FILE_NAMES = ("AGENTS.md", "SOUL.md", ".cursorrules", ".hermes.md", "CLAUDE.md")
_INSTRUCTION_DIFF_EXCLUSIONS = tuple(
    pathspec
    for name in _INSTRUCTION_FILE_NAMES
    for pathspec in (f":(exclude,literal){name}", f":(exclude,glob)**/{name}")
)
_TIRITH_UNAVAILABLE_DIAGNOSTIC = (
    "  ⚠ tirith security scanner enabled but not available "
    "— command scanning will use pattern matching only\n"
)
_ROLE_PROBES = {
    "primary": (
        ("primary.changed-file-callers-tests", "full changed-file/caller/test inspection"),
        ("primary.public-contract-compatibility", "public contract and compatibility paths"),
        (
            "primary.malformed-omitted-negative-timezone-inputs",
            "malformed/omitted/negative/timezone inputs",
        ),
        ("primary.error-propagation", "error propagation"),
    ),
    "adversarial": (
        ("adversarial.policy-effectful-sinks", "policy at effectful sinks"),
        (
            "adversarial.token-output-redirect-boundaries",
            "token/output/redirect boundaries",
        ),
        (
            "adversarial.cancellation-partial-success-compensation",
            "cancellation and partial-success compensation",
        ),
        ("adversarial.writers-shared-locks", "all writers and shared locks"),
        (
            "adversarial.pagination-snapshots-changed-totals",
            "pagination snapshots and changed totals",
        ),
        ("adversarial.ordering-deduplication", "ordering/deduplication"),
        (
            "adversarial.ambient-credentials-caller-authorization",
            "ambient credentials and caller-widenable authorization",
        ),
        (
            "adversarial.process-cleanup-status-propagation",
            "process cleanup and status propagation",
        ),
    ),
}
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


def _require_normalized_string(value, label: str) -> str:
    value = _require_string(value, label)
    if value != value.strip():
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


def _git(repo_dir: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


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
    head_sha = _require_hash(manifest.get("head_sha"), "head_sha", _SHA_PATTERN)
    branch_value = manifest.get("branch")
    branch = head_sha if branch_value == "" else _require_string(branch_value, "branch")
    base_ref = _require_string(manifest.get("base_ref"), "base_ref")
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
        "manifest_branch": branch_value,
        "base_ref": base_ref,
        "head_sha": head_sha,
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


def revalidate_context(context: dict) -> None:
    """Fail closed unless the checkout still matches all manifest Git bindings."""
    try:
        if _git(context["repo_dir"], "rev-parse", "HEAD") != context["head_sha"]:
            raise ReviewError("HEAD does not match manifest")
        if _git(context["repo_dir"], "rev-parse", context["base_ref"]) != context["base_sha"]:
            raise ReviewError("base ref does not match manifest")
        if (
            _git(context["repo_dir"], "merge-base", "HEAD", context["base_ref"])
            != context["merge_base_sha"]
        ):
            raise ReviewError("merge base does not match manifest")
        current_branch = _git(context["repo_dir"], "branch", "--show-current")
        if context["manifest_branch"] == "":
            if current_branch:
                raise ReviewError("checkout branch does not match detached manifest")
        elif current_branch != context["manifest_branch"]:
            raise ReviewError("checkout branch does not match manifest")
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"git revalidation failed: {message}") from error


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
                "requested values. Bind the object to the supplied bindings. Every finding must be "
                "an object with a nonblank normalized string probe_id. Every probes entry must be "
                "an object with exactly probe_id, status, and evidence fields, each a nonblank "
                "normalized string; status must be passed, not-applicable, or finding. If findings "
                "is empty, probes must record exactly one outcome for every checklist ID. Review "
                "every role-specific checklist item, using its stable ID: "
                + "; ".join(
                    f"{probe_id}: {description}" for probe_id, description in _ROLE_PROBES[role]
                )
                + "."
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
                "--",
                *_INSTRUCTION_DIFF_EXCLUSIONS,
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
    if output.startswith(_TIRITH_UNAVAILABLE_DIAGNOSTIC):
        output = output.removeprefix(_TIRITH_UNAVAILABLE_DIAGNOSTIC)
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
    valid_probe_ids = {probe_id for probe_id, _ in _ROLE_PROBES[role]}
    for finding in artifact["findings"]:
        if not isinstance(finding, dict):
            raise ReviewError("finding must be an object")
        finding_probe_id = _require_normalized_string(finding.get("probe_id"), "finding probe_id")
        if finding_probe_id not in valid_probe_ids:
            raise ReviewError("finding probe_id invalid")
    probe_ids = set()
    for probe in artifact["probes"]:
        if not isinstance(probe, dict) or set(probe) != {"probe_id", "status", "evidence"}:
            raise ReviewError("probe schema mismatch")
        probe_id = _require_normalized_string(probe["probe_id"], "probe probe_id")
        if probe_id not in valid_probe_ids:
            raise ReviewError("probe probe_id invalid")
        if probe_id in probe_ids:
            raise ReviewError("probe IDs must be unique")
        probe_ids.add(probe_id)
        status = _require_normalized_string(probe["status"], "probe status")
        if status not in {"passed", "not-applicable", "finding"}:
            raise ReviewError("probe status invalid")
        _require_normalized_string(probe["evidence"], "probe evidence")
    if not artifact["findings"] and probe_ids != valid_probe_ids:
        raise ReviewError("complete role probes coverage required when findings is empty")


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
            "context_engine",
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
        revalidate_context(context)
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

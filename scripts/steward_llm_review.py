#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import steward_review


class ReviewError(Exception):
    pass


class ReviewerExecutionError(ReviewError):
    """A candidate could not complete its reviewer process."""


_LANE_ROLES = {
    "fast": ("primary",),
    "deep": ("primary", "adversarial"),
    "visual": ("primary", "adversarial"),
}
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ORIGIN_PATTERNS = (
    re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?"),
    re.compile(r"git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?"),
    re.compile(r"ssh://git@github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?"),
)
_MAX_PROMPT_DIFF_BYTES = 256 * 1024
_MAX_REVIEWER_STDOUT_BYTES = 64 * 1024
_MAX_REVIEWER_STDERR_BYTES = 1024
_MAX_PROBE_EVIDENCE_BYTES = 8 * 1024
_MAX_LIMITATION_BYTES = 2 * 1024
_REVIEWER_TIMEOUT_SECONDS = 300
_REVIEWER_TERMINATION_GRACE_SECONDS = 1
_REVIEWER_DRAIN_TIMEOUT_SECONDS = 1
_MAX_FINDINGS = 64
_MAX_LIMITATIONS = 32
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


def _origin_repository(repo_dir: Path) -> str:
    try:
        origin = _git(repo_dir, "remote", "get-url", "origin")
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"git origin lookup failed: {message}") from error
    for pattern in _ORIGIN_PATTERNS:
        match = pattern.fullmatch(origin)
        if match:
            return "/".join(match.groups())
    raise ReviewError("origin URL must be a supported GitHub repository URL")


def _policy_context(repo_dir: Path) -> dict:
    """Derive launcher bindings solely from the active owner-private policy."""
    if os.environ.get("STEWARD_POLICY_ROOT") is None:
        raise ReviewError("STEWARD_POLICY_ROOT is required")
    config = steward_review.load_global_policy(repo_dir)
    if config is None:
        raise ReviewError("STEWARD_POLICY_ROOT is required")
    try:
        repository = _origin_repository(repo_dir)
        head_sha = _git(repo_dir, "rev-parse", "HEAD")
        branch = _git(repo_dir, "branch", "--show-current")
        base_ref = config["repository"]["base_ref"]
        merge_base_sha = _git(repo_dir, "merge-base", "HEAD", base_ref)
        changed_paths = _git(repo_dir, "diff", "--name-only", merge_base_sha, "HEAD").splitlines()
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ReviewError(f"cannot derive policy review state: {message}") from error
    lane = steward_review.select_lane(changed_paths, config["review"])
    report_root = Path(config["paths"]["report_root"])
    manifest_root = Path(config["paths"]["manifest_root"])
    steward_review.validate_lexical_path(report_root, "report_root")
    steward_review.validate_lexical_path(manifest_root, "manifest_root")
    return {
        "repository": repository,
        "head_sha": head_sha,
        "branch": branch,
        "base_ref": base_ref,
        "config_revision": steward_review._config_revision(config),
        "report_root": report_root.resolve(),
        "lane": lane,
        "reviewers": steward_review.required_reviewer_contracts(lane, config["review"]),
        "manifest_path": steward_review.manifest_path(
            manifest_root.resolve(), repository, branch, head_sha
        ).resolve(),
    }


def load_context(repo_dir: Path, manifest_path: Path, policy: dict) -> dict:
    if manifest_path != policy["manifest_path"]:
        raise ReviewError("manifest path does not match active global policy")
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
    steward_review.validate_lexical_path(report_root, "report_root")
    report_root = report_root.resolve()
    if _inside(report_root, repo_dir):
        raise ReviewError("report_root must be outside reviewed checkout")

    lane = manifest.get("lane")
    if not isinstance(lane, str) or lane not in _LANE_ROLES:
        raise ReviewError("lane invalid")
    roles = _LANE_ROLES[lane]
    reviewers = manifest.get("required_reviewers")
    expected_keys = {"primary"} if lane == "fast" else {"primary", "adversarial_candidates"}
    if not isinstance(reviewers, dict) or set(reviewers) != expected_keys:
        raise ReviewError("required_reviewers invalid")
    primary = reviewers["primary"]
    if not isinstance(primary, dict) or set(primary) != {"provider", "model"}:
        raise ReviewError("primary reviewer contract invalid")
    _require_string(primary["provider"], "primary provider")
    _require_string(primary["model"], "primary model")
    if lane != "fast":
        candidates = reviewers["adversarial_candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise ReviewError("adversarial candidates invalid")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict) or set(candidate) != {"provider", "model"}:
                raise ReviewError("secondary candidate contract invalid")
            _require_string(candidate["provider"], f"secondary candidate {index} provider")
            _require_string(candidate["model"], f"secondary candidate {index} model")

    if repository != policy["repository"]:
        raise ReviewError("repository does not match active global policy")
    if head_sha != policy["head_sha"] or branch_value != policy["branch"]:
        raise ReviewError("manifest Git state does not match active global policy")
    if base_ref != policy["base_ref"]:
        raise ReviewError("base_ref does not match active global policy")
    if report_root != policy["report_root"]:
        raise ReviewError("report_root does not match active global policy")
    if lane != policy["lane"]:
        raise ReviewError("lane does not match active global policy")
    if reviewers != policy["reviewers"]:
        raise ReviewError("reviewer contracts do not match active global policy")
    if manifest.get("config_revision") != policy["config_revision"]:
        raise ReviewError("config_revision does not match active global policy")

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
        "lane": lane,
        "roles": roles,
        "reviewers": reviewers,
    }


def revalidate_context(context: dict) -> None:
    """Fail closed unless the checkout still matches all manifest Git bindings."""
    try:
        if _origin_repository(context["repo_dir"]) != context["repository"]:
            raise ReviewError("origin repository does not match manifest")
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


def _prompt(role: str, reviewer: dict, context: dict) -> str:
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
    bindings["provider"] = reviewer["provider"]
    bindings["model"] = reviewer["model"]
    return json.dumps(
        {
            "instructions": (
                "Perform a read-only review using the supplied committed diff only. Do not "
                "execute reviewed code or repository tests. Make no GitHub writes and do not "
                "create, alter, or delete any GitHub object. Do not invoke network write APIs. "
                "Emit exactly one JSON object and no markdown or prose. Its exact keys must be: "
                "repository, head_sha, base_sha, merge_base_sha, config_revision, role, provider, "
                "model, status, findings, probes, limitations. The provider and model must be the "
                "requested values in the supplied bindings. Bind the object to every supplied "
                "binding. Status must be exactly complete. Findings must contain exactly the key "
                "probe_id with a nonblank normalized string value. Every probes entry must be an "
                "object with exactly probe_id, status, and evidence fields, each a nonblank "
                "normalized string within their byte bounds; status must be passed, not-applicable, "
                "or finding. Limitations must be normalized strings within their byte bounds. "
                "Unconditionally record exactly one outcome for every checklist ID. Review "
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
    steward_review.secure_directory_chain(report_root, "report_root")


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
    if artifact["status"] != "complete":
        raise ReviewError("artifact status must be complete")
    for key in ("findings", "probes", "limitations"):
        if not isinstance(artifact[key], list):
            raise ReviewError(f"{key} must be a list")
    if len(artifact["findings"]) > _MAX_FINDINGS:
        raise ReviewError("findings exceeds limit")
    if len(artifact["probes"]) > len(_ROLE_PROBES[role]):
        raise ReviewError("probes exceeds limit")
    if len(artifact["limitations"]) > _MAX_LIMITATIONS:
        raise ReviewError("limitations exceeds limit")
    for limitation in artifact["limitations"]:
        _require_normalized_string(limitation, "limitation")
        if len(limitation.encode("utf-8")) > _MAX_LIMITATION_BYTES:
            raise ReviewError("limitation exceeds limit")
    valid_probe_ids = {probe_id for probe_id, _ in _ROLE_PROBES[role]}
    for finding in artifact["findings"]:
        if not isinstance(finding, dict) or set(finding) != {"probe_id"}:
            raise ReviewError("finding schema mismatch")
        finding_probe_id = _require_normalized_string(finding["probe_id"], "finding probe_id")
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
        evidence = _require_normalized_string(probe["evidence"], "probe evidence")
        if len(evidence.encode("utf-8")) > _MAX_PROBE_EVIDENCE_BYTES:
            raise ReviewError("probe evidence exceeds limit")
    if probe_ids != valid_probe_ids:
        raise ReviewError("complete role probes coverage required")
    finding_probe_ids = {
        _require_normalized_string(finding["probe_id"], "finding probe_id")
        for finding in artifact["findings"]
    }
    finding_probe_outcomes = {
        probe["probe_id"] for probe in artifact["probes"] if probe["status"] == "finding"
    }
    if finding_probe_ids != finding_probe_outcomes:
        raise ReviewError("findings must exactly match finding probe outcomes")


def _reviewer_environment() -> dict[str, str]:
    """Pass only Hermes runtime locations and locale, never ambient credentials."""
    allowed = (
        "PATH", "HOME", "TMPDIR", "HERMES_HOME", "HERMES_PROFILE", "XDG_CONFIG_HOME",
        "XDG_DATA_HOME", "XDG_CACHE_HOME", "LANG", "LC_ALL", "LC_CTYPE",
    )
    return {name: os.environ[name] for name in allowed if os.environ.get(name)}


def _terminate_process_group(process: subprocess.Popen, signal_number: int) -> None:
    """Signal the isolated reviewer process group if it still exists."""
    try:
        os.killpg(process.pid, signal_number)
    except (PermissionError, ProcessLookupError):
        pass


def _stop_reviewer_process_group(process: subprocess.Popen) -> None:
    """Escalate group termination without depending on leader liveness."""
    _terminate_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=_REVIEWER_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    _terminate_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=_REVIEWER_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _close_reviewer_streams(process: subprocess.Popen) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _join_reviewer_drains(stdout_thread: threading.Thread, stderr_thread: threading.Thread) -> bool:
    """Wait briefly for pipe drains and report whether both completed."""
    deadline = time.monotonic() + _REVIEWER_DRAIN_TIMEOUT_SECONDS
    for thread in (stdout_thread, stderr_thread):
        thread.join(timeout=max(0, deadline - time.monotonic()))
    return not stdout_thread.is_alive() and not stderr_thread.is_alive()


def _drain_reviewer_stream(stream, limit: int, result: dict) -> None:
    """Drain a reviewer pipe while retaining no more than limit bytes."""
    captured = bytearray()
    overflow = False
    while chunk := stream.read(8_192):
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        overflow = overflow or len(chunk) > remaining
    result["output"] = bytes(captured).decode("utf-8", errors="replace")
    result["overflow"] = overflow


def _run_reviewer_process(command: list[str], prompt_dir: Path, role: str) -> tuple[str, str]:
    """Run an isolated reviewer with bounded live pipe draining and group cleanup."""
    try:
        process = subprocess.Popen(
            command,
            cwd=prompt_dir,
            env=_reviewer_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise ReviewerExecutionError(f"{role} reviewer failed: {error}") from error
    stdout_result = {}
    stderr_result = {}
    stdout_thread = threading.Thread(
        target=_drain_reviewer_stream,
        args=(process.stdout, _MAX_REVIEWER_STDOUT_BYTES, stdout_result),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_reviewer_stream,
        args=(process.stderr, _MAX_REVIEWER_STDERR_BYTES, stderr_result),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        process.wait(timeout=_REVIEWER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_reviewer_process_group(process)
    if timed_out:
        _close_reviewer_streams(process)
        _join_reviewer_drains(stdout_thread, stderr_thread)
        raise ReviewerExecutionError(f"{role} reviewer timed out")
    if not _join_reviewer_drains(stdout_thread, stderr_thread):
        _stop_reviewer_process_group(process)
        _close_reviewer_streams(process)
        _join_reviewer_drains(stdout_thread, stderr_thread)
        raise ReviewerExecutionError(f"{role} reviewer pipe drain timed out")
    _close_reviewer_streams(process)
    if stdout_result["overflow"]:
        raise ReviewError(f"{role} reviewer stdout exceeds limit")
    if process.returncode:
        diagnostic = stderr_result["output"].strip()
        if diagnostic:
            raise ReviewerExecutionError(f"{role} reviewer failed: {diagnostic}")
        raise ReviewerExecutionError(f"{role} reviewer failed")
    return stdout_result["output"], stderr_result["output"]


def run_reviewer(role: str, reviewer: dict, context: dict, hermes_bin: str) -> dict:
    try:
        prompt_dir = Path(tempfile.mkdtemp(prefix="steward-llm-review-"))
        os.chmod(prompt_dir, 0o700)
    except OSError as error:
        raise ReviewError(f"{role} reviewer prompt setup failed: {error}") from error
    prompt_path = prompt_dir / "review-prompt.json"
    try:
        try:
            prompt_path.write_text(_prompt(role, reviewer, context))
            os.chmod(prompt_path, 0o600)
        except OSError as error:
            raise ReviewError(f"{role} reviewer prompt setup failed: {error}") from error
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
        stdout, _stderr = _run_reviewer_process(command, prompt_dir, role)
        artifact = parse_only_json(stdout, role)
        validate_artifact(artifact, role, reviewer, context)
        return artifact
    finally:
        pending_exception = sys.exc_info()[0]
        try:
            shutil.rmtree(prompt_dir)
        except OSError as error:
            if pending_exception is None:
                raise ReviewError(f"{role} reviewer cleanup failed: {error}") from error


def run_secondary_reviewer(context: dict, hermes_bin: str) -> dict:
    """Use the first ordered secondary candidate that returns a valid artifact."""
    failures = []
    for candidate in context["reviewers"]["adversarial_candidates"]:
        revalidate_context(context)
        try:
            return run_reviewer("adversarial", candidate, context, hermes_bin)
        except ReviewerExecutionError as error:
            failures.append(str(error))
            revalidate_context(context)
    raise ReviewError("all secondary reviewer candidates failed: " + "; ".join(failures))


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
    roles = context.get("roles", tuple(artifacts))
    destination_directory = _artifact_path(context, roles[0]).parent
    destination_parent = destination_directory.parent
    staging_directory = None
    try:
        steward_review.secure_directory_chain(destination_parent, "artifact destination")
        if destination_directory.exists():
            raise ReviewError("exact-SHA artifact directory already exists")
        staging_directory = Path(
            tempfile.mkdtemp(prefix=f".{context['head_sha']}.", dir=destination_parent)
        )
        os.chmod(staging_directory, 0o700)
        for role in roles:
            artifact_path = staging_directory / f"{role}.json"
            descriptor = os.open(artifact_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as temporary_file:
                json.dump(artifacts[role], temporary_file, sort_keys=True)
                temporary_file.write("\n")
        if destination_directory.exists():
            raise ReviewError("exact-SHA artifact directory already exists")
        staging_directory.replace(destination_directory)
        staging_directory = None
        return [_artifact_path(context, role) for role in roles]
    except OSError as error:
        raise ReviewError(f"cannot persist artifacts: {error}") from error
    finally:
        if staging_directory is not None:
            shutil.rmtree(staging_directory, ignore_errors=True)


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
        manifest_path = args.manifest
        policy = _policy_context(repo_dir)
        steward_review.validate_lexical_path(manifest_path, "manifest path")
        manifest_path = manifest_path.resolve()
        context = load_context(repo_dir, manifest_path, policy)
        revalidate_context(context)
        ensure_private_report_root(context["report_root"])
        context["diff"] = committed_diff(context)
        revalidate_context(context)
        artifacts = {}
        for role in context["roles"]:
            if role == "adversarial":
                artifacts[role] = run_secondary_reviewer(context, args.hermes_bin)
            else:
                artifacts[role] = run_reviewer(role, context["reviewers"][role], context, args.hermes_bin)
            revalidate_context(context)
        for role in context["roles"]:
            if role not in artifacts:
                raise ReviewError(f"{role} artifact missing")
        revalidate_context(context)
        for path in persist_artifacts(context, artifacts):
            print(path)
    except ReviewError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic scorer for the private Steward review benchmark."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

VALID_DEFECT_CLASSES = frozenset({
    "capability-boundary",
    "credential-egress",
    "cancellation-compensation",
    "state-lock-recovery",
    "snapshot-pagination",
    "shape-type-time-validation",
    "authorization-widening",
    "process-lifecycle",
    "error-propagation",
})
_REQUIRED_FIELDS = frozenset({
    "id", "source", "revision", "language", "defect_class", "hypothesis",
    "expected_probes", "severity", "disposition",
})
_SOURCE_FIELDS = frozenset({"repository", "pr"})
_FORBIDDEN_FIELDS = frozenset({
    "body", "comment", "comments", "review", "review_body", "raw_review",
    "transcript", "prompt", "diff_hunk",
})
_TOKEN_PATTERN = re.compile(
    r"(?:\b(?:github_pat|gh[pours])_[A-Za-z0-9_]{20,}\b"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{20,}\b"
    r"|\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*[A-Za-z0-9._~+/=-]{20,}\b)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^/|^[A-Za-z]:[\\/]|^\\\\)")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PROBE_SEPARATORS = re.compile(r"[_\s]+")


def normalize_probe_id(value: str) -> str:
    """Normalize stable probe IDs without attempting fuzzy text matching."""
    return _PROBE_SEPARATORS.sub("-", value.strip().lower())


def _validate_redaction(value: Any, path: tuple[str, ...] = ()) -> None:
    """Reject raw review content, secrets, and live paths at every nesting level."""
    location = ".".join(path) or "<root>"
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_FIELDS:
                raise ValueError(f"raw review field is not permitted at {location}")
            _validate_redaction(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_redaction(child, (*path, str(index)))
    elif isinstance(value, str):
        if _TOKEN_PATTERN.search(value):
            raise ValueError(f"token-like string is not permitted at {location}")
        if _ABSOLUTE_PATH_PATTERN.search(value):
            raise ValueError(f"absolute path is not permitted at {location}")


def validate_cases(cases: Any) -> list[dict[str, Any]]:
    """Reject corpus data that is incomplete, unredacted, or non-deterministic."""
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark corpus must be a non-empty list")

    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or case.keys() != _REQUIRED_FIELDS:
            raise ValueError("each case must contain exactly the required benchmark fields")
        _validate_redaction(case)
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in ids:
            raise ValueError("case IDs must be unique non-empty strings")
        ids.add(case["id"])
        source = case["source"]
        if (not isinstance(source, dict) or source.keys() != _SOURCE_FIELDS
                or not isinstance(source.get("repository"), str)
                or not source["repository"] or not isinstance(source.get("pr"), int)
                or source["pr"] <= 0):
            raise ValueError("each case requires a source repository and PR number")
        if not isinstance(case["revision"], str) or not _REVISION_PATTERN.fullmatch(case["revision"]):
            raise ValueError("each case requires an exact 40-character reviewed revision")
        if case["defect_class"] not in VALID_DEFECT_CLASSES:
            raise ValueError("case has an unknown defect class")
        probes = case["expected_probes"]
        if (not isinstance(probes, list) or not probes
                or any(not isinstance(probe, str) or not normalize_probe_id(probe) for probe in probes)):
            raise ValueError("each case requires an expected probe list")
    return cases


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as corpus_file:
        return validate_cases(json.load(corpus_file))


def score_cases(cases: list[dict[str, Any]], reviewer_findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Score only structured probe IDs, preserving corpus order in case results."""
    validate_cases(cases)
    finding_ids: list[str] = []
    for finding in reviewer_findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("probe_id"), str):
            raise ValueError("each reviewer finding requires a string probe_id")
        probe_id = normalize_probe_id(finding["probe_id"])
        if not probe_id:
            raise ValueError("probe_id cannot be empty")
        if probe_id not in finding_ids:
            finding_ids.append(probe_id)

    expected_by_case = [
        {normalize_probe_id(probe) for probe in case["expected_probes"]}
        for case in cases
    ]
    expected = set().union(*expected_by_case)
    matched_case_ids = [
        case["id"] for case, probes in zip(cases, expected_by_case)
        if probes & set(finding_ids)
    ]
    missed_case_ids = [case["id"] for case in cases if case["id"] not in matched_case_ids]
    unexpected_findings = [probe_id for probe_id in finding_ids if probe_id not in expected]
    matched_count = len(matched_case_ids)
    return {
        "cases": len(cases),
        "matched_case_ids": matched_case_ids,
        "missed_case_ids": missed_case_ids,
        "unexpected_findings": unexpected_findings,
        "recall": matched_count / len(cases),
        "precision_proxy": matched_count / (matched_count + len(unexpected_findings))
        if matched_count + len(unexpected_findings) else 0.0,
    }


def _private_output_path(value: str) -> Path:
    output = Path(value)
    repository = Path(__file__).resolve().parents[1]
    if not output.is_absolute():
        raise argparse.ArgumentTypeError("output path must be absolute")
    try:
        output.resolve().relative_to(repository.resolve())
    except ValueError:
        return output
    raise argparse.ArgumentTypeError("output path must be outside the working tree")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--findings", required=True, type=Path)
    parser.add_argument("--output", required=True, type=_private_output_path)
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    with args.findings.open(encoding="utf-8") as findings_file:
        findings = json.load(findings_file)
    if not isinstance(findings, list):
        raise ValueError("reviewer findings must be a JSON list")
    score = score_cases(cases, findings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

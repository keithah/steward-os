"""Deterministic scorer for the private Steward review benchmark."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

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
_FORBIDDEN_FIELDS = frozenset({
    "body", "comment", "comments", "review", "review_body", "raw_review",
    "transcript", "prompt", "diff_hunk",
})
_TOKEN_PATTERN = re.compile(
    r"(?:bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)[A-Za-z0-9._~+/=-]{20,}",
    re.IGNORECASE,
)
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PROBE_SEPARATORS = re.compile(r"[_\s]+")


def normalize_probe_id(value: str) -> str:
    """Normalize stable probe IDs without attempting fuzzy text matching."""
    return _PROBE_SEPARATORS.sub("-", value.strip().lower())


def _walk_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def validate_cases(cases: Any) -> list[dict[str, Any]]:
    """Reject corpus data that is incomplete, unredacted, or non-deterministic."""
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark corpus must be a non-empty list")

    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or _REQUIRED_FIELDS - case.keys():
            raise ValueError("each case must contain the required benchmark fields")
        if _FORBIDDEN_FIELDS & case.keys():
            raise ValueError("raw review fields are not permitted in the corpus")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in ids:
            raise ValueError("case IDs must be unique non-empty strings")
        ids.add(case["id"])
        source = case["source"]
        if (not isinstance(source, dict) or not isinstance(source.get("repository"), str)
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
        if any(_TOKEN_PATTERN.search(text) for text in _walk_values(case)):
            raise ValueError("token-like strings are not permitted in the corpus")
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

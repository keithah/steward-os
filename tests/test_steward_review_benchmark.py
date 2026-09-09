"""Tests for the redacted Steward review benchmark."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

from steward_review_benchmark import (  # noqa: E402
    VALID_DEFECT_CLASSES,
    load_cases,
    score_cases,
    validate_cases,
)


FIXTURE = REPOSITORY / "tests" / "fixtures" / "review-corpus" / "code-rabbit-cases.json"


class StewardReviewBenchmarkTests(unittest.TestCase):
    def test_fixture_is_a_valid_redacted_twenty_case_corpus(self) -> None:
        cases = load_cases(FIXTURE)

        self.assertEqual(len(cases), 20)
        self.assertEqual(validate_cases(cases), cases)
        self.assertEqual(len({case["id"] for case in cases}), 20)
        self.assertTrue({"capability-boundary", "credential-egress", "cancellation-compensation",
                         "state-lock-recovery", "snapshot-pagination", "shape-type-time-validation",
                         "authorization-widening", "process-lifecycle", "error-propagation"}
                        <= {case["defect_class"] for case in cases})

    def test_validation_rejects_unsafe_or_incomplete_cases(self) -> None:
        case = {
            "id": "pagination-changing-total",
            "source": {"repository": "keithah/example", "pr": 12},
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "language": "python",
            "defect_class": "snapshot-pagination",
            "hypothesis": "Changed totals must not shift an established page boundary.",
            "expected_probes": ["pagination.snapshot", "pagination.changed-total"],
            "severity": "high",
            "disposition": "remediated",
        }
        self.assertEqual(validate_cases([case]), [case])

        invalid_cases = []
        duplicate = [case, copy.deepcopy(case)]
        invalid_cases.append(duplicate)
        unknown_class = copy.deepcopy(case)
        unknown_class["defect_class"] = "unknown"
        invalid_cases.append([unknown_class])
        raw_body = copy.deepcopy(case)
        raw_body["review_body"] = "not permitted"
        invalid_cases.append([raw_body])
        token_like = copy.deepcopy(case)
        token_like["hypothesis"] = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        invalid_cases.append([token_like])
        github_pat = copy.deepcopy(case)
        github_pat["hypothesis"] = "github_pat_" + "A" * 82
        sk_proj = copy.deepcopy(case)
        sk_proj["hypothesis"] = "Potential credential: sk-proj-" + "A" * 48
        sk = copy.deepcopy(case)
        sk["hypothesis"] = "Potential credential: sk-" + "A" * 48
        short_sk_prose = copy.deepcopy(case)
        short_sk_prose["hypothesis"] = "«redacted:sk-brief-note»"
        self.assertEqual(validate_cases([short_sk_prose]), [short_sk_prose])
        nested_body = copy.deepcopy(case)
        nested_body["source"]["body"] = "raw bot prose"
        absolute_path = copy.deepcopy(case)
        absolute_path["hypothesis"] = "/Users/alice/work/private-repo/config.json"
        for unsafe in (github_pat, sk_proj, sk, nested_body, absolute_path):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    validate_cases([unsafe])
        unexpected_top_level = copy.deepcopy(case)
        unexpected_top_level["extra"] = "not permitted"
        invalid_cases.append([unexpected_top_level])
        unexpected_source_field = copy.deepcopy(case)
        unexpected_source_field["source"]["extra"] = "not permitted"
        invalid_cases.append([unexpected_source_field])
        missing_metadata = copy.deepcopy(case)
        del missing_metadata["revision"]
        invalid_cases.append([missing_metadata])
        missing_probes = copy.deepcopy(case)
        missing_probes["expected_probes"] = []
        invalid_cases.append([missing_probes])

        for cases in invalid_cases:
            with self.subTest(cases=cases):
                with self.assertRaises(ValueError):
                    validate_cases(cases)

    def test_scorer_translates_bounded_role_probe_to_canonical_corpus_probes(self) -> None:
        cases = [{
            "id": "credential-output-redirect",
            "source": {"repository": "keithah/example", "pr": 12},
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "language": "python",
            "defect_class": "credential-egress",
            "hypothesis": "Outputs and redirects must not leak credentials.",
            "expected_probes": ["credential.output-boundary", "credential.redirect-boundary"],
            "severity": "high",
            "disposition": "remediated",
        }]

        score = score_cases(cases, [
            {"probe_id": "adversarial.token-output-redirect-boundaries"},
        ])

        self.assertEqual(score["matched_case_ids"], ["credential-output-redirect"])
        self.assertEqual(score["unexpected_findings"], [])

    def test_scorer_matches_normalized_probe_ids_and_reports_unexpected(self) -> None:
        cases = [
            {
                "id": "pagination-changing-total",
                "source": {"repository": "keithah/example", "pr": 12},
                "revision": "0123456789abcdef0123456789abcdef01234567",
                "language": "python",
                "defect_class": "snapshot-pagination",
                "hypothesis": "Page boundaries are stable while totals change.",
                "expected_probes": ["pagination.changed-total"],
                "severity": "high",
                "disposition": "remediated",
            },
            {
                "id": "cancellation-compensation",
                "source": {"repository": "keithah/example", "pr": 13},
                "revision": "fedcba9876543210fedcba9876543210fedcba98",
                "language": "go",
                "defect_class": "cancellation-compensation",
                "hypothesis": "Cancellation compensates completed partial work.",
                "expected_probes": ["cancellation.compensation"],
                "severity": "high",
                "disposition": "remediated",
            },
        ]

        score = score_cases(cases, [
            {"probe_id": "PAGINATION.CHANGED_TOTAL"},
            {"probe_id": "unrelated.probe"},
        ])

        self.assertEqual(score["cases"], 2)
        self.assertEqual(score["matched_case_ids"], ["pagination-changing-total"])
        self.assertEqual(score["missed_case_ids"], ["cancellation-compensation"])
        self.assertEqual(score["unexpected_findings"], ["unrelated.probe"])
        self.assertEqual(score["recall"], 0.5)
        self.assertEqual(score["precision_proxy"], 0.5)

    def test_review_skill_requires_structured_primary_and_adversarial_probes(self) -> None:
        skill = (REPOSITORY / "skills" / "hermes-pr-review" / "SKILL.md").read_text(encoding="utf-8")
        required_terms = [
            "full changed-file/caller/test inspection",
            "public contract and compatibility paths",
            "malformed/omitted/negative/timezone inputs",
            "policy at effectful sinks",
            "token/output/redirect boundaries",
            "cancellation and partial-success compensation",
            "all writers and shared locks",
            "pagination snapshots and changed totals",
            "ordering/deduplication",
            "ambient credentials and caller-widenable authorization",
            "process cleanup and status propagation",
            "probe_id",
            "passed/not-applicable with evidence",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, skill)

    def test_cli_requires_an_absolute_private_output_path(self) -> None:
        cases = load_cases(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            findings_path = Path(directory) / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            output_path = Path(directory) / "score.json"
            from steward_review_benchmark import main

            self.assertEqual(main([
                "--cases", str(FIXTURE), "--findings", str(findings_path),
                "--output", str(output_path),
            ]), 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["cases"], len(cases))
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--cases", str(FIXTURE), "--findings", str(findings_path), "--output", "score.json"])


if __name__ == "__main__":
    unittest.main()

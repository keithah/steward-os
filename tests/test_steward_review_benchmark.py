"""Tests for the redacted Steward review benchmark."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock
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

        valid_url_cases = []
        for url in (
            "https://example.test/path?next=/login",
            "https://example.test/?redirect=/private/area",
            "//cdn.example.test/app",
            "https://user@example.test/?next=/login",
            "https://[2001:db8::1]/?next=/login",
        ):
            url_case = copy.deepcopy(case)
            url_case["hypothesis"] = f"A valid URL: {url}"
            valid_url_cases.append(url_case)
        for url_case in valid_url_cases:
            with self.subTest(url_case=url_case):
                self.assertEqual(validate_cases([url_case]), [url_case])

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
        embedded_posix_path = copy.deepcopy(case)
        embedded_posix_path["hypothesis"] = "Stored at /Users/alice/private/config.json."
        embedded_windows_path = copy.deepcopy(case)
        embedded_windows_path["hypothesis"] = "Stored at C:\\Users\\alice\\private\\config.json."
        embedded_unc_path = copy.deepcopy(case)
        embedded_unc_path["hypothesis"] = "Stored at \\\\server\\share\\private\\config.json."
        for unsafe in (
            github_pat, sk_proj, sk, nested_body, absolute_path, embedded_posix_path,
            embedded_windows_path, embedded_unc_path,
        ):
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

    def test_validation_rejects_nonstring_or_blank_metadata_and_boolean_pr_numbers(self) -> None:
        case = {
            "id": "pagination-changing-total",
            "source": {"repository": "keithah/example", "pr": 12},
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "language": "python",
            "defect_class": "snapshot-pagination",
            "hypothesis": "Changed totals must not shift an established page boundary.",
            "expected_probes": ["pagination.snapshot"],
            "severity": "high",
            "disposition": "remediated",
        }
        for field in ("language", "hypothesis", "severity", "disposition"):
            for value in (None, "", " \t", {}, []):
                malformed = copy.deepcopy(case)
                malformed[field] = value
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(ValueError):
                        validate_cases([malformed])
        for pr in (True, False):
            malformed = copy.deepcopy(case)
            malformed["source"]["pr"] = pr
            with self.subTest(pr=pr):
                with self.assertRaises(ValueError):
                    validate_cases([malformed])

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

        self.assertEqual(score["matched_probe_ids"], [
            "credential.output-boundary", "credential.redirect-boundary",
        ])
        self.assertEqual(score["candidate_case_ids_by_probe"], {
            "credential.output-boundary": ["credential-output-redirect"],
            "credential.redirect-boundary": ["credential-output-redirect"],
        })
        self.assertEqual(score["matched_case_ids"], [])
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
        self.assertEqual(score["matched_probe_ids"], ["pagination.changed-total"])
        self.assertEqual(score["candidate_case_ids_by_probe"], {
            "pagination.changed-total": ["pagination-changing-total"],
        })
        self.assertEqual(score["matched_case_ids"], [])
        self.assertEqual(score["missed_case_ids"], [])
        self.assertEqual(score["unexpected_findings"], ["unrelated.probe"])
        self.assertNotIn("recall", score)
        self.assertEqual(score["probe_coverage"], 0.5)
        self.assertEqual(score["precision_proxy"], 0.5)

    def test_scorer_does_not_claim_case_recall_when_one_probe_maps_to_two_cases(self) -> None:
        case = {
            "source": {"repository": "keithah/example", "pr": 12},
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "language": "python", "defect_class": "snapshot-pagination",
            "hypothesis": "Stable boundaries require snapshots.",
            "expected_probes": ["pagination.snapshot"], "severity": "high",
            "disposition": "remediated",
        }
        cases = [{**case, "id": "snapshot-case-one"}, {**case, "id": "snapshot-case-two"}]

        score = score_cases(cases, [{"probe_id": "pagination.snapshot"}])

        self.assertEqual(score["matched_probe_ids"], ["pagination.snapshot"])
        self.assertEqual(score["candidate_case_ids_by_probe"], {
            "pagination.snapshot": ["snapshot-case-one", "snapshot-case-two"],
        })
        self.assertEqual(score["matched_case_ids"], [])
        self.assertNotIn("recall", score)

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
            "steward_llm_review.py --repo-dir",
            "--manifest <emitted manifest>",
            "exact-SHA primary artifact",
            "exact-SHA adversarial artifact",
            "manual Markdown is supplemental",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, skill)

    def test_cli_requires_an_absolute_private_output_path(self) -> None:
        cases = load_cases(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            output_path = Path(directory).resolve() / "score.json"
            from steward_review_benchmark import main

            self.assertEqual(main([
                "--cases", str(FIXTURE), "--findings", str(findings_path),
                "--output", str(output_path),
            ]), 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["cases"], len(cases))
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--cases", str(FIXTURE), "--findings", str(findings_path), "--output", "score.json"])

    def test_cli_creates_owner_private_output_parents_and_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory).resolve() / "private" / "scorecards"
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            from steward_review_benchmark import main

            output_path = output_directory / "score.json"
            self.assertEqual(main([
                "--cases", str(FIXTURE), "--findings", str(findings_path),
                "--output", str(output_path),
            ]), 0)

            for path in (Path(directory).resolve() / "private", output_directory):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)

    def test_cli_rejects_intermediate_lexical_output_symlink_before_redirected_scorecard_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory).resolve()
            redirected = temporary_root / "private-redirected"
            redirected.mkdir(mode=0o700)
            nested = redirected / "nested"
            nested.mkdir(mode=0o700)
            output_link = temporary_root / "output-link"
            output_link.symlink_to(redirected, target_is_directory=True)
            findings_path = temporary_root / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            from steward_review_benchmark import main

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main([
                        "--cases", str(FIXTURE), "--findings", str(findings_path),
                        "--output", str(output_link / "nested" / "score.json"),
                    ])
            self.assertIn("lexical symlink", stderr.getvalue())
            self.assertFalse((nested / "score.json").exists())

    def test_cli_rejects_dot_dot_before_later_lexical_output_symlink_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory).resolve()
            redirected = temporary_root / "redirected"
            redirected.mkdir(mode=0o700)
            nested = redirected / "nested"
            nested.mkdir(mode=0o700)
            output_link = temporary_root / "output-link"
            output_link.symlink_to(redirected, target_is_directory=True)
            missing = temporary_root / "missing"
            findings_path = temporary_root / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            from steward_review_benchmark import main

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main([
                        "--cases", str(FIXTURE), "--findings", str(findings_path),
                        "--output", str(missing / ".." / "output-link" / "nested" / "score.json"),
                    ])
            self.assertIn("lexical symlink", stderr.getvalue())
            self.assertFalse(missing.exists())
            self.assertFalse((nested / "score.json").exists())

    def test_cli_rejects_nonprivate_existing_output_directory_without_chmodding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory).resolve() / "scorecards"
            output_directory.mkdir(mode=0o755)
            os.chmod(output_directory, 0o755)
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            from steward_review_benchmark import main

            with self.assertRaisesRegex(ValueError, "owner-private"):
                main([
                    "--cases", str(FIXTURE), "--findings", str(findings_path),
                    "--output", str(output_directory / "score.json"),
                ])
            self.assertEqual(stat.S_IMODE(output_directory.stat().st_mode), 0o755)

    def test_private_output_rejects_nonprivate_existing_creation_anchor_without_writes(self) -> None:
        from steward_review_benchmark import _prepare_private_output_directory

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "private-root"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            shared = root / "shared"
            shared.mkdir(mode=0o777)
            os.chmod(shared, 0o777)
            missing = shared / "new"
            output = missing / "score.json"

            with self.assertRaisesRegex(ValueError, "owner-private"):
                _prepare_private_output_directory(output)

            self.assertFalse(missing.exists())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o777)

    def test_cli_rejects_symlink_output_target_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory).resolve() / "scorecards"
            output_directory.mkdir(mode=0o700)
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            target = Path(directory).resolve() / "target.json"
            target.write_text("keep", encoding="utf-8")
            output_path = output_directory / "score.json"
            output_path.symlink_to(target)
            from steward_review_benchmark import main

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main([
                        "--cases", str(FIXTURE), "--findings", str(findings_path),
                        "--output", str(output_path),
                    ])
            self.assertIn("lexical symlink", stderr.getvalue())
            self.assertTrue(output_path.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_cli_rejects_dangling_symlink_output_target_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory).resolve() / "scorecards"
            output_directory.mkdir(mode=0o700)
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            output_path = output_directory / "score.json"
            output_path.symlink_to(Path(directory).resolve() / "missing-target.json")
            from steward_review_benchmark import main

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    main([
                        "--cases", str(FIXTURE), "--findings", str(findings_path),
                        "--output", str(output_path),
                    ])
            self.assertIn("lexical symlink", stderr.getvalue())
            self.assertTrue(output_path.is_symlink())

    def test_cli_cleans_temporary_scorecard_after_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory).resolve() / "scorecards"
            output_directory.mkdir(mode=0o700)
            findings_path = Path(directory).resolve() / "findings.json"
            findings_path.write_text("[]", encoding="utf-8")
            from steward_review_benchmark import main

            with mock.patch("steward_review_benchmark.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(ValueError, "cannot write scorecard"):
                    main([
                        "--cases", str(FIXTURE), "--findings", str(findings_path),
                        "--output", str(output_directory / "score.json"),
                    ])
            self.assertEqual(list(output_directory.glob(".score.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

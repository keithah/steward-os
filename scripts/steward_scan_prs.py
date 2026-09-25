#!/usr/bin/env python3
"""Scan all open PRs across configured repositories and post review comments."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Import the existing steward review runner
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from steward_review import (
    ReviewError,
    builtin_config,
    git_state,
    load_config,
    resolve_config_path,
    run_commands,
    post_command_state,
    select_lane,
    write_manifest,
    _config_revision,
    _origin_repository,
)


class ScanError(Exception):
    pass


def list_open_prs(repo: str) -> list[dict]:
    """List all open pull requests for a repository using gh CLI."""
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--state", "open", "--json", "number,title,headRefName,headRefOid,baseRefName,url"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def checkout_pr(repo: str, pr_number: int, work_dir: Path) -> Path:
    """Clone the repo and checkout a specific PR into a work directory."""
    clone_dir = work_dir / f"pr-{pr_number}"
    if clone_dir.exists():
        # Clean up existing directory
        subprocess.run(["rm", "-rf", str(clone_dir)], check=True)

    # Clone the repository
    subprocess.run(
        ["git", "clone", f"https://github.com/{repo}.git", str(clone_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Fetch the PR
    subprocess.run(
        ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
        cwd=clone_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    # Checkout the PR branch
    subprocess.run(
        ["git", "checkout", f"pr-{pr_number}"],
        cwd=clone_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    return clone_dir


def run_review_on_pr(
    repo_dir: Path,
    config_path: Path | None,
    config_dir: Path | None,
) -> dict:
    """Run the steward review on a PR checkout and return the manifest."""
    try:
        repo_dir = repo_dir.resolve()
        config = None
        config_source = None

        if config_path is not None:
            config_path = config_path.resolve()
            if config_path.is_relative_to(repo_dir):
                raise ScanError("configuration path must be outside the reviewed checkout")
            config = load_config(config_path, repo_dir)
            config_source = "private-override"
        elif config_dir is not None:
            resolved_path = resolve_config_path(config_dir, repo_dir)
            if resolved_path is not None:
                config = load_config(resolved_path, repo_dir)
                config_source = "private-override"

        if config is None:
            config = builtin_config(repo_dir)
            config_source = "builtin-default"

        manifest = git_state(repo_dir, config["repository"]["base_ref"])
        manifest.update(
            {
                "base_ref": config["repository"]["base_ref"],
                "config_revision": _config_revision(config),
                "config_source": config_source,
                "status": "ready",
                "lane": select_lane(manifest["changed_paths"], config["review"]),
                "evidence_gaps": [],
            }
        )
        manifest["commands"], manifest["skipped_checks"] = run_commands(
            repo_dir, config["review"]
        )
        manifest["evidence_gaps"].extend(
            f"{check['id']}: {check['reason']}" for check in manifest["skipped_checks"]
        )
        manifest["post_command_state"] = post_command_state(repo_dir, manifest)
        if (
            manifest["evidence_gaps"]
            or any(command["status"] == "failed" for command in manifest["commands"])
            or manifest["post_command_state"]["status"] == "failed"
        ):
            manifest["status"] = "blocked"

        return manifest
    except ReviewError as error:
        return {"status": "error", "error": str(error)}


def post_pr_comment(repo: str, pr_number: int, body: str) -> None:
    """Post a review comment on a PR using gh CLI."""
    subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body", body],
        check=True,
        capture_output=True,
        text=True,
    )


def format_review_comment(manifest: dict, pr_info: dict) -> str:
    """Format the manifest into a PR review comment."""
    lines = [
        "## 🤖 Steward PR Review",
        "",
        f"**Repository:** {manifest.get('repository', 'unknown')}",
        f"**Branch:** `{pr_info['headRefName']}`",
        f"**Head SHA:** `{manifest.get('head_sha', 'unknown')[:8]}`",
        f"**Base:** `{manifest.get('base_ref', 'unknown')}`",
        f"**Lane:** {manifest.get('lane', 'unknown')}",
        f"**Status:** {manifest.get('status', 'unknown')}",
        "",
    ]

    if manifest.get("evidence_gaps"):
        lines.append("### ⚠️ Evidence Gaps")
        for gap in manifest["evidence_gaps"]:
            lines.append(f"- {gap}")
        lines.append("")

    if manifest.get("commands"):
        lines.append("### 🔍 Quality Commands")
        for cmd in manifest["commands"]:
            status_icon = "✅" if cmd["status"] == "passed" else "❌"
            lines.append(f"- {status_icon} **{cmd['id']}**: {cmd['status']}")
            if cmd.get("stderr") and cmd["status"] == "failed":
                # Include first few lines of stderr for context
                stderr_lines = cmd["stderr"].strip().split("\n")[:5]
                lines.append("  ```")
                for stderr_line in stderr_lines:
                    lines.append(f"  {stderr_line}")
                lines.append("  ```")
        lines.append("")

    if manifest.get("skipped_checks"):
        lines.append("### ⏭️ Skipped Checks")
        for check in manifest["skipped_checks"]:
            lines.append(f"- **{check['id']}**: {check['reason']}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Steward PR Scanner*")

    return "\n".join(lines)


def scan_repository(
    repo: str,
    config_path: Path | None,
    config_dir: Path | None,
    work_dir: Path,
    dry_run: bool = False,
) -> list[dict]:
    """Scan all open PRs in a repository."""
    print(f"\n{'='*60}")
    print(f"Scanning repository: {repo}")
    print(f"{'='*60}")

    try:
        prs = list_open_prs(repo)
    except subprocess.CalledProcessError as error:
        print(f"❌ Failed to list PRs for {repo}: {error.stderr}", file=sys.stderr)
        return []

    if not prs:
        print(f"✅ No open PRs found in {repo}")
        return []

    print(f"Found {len(prs)} open PR(s)")

    results = []
    for pr in prs:
        pr_number = pr["number"]
        pr_title = pr["title"]
        print(f"\nProcessing PR #{pr_number}: {pr_title}")

        try:
            # Checkout the PR
            print(f"  → Checking out PR...")
            pr_dir = checkout_pr(repo, pr_number, work_dir)

            # Run the review
            print(f"  → Running steward review...")
            manifest = run_review_on_pr(pr_dir, config_path, config_dir)

            # Format the comment
            comment_body = format_review_comment(manifest, pr)

            # Post the comment (unless dry run)
            if dry_run:
                print(f"  → [DRY RUN] Would post comment:")
                print(f"    Status: {manifest.get('status', 'unknown')}")
                print(f"    Lane: {manifest.get('lane', 'unknown')}")
            else:
                print(f"  → Posting review comment...")
                post_pr_comment(repo, pr_number, comment_body)
                print(f"  ✅ Posted review comment")

            results.append({
                "repo": repo,
                "pr_number": pr_number,
                "pr_title": pr_title,
                "pr_url": pr["url"],
                "status": manifest.get("status"),
                "lane": manifest.get("lane"),
                "head_sha": manifest.get("head_sha"),
            })

        except Exception as error:
            print(f"  ❌ Failed to process PR #{pr_number}: {error}", file=sys.stderr)
            results.append({
                "repo": repo,
                "pr_number": pr_number,
                "pr_title": pr_title,
                "pr_url": pr["url"],
                "status": "error",
                "error": str(error),
            })

    return results


def write_summary(results: list[dict], output_path: Path) -> None:
    """Write a summary report of all scanned PRs."""
    summary = {
        "total_prs": len(results),
        "by_status": {},
        "by_repo": {},
        "prs": results,
    }

    for result in results:
        status = result.get("status", "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1

        repo = result["repo"]
        if repo not in summary["by_repo"]:
            summary["by_repo"][repo] = {"total": 0, "by_status": {}}
        summary["by_repo"][repo]["total"] += 1
        summary["by_repo"][repo]["by_status"][status] = (
            summary["by_repo"][repo]["by_status"].get(status, 0) + 1
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n📊 Summary written to: {output_path}")


def main() -> int:
    """Scan all open PRs across configured repositories."""
    parser = argparse.ArgumentParser(
        description="Scan all open PRs and post steward review comments"
    )
    parser.add_argument(
        "--repos",
        nargs="+",
        help="Repositories to scan (owner/repo format). If not provided, reads from config.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a steward review configuration file",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="Directory containing per-repository configuration files",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "steward-pr-scan",
        help="Working directory for PR checkouts (default: temp dir)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / ".config" / "steward-os" / "pr-scan-summary.json",
        help="Path to write the summary report (default: ~/.config/steward-os/pr-scan-summary.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the review but don't post comments to GitHub",
    )

    args = parser.parse_args()

    # Determine which repos to scan
    repos = args.repos
    if not repos:
        print("❌ No repositories specified. Use --repos or provide a config.", file=sys.stderr)
        return 1

    # Create work directory
    args.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"🔍 Steward PR Scanner")
    print(f"   Repositories: {', '.join(repos)}")
    print(f"   Work directory: {args.work_dir}")
    print(f"   Dry run: {args.dry_run}")

    # Scan each repository
    all_results = []
    for repo in repos:
        results = scan_repository(
            repo,
            args.config,
            args.config_dir,
            args.work_dir,
            args.dry_run,
        )
        all_results.extend(results)

    # Write summary
    write_summary(all_results, args.output)

    # Print final summary
    print(f"\n{'='*60}")
    print(f"📋 Scan Complete")
    print(f"{'='*60}")
    print(f"Total PRs scanned: {len(all_results)}")

    status_counts = {}
    for result in all_results:
        status = result.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

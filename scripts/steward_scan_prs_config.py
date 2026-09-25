#!/usr/bin/env python3
"""Wrapper script that reads repos from a config file and runs the PR scanner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from steward_scan_prs import scan_repository, write_summary


def load_repos_config(config_path: Path) -> dict:
    """Load the repositories configuration."""
    try:
        return json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read configuration: {error}") from error


def main() -> int:
    """Scan all configured repositories."""
    parser = argparse.ArgumentParser(
        description="Scan all configured repositories for open PRs"
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the repositories configuration file",
    )
    parser.add_argument(
        "--review-config",
        type=Path,
        help="Path to a steward review configuration file",
    )
    parser.add_argument(
        "--review-config-dir",
        type=Path,
        help="Directory containing per-repository review configuration files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the review but don't post comments to GitHub",
    )

    args = parser.parse_args()

    # Load the repos config
    try:
        config = load_repos_config(args.config)
    except RuntimeError as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1

    repos = config.get("repositories", [])
    if not repos:
        print("❌ No repositories configured", file=sys.stderr)
        return 1

    scan_config = config.get("scan", {})
    work_dir = Path(scan_config.get("work_dir", "/tmp/steward-pr-scan"))
    output = Path(scan_config.get("output", "~/.config/steward-os/pr-scan-summary.json")).expanduser()

    # Override post_comments with dry-run flag
    post_comments = scan_config.get("post_comments", True) and not args.dry_run

    print(f"🔍 Steward PR Scanner (Config Mode)")
    print(f"   Repositories: {', '.join(repos)}")
    print(f"   Work directory: {work_dir}")
    print(f"   Post comments: {post_comments}")

    # Create work directory
    work_dir.mkdir(parents=True, exist_ok=True)

    # Scan each repository
    all_results = []
    for repo in repos:
        results = scan_repository(
            repo,
            args.review_config,
            args.review_config_dir,
            work_dir,
            dry_run=not post_comments,
        )
        all_results.extend(results)

    # Write summary
    write_summary(all_results, output)

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

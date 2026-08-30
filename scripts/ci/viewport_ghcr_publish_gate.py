#!/usr/bin/env python3
"""Decide whether the fork GHCR image should publish for a main push."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


PIN_ONLY_FILES = frozenset({
    "deploy/docker-compose.yml",
    "deploy/dokploy.desired-state.json",
})


def should_publish(changed_files: set[str], before: str) -> bool:
    if not changed_files:
        return False
    if set(before) == {"0"}:
        return True
    return not changed_files <= PIN_ONLY_FILES


def changed_files_between(before: str, after: str) -> set[str]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", f"{before}..{after}"],
        text=True,
    )
    return {line.strip() for line in output.splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    changed = changed_files_between(args.before, args.after)
    publish = should_publish(changed, args.before)
    lines = [
        f"publish={str(publish).lower()}",
        "changed_files<<EOF",
        *sorted(changed),
        "EOF",
    ]
    if args.output:
        Path(args.output).open("a", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"publish={str(publish).lower()}")
    print("changed_files=" + ",".join(sorted(changed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Require the self-maintenance runtime to come from the published main history."""

from pathlib import Path
import re
import subprocess


def check(root, published="refs/remotes/origin/main"):
    workflow = root / ".github/workflows/continuous-ai-kit-maintenance.yml"
    matches = re.findall(r"uses: Maokami/continuous-ai-kit/\.github/workflows/scan-report\.yml@([0-9a-f]{40})\s*$",
                         workflow.read_text(), re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Self-maintenance needs one full runtime SHA")
    sha = matches[0]
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, published],
                            capture_output=True)
    if result.returncode:
        raise ValueError("Self-maintenance runtime must be in published main history; use the post-merge SHA")
    subprocess.run(["git", "-C", str(root), "cat-file", "-e", sha + ":.github/workflows/scan-report.yml"],
                   check=True, capture_output=True)
    print("Published self-maintenance runtime:", sha)


if __name__ == "__main__":
    check(Path(__file__).resolve().parents[1])

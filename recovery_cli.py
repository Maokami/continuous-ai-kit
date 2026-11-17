#!/usr/bin/env python3
"""Run triage recovery from the pinned package, outside the consumer import path."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cai.github import GitHub
from cai.recovery import emit, triage_update
from cai.triage import TriageConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "record", "reset"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    revision = os.environ.get("CAI_TOOL_SHA", "")
    actual = subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parent),
                                      "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision != actual:
        raise ValueError("Package revision mismatch")
    cfg = TriageConfig.from_mapping(json.loads(Path(args.config).read_text()))
    raw_reset = os.environ.get("CAI_RESET_AT", "")
    reset = int(raw_reset) if re.fullmatch(r"[0-9]{1,10}", raw_reset) else None
    emit(triage_update(GitHub(), cfg.marker_id, args.operation,
                       category=os.environ.get("CAI_FAILURE_CATEGORY", ""), reset_at=reset))


if __name__ == "__main__":
    main()

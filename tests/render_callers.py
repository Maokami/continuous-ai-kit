"""Materialize all caller variants for actionlint in local checks and CI."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
import onboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    files = {}
    for profile in install.PROFILES:
        rendered = install.render_scan("a" * 40, profile, ["sources/*"],
                                       "lean" if profile == "lean" else "text", "1.0", profile=profile)
        files.update({Path(p).name: body for p, body in rendered.items() if p.endswith(".yml")})
    rendered = onboard.render_triage("a" * 40, "pr-triage", "1.0", ["Unit tests", "Lint"])
    files.update({Path(p).name: body for p, body in rendered.items() if p.endswith(".yml")})
    args.output.mkdir(parents=True, exist_ok=True)
    install.write_files(args.output, files)


if __name__ == "__main__":
    main()

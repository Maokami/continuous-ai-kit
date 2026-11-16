"""Emit fixed failure categories without publishing credentials or SDK message text."""

import argparse
import json
import os
from pathlib import Path
import sys

MAX_BYTES = 4_000_000
UNAVAILABLE = {"category": "execution_record_unavailable"}


def token_category(token):
    if not token:
        return "missing_oauth_token"
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
        return "oauth_token_whitespace"
    return "token_present"


def classify(messages):
    if not isinstance(messages, list):
        return dict(UNAVAILABLE)
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    if not results:
        return dict(UNAVAILABLE)
    result = results[-1]
    subtype = result.get("subtype")
    if subtype == "error_max_turns":
        return {"category": "turn_limit"}
    if subtype == "success" and result.get("is_error") is False:
        return {"category": "upstream_action_failure" if result.get("structured_output")
                else "structured_output_missing"}
    fields = [result.get("result", "")]
    errors = result.get("errors", [])
    if isinstance(errors, list):
        fields.extend(errors)
    message = " ".join(x for x in fields if isinstance(x, str)).lower()
    if any(s in message for s in ("usage limit", "rate_limit", "rate limit", "quota", "429")) or (
            "hit your" in message and "limit" in message):
        category = "usage_limit"
    elif any(s in message for s in ("authentication_error", "invalid api key", "invalid_api_key",
                                    "please run /login", "not logged in", "token has expired",
                                    "invalid x-api-key", "invalid bearer")):
        category = "authentication_error"
    elif "model" in message and any(s in message for s in (
            "not_found", "not found", "does not exist", "not have access", "not available")):
        category = "model_unavailable"
    else:
        category = "unknown_analysis_error"
    return {"category": category}


def read_failure(path):
    try:
        if path.is_symlink() or not path.is_file():
            return dict(UNAVAILABLE)
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return dict(UNAVAILABLE)
        return classify(json.loads(data))
    except (OSError, ValueError, RecursionError):
        return dict(UNAVAILABLE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check-token", "failure"))
    args = parser.parse_args()
    if args.operation == "check-token":
        category = token_category(os.environ.get("CAI_OAUTH_TOKEN", ""))
        print(json.dumps({"category": category}))
        return 0 if category == "token_present" else 1
    temp = os.environ.get("RUNNER_TEMP")
    result = read_failure(Path(temp) / "claude-execution-output.json") if temp else dict(UNAVAILABLE)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Emit fixed failure categories without publishing credentials or SDK message text."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

MAX_BYTES = 4_000_000
UNAVAILABLE = {"category": "execution_record_unavailable"}


def token_category(token):
    if not token:
        return "missing_oauth_token"
    if any(not 33 <= ord(c) <= 126 for c in token):
        return "oauth_token_invalid_characters"
    return "token_present"


def classify(messages):
    if not isinstance(messages, list):
        return dict(UNAVAILABLE)
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    result = results[-1] if results else {}
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
    if any(s in message for s in ("credit balance is too low", "credits_required", "billing_error")):
        return {"category": "billing_error"}
    if any(s in message for s in ("authentication_error", "invalid api key", "invalid_api_key",
                                  "please run /login", "not logged in", "token has expired",
                                  "invalid x-api-key", "invalid bearer")) or re.search(
            r"\b(?:api(?: error)?|status(?: code)?|http(?: error)?)[: ]+(?:401|403)\b", message):
        return {"category": "authentication_error"}
    # Only top-level SDK events qualify; never parse reset times from prose or tool output.
    rejected = [m.get("rate_limit_info") for m in messages if isinstance(m, dict)
                and m.get("type") == "rate_limit_event"
                and isinstance(m.get("rate_limit_info"), dict)
                and m["rate_limit_info"].get("status") == "rejected"]
    if any(info.get("errorCode") == "credits_required" for info in rejected):
        return {"category": "billing_error"}
    if rejected:
        output = {"category": "usage_limit"}
        now = int(time.time())
        resets = [info.get("resetsAt") for info in rejected]
        resets = [value for value in resets if type(value) is int and now < value < now + 14 * 86400]
        if resets:
            output["reset_at"] = max(resets)
        return output
    if not results:
        return dict(UNAVAILABLE)
    if (any(s in message for s in ("usage limit", "rate_limit", "rate limit", "quota"))
            or re.search(r"\b(?:api(?: error)?|status(?: code)?|http(?: error)?)[: ]+429\b", message)
            or ("hit your" in message and "limit" in message)):
        category = "usage_limit"
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
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

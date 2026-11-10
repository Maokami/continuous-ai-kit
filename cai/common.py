"""Small validation primitives shared by deterministic workflow steps."""

import hashlib
import json
import re


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def bounded_text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid or oversized text field")


def neutralize(value):
    return value.replace("@", "＠").replace("<", "&lt;").replace(">", "&gt;")


def reject_secrets(value, literals=()):
    decoded = json.dumps(value, ensure_ascii=False)
    if any(secret and secret in decoded for secret in literals) or re.search(
        r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})", decoded
    ):
        raise ValueError("Refusing to retain output containing credential material")


def quote_at_line(sources, path, line, quote):
    if path not in sources or type(line) is not int or line < 1:
        raise ValueError("Invalid citation location")
    bounded_text(quote, 1500)
    if not "\n".join(sources[path].splitlines()[line - 1:]).startswith(quote):
        raise ValueError("Citation does not match the source")

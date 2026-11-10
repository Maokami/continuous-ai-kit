"""Strict versioned scan configuration and immutable, tracked source reads."""

import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

DEFAULTS = {
    "version": 1, "lane": "scan", "default_branch": "main", "batch_size": 4,
    "adapter": "text", "model": "claude-sonnet-5-5", "max_turns": 32,
    "policy_files": [], "legacy": None,
}


def git(*args):
    return subprocess.check_output(["git", *args])


def path_name(value, glob=False):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("Invalid path")
    path = PurePosixPath(value)
    pattern = r"[A-Za-z0-9_./*?-]+" if glob else r"[A-Za-z0-9_./-]+"
    if (not re.fullmatch(pattern, value) or path.is_absolute() or ".." in path.parts
            or str(path) != value or value.startswith(".git/")):
        raise ValueError("Only normalized relative repository paths are allowed")
    return value


def tracked_bytes(path):
    path_name(path)
    entries = git("ls-tree", "-z", "HEAD", "--", path).decode().split("\0")
    if (len(entries) != 2 or entries[-1] != "" or "\t" not in entries[0]
            or entries[0].split("\t", 1)[1] != path
            or entries[0].split(" ", 1)[0] not in {"100644", "100755"}):
        raise ValueError("Input must be a tracked regular file")
    current = Path(path)
    if any(p.is_symlink() for p in (current, *current.parents)):
        raise ValueError("Symlink inputs are forbidden")
    value = git("show", "HEAD:" + path)
    if len(value) > 250_000 or current.read_bytes() != value:
        raise ValueError("Input is oversized or differs from the pinned checkout")
    return value


def load(path):
    obj = json.loads(tracked_bytes(path))
    required = {"version", "lane", "source_globs", "prompt_file"}
    allowed = set(DEFAULTS) | {"source_globs", "prompt_file"}
    if not isinstance(obj, dict) or not required <= obj.keys() or obj.keys() - allowed:
        raise ValueError("Missing or unknown scan configuration keys")
    cfg = DEFAULTS | obj
    if type(cfg["version"]) is not int or cfg["version"] != 1:
        raise ValueError("Unsupported configuration version")
    if not isinstance(cfg["lane"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", cfg["lane"]):
        raise ValueError("Invalid lane identifier")
    for name in ("default_branch", "model"):
        if not isinstance(cfg[name], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", cfg[name]):
            raise ValueError(f"Invalid {name}")
    for name, maximum in (("batch_size", 8), ("max_turns", 64)):
        if type(cfg[name]) is not int or not 1 <= cfg[name] <= maximum:
            raise ValueError(f"Invalid {name}")
    if cfg["adapter"] not in {"lean", "text"} or cfg["legacy"] not in {None, "kuratowski-v1"}:
        raise ValueError("Unknown adapter or migration mode")
    for key in ("source_globs", "policy_files"):
        if (not isinstance(cfg[key], list) or len(cfg[key]) > 20
                or (key == "source_globs" and not cfg[key])):
            raise ValueError("Invalid file selection")
        for item in cfg[key]:
            path_name(item, glob=key == "source_globs")
    path_name(cfg["prompt_file"])
    return cfg


def source_files(cfg):
    names = git("ls-files", "-z").decode().split("\0")[:-1]
    names = sorted(p for p in names if any(fnmatch.fnmatchcase(p, g) for g in cfg["source_globs"]))
    if not names or len(names) > 1000:
        raise ValueError("Empty or oversized source selection")
    texts = {p: tracked_bytes(p).decode() for p in names}
    if sum(len(s.encode()) for s in texts.values()) > 2_000_000:
        raise ValueError("Source selection exceeds 2 MB")
    return texts

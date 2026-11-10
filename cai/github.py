"""Bounded GitHub REST access. Never retry writes with an unknown outcome."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GitHub:
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ValueError("Invalid repository")
        self.token = os.environ["GITHUB_TOKEN"]

    def request(self, path, data=None, method=None):
        if not path.startswith(f"/repos/{self.repo}/"):
            raise ValueError("Only the consumer repository is accessible")
        method = method or ("GET" if data is None else "POST")
        request = Request(
            "https://api.github.com" + path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=60) as response:
                    raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError("GitHub response exceeds the size limit")
                return json.loads(raw) if raw else None
            except (HTTPError, URLError) as error:
                retryable = not isinstance(error, HTTPError) or error.code in (429, 500, 502, 503, 504)
                if method != "GET" or not retryable or attempt == 2:
                    raise RuntimeError(f"GitHub {method} failed; reconcile before retrying") from None
                time.sleep(2 ** attempt)

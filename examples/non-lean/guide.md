# Example cache API

`read(key)` returns a cached value or None. Calling it never extends the entry's lifetime.
The default expiry is five minutes. Call `read(key)` regularly to keep an entry alive.

This deliberately inconsistent fixture lets a documentation scan produce a checkable candidate.
It is not production advice or a benchmark of repository-wide recall.

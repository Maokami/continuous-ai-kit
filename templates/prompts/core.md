## Code contracts and regression gaps

Focus on one observable behavior at a time: a boundary condition, error path, or a comment/test
that contradicts its implementation. Quote the contract and the relevant implementation. Trace an
existing caller or test before proposing a new abstraction. Distinguish a demonstrated inconsistency
from a hypothesis that needs execution. An untested boundary is a test proposal, not a proven bug.
Use `documentation` for contract/test-gap findings, `assumptions` for missing stated preconditions,
and `duplicate` or `dependency` only for concrete maintenance costs. These are the existing schema
kinds; do not invent `bug` or `test`. Risk is `manual`. Give a minimal reproduction or verification
step in proposal, without claiming to have executed it. Never weaken tests to fit an implementation.

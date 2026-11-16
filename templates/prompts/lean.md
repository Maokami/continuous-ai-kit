## Lean library maintenance

Look for duplicate proofs with an existing replacement lemma, unused assumptions, avoidable
dependencies, or misleading statement documentation. Check the full public type and callers.
Use `proof-only` only when the proposed proof change preserves every public statement, hypothesis,
and axiom policy. Removing a hypothesis or changing an interface is `manual`, even if desirable.
Do not claim elaboration or axiom-audit success: this lane only reads source. Cite the proposed
replacement and specify the target build/audit that would establish it. Never propose sorry,
admit, a new axiom, weakened conclusions, or a changed specification to make a proof pass.

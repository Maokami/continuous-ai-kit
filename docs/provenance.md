# Provenance and scope

The scan ledger and validation originated in Maokami/kuratowski-lean at
138e567dbacbed099436c88e7f516bb748f28fe7 (`scripts/lean_improvement.py` and its tests).
The PR triage adapter originated in Maokami/smarandache-wellin-lean at
7843df953414f78faa80a0ff3ae583f4cbe1bc10 (`scripts/pr_triage.py` and its tests).
Both repositories are maintained by Maokami. AI tools helped implement and review this extraction.

The extraction adds a versioned configuration, explicit callee revision, strict tracked-source
reads, reusable workflows, and a language-neutral text adapter. It retains original ledger formats
where requested. SW triage was differentially checked against the original on 3,000 generated
scenarios; this is compatibility evidence, not a guarantee for all inputs.

Merge/status/branch cleanup and mathematical proof policy remain consumer-specific. The package
does not generalize them into a workflow DSL. Output strings are currently Korean; source comments
and docstrings are English. Localization and an append-only distributed ledger are out of scope.

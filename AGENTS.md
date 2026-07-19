# Project instructions

These instructions apply to the entire repository.

## Mission

Ship a judge-testable Roundtable Messaging v2 developer tool for the 2026
OpenAI Build Week. Prefer a small, reliable release over speculative features.

## Collaboration boundary

- Ocean is the human product lead and final decision-maker.
- New product work in this repository is led, implemented, and reviewed through
  GPT-5.6 in Codex unless Ocean explicitly changes that boundary.
- Preserve historical Fable 5 and Hermes attribution exactly as documented in
  `PROVENANCE.md` and `CREDITS.md`; do not present historical collaboration as
  new work in this repository.
- Do not ask another harness to implement new product code without Ocean's
  explicit approval.

## Provenance

- Never copy a source tree as an unexplained snapshot.
- A replayed change must retain its original source commit in the commit body
  and in `docs/provenance/source-commits.tsv`.
- Exclude runtime mailboxes, local registries, backups, transcripts, secrets,
  personal paths, and unrelated project material.
- Do not weaken or rewrite contributor attribution to improve a submission
  narrative. Describe uncertainty explicitly.

## Product constraints

- Durable maildir delivery is the fact source.
- Core send, receive, acknowledge, recovery, and diagnostics must work without
  cmux. cmux support is an optional adapter.
- Use one explicit Codex executable resolver for the launcher, daemon, wake
  bridge, and doctor.
- Fail closed on unsupported Codex protocol behavior. Do not claim support from
  version-number comparisons or fixtures alone.
- Cross-host transport and multi-auth switching are out of the Build Week P0
  scope unless Ocean changes the plan.

## Implementation and tests

- Prefer the Python standard library; declare every non-standard dependency.
- Follow the shared environment rule: use `mamba run -n general ...` for Python
  commands, never bare `python3` or `pip3 install`.
- Every behavior change needs focused regression coverage.
- Before a commit, run the focused tests, the full suite, compile checks, and
  the repository's public-safety scan.
- Installation and uninstallation must be idempotent and modify only managed
  files, symlinks, launch agents, and marked configuration blocks.

## Release claims

- A supported platform/runtime combination needs a real end-to-end smoke test.
- The judge path must start from a release artifact, require no source rebuild,
  and finish in five minutes or less.
- Keep README support tables and limitations honest and current.

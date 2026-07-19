# Source replay policy

`source-commits.tsv` is the durable mapping between the private implementation
history and this public Build Week repository.

For each replay:

1. inspect the source diff and contributor trailers;
2. exclude the paths listed in `PROVENANCE.md`;
3. remove personal absolute paths and unrelated project fixtures;
4. apply the remaining logical change;
5. run focused tests and the public-safety scan;
6. record the new public commit SHA and verification status.

A blank `public_commit` means the source change has not yet been replayed.
Source hashes are identifiers for auditability, not links to a public private
repository.

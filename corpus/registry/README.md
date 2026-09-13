# Corpus source registry

This directory is the policy authority for every network acquisition that the
future corpus builder may perform. The runtime application never reads it and
never performs a corpus fetch. See `corpus_policy.py` and
`tests/test_phase0_gates.py` for the executable contract. Private planning
documents are intentionally not included in the public source tree.

Layout:

```text
corpus/
  registry/
    README.md                        this file
    source_record.template.json      one file per source under sources/<source_id>.json
    policy_decision.template.json    reviewer decision attached to a source record
    sources/                         registered sources (none yet)
  tombstones/                        deletion / revocation records (rebuild required)
  quarantine/                        builder-only fetch isolation; git-ignored, never shipped
```

Rules enforced by `corpus_policy.py`:

- A fetch of any kind requires a complete record, `decision == approved`, an
  unexpired `policy_review_expires_at`, and past `robots_checked_at` and
  `terms_checked_at` timestamps.
- Class `E` is never fetched. Class `D` may supply metadata only, never prose.
- `transform` and `redistribute` need class `A` or `B` plus the matching
  `allowed_transformation` / `allowed_redistribution` flag.
- A tombstoned `source_id` is blocked for every purpose until the pack is
  rebuilt without it.
- `image/*`, `video/*`, and `audio/*` payloads are rejected by the text-only
  contract before storage.

No source is approved by creating a file here. Approval requires a reviewer,
a reason, evidence hashes for robots/terms/license snapshots, and an expiry.

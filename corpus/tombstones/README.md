# Tombstones

One JSON file per revoked, denied, or deleted source. A tombstone blocks every
gate purpose for its `source_id` and marks every listed pack as requiring a
rebuild. Old packs remain auditable but are not selectable for new output.

Create tombstones with `corpus_policy.tombstone(record, reason, affected_pack_ids=[...])`.

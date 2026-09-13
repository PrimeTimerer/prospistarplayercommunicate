# Public-source privacy boundary

The first public commit is a new snapshot, not a push of the private development
repository. Private Git history, handoffs, logs, reports, backups, runtime
settings, encrypted keys, save files, model weights, captured images, original
OCR and personal story memory are excluded.

The old binary fixture and OCR outputs were not copied. Their replacements are
built from zero/declared constants by `scripts/build_test_fixture.py`; verify
all seven with `--check`. Personal shared-chat/Gem URLs and access records are
absent. The optional story-reference pack is newly authored generic fiction;
its legacy filename/IDs remain for route compatibility, not as user content.

Small numeric/name literals in unit tests are regression examples, not a shipped
player profile or account. Some real baseball names also exercise transliteration
and alias handling. No real player's generated personal archive is included.

`scripts/verify_public_tree.py` checks every tracked path, common credential
patterns, private account paths, shared conversation IDs and deterministic fixture
identity. It complements human review; no pattern scanner can prove absence of
every possible secret. Review diffs and the exact staged tree before publication.

Never commit `%LOCALAPPDATA%/StarModeFeed`, a portable profile, or game saves.
Do not upload raw logs/screenshots to issues. Review and redact locally; security
reports should use GitHub's private vulnerability reporting when available.
No telemetry, issue-report upload or external generation is enabled by cloning.

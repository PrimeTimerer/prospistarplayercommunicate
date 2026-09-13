# Initial public snapshot verification

Local verification on 2026-09-13 used Windows 11 x64 and Python 3.14.0.
The public version is 2.7.10-preview; the successful generation engine remains
based on 2.7.9-preview. This is a source publication, not an installed-app upgrade.

- `python -m compileall -q .`: passed.
- `python run_tests.py`: 767 discovered, 766 passed, one optional real-save test
  skipped, 54.380 seconds. Existing tests were retained; six privacy regressions
  were added. Two fixture tests were renamed to describe the new synthetic data
  and removal of private Gem access records.
- Seven save/OCR fixtures exactly match the from-zero synthetic generator.
- All tracked public files passed the credential/path/fixture audit and manual
  publication review. This is not a guarantee against every possible disclosure.
- `build.ps1`: succeeded. Exact executable verification matched 30 assets,
  found 29 required modules, and checked both Windows version resources.
- The exact executable's isolated `--verify-backend` passed 36 checks in
  2.736 seconds with exit code zero and normal shutdown.

No real provider request, user save, model startup, GUI interaction or installed
executable replacement was part of these checks. Provider behavior is tested with
fixtures/mocks. Long-form generation quality, all game builds and native UI
appearance are not established by these results. GitHub Actions supplies fresh
verification for future commits; refer to the matching run, not this historical
snapshot, when evaluating later changes.

# Contributing

This is a community-maintained preview. Small, independently reviewable fixes
are welcome; no response-time or ongoing-update commitment is implied.

1. Fork the repository and create a focused branch.
2. Install `requirements-dev.txt` in an isolated Python 3.14 environment on Windows.
3. Add a synthetic regression for the changed behavior. Do not upload saves,
   screenshots, raw OCR, personal conversations, account paths or API keys.
4. Run `python scripts/build_test_fixture.py --check`, `python -m compileall -q .`,
   then `python run_tests.py` and `python scripts/verify_public_tree.py`.
5. Explain what changed, which checks passed and what remains unverified in a PR.

The standard runner redirects user-state roots to a temporary directory, removes
provider keys from its environment, and never opts into the real-save smoke.
No paid API call or local model startup is required. Test names are not proof of
isolation: inspect any new process/network/file target before running it.

Preserve successful provider transport, complete save verification, player/world
isolation, cancellation/source guards, old data formats and prior generated work.
Treat narrative/image content as fiction or explicitly reviewed hints, not save
authority. Do not add implicit model startup or external transmission.

Shipped behavior changes update `product_version.py` and `CHANGELOG.md`; documentation
alone does not need a version bump. Windows packaging uses `build.ps1` and the
read-only `scripts/verify_packaged_release.py`/`--verify-backend` checks. Use fresh
artifact report paths. Never kill or overwrite a running user instance.

Optional browser tests in `scripts/test_*.cjs` require Node.js, Playwright and a
compatible browser. They use disposable fixtures; do not point them at a personal
running app. Native appearance and real long-form provider quality are separate
from automated backend checks and must not be claimed from mock results.

By submitting a contribution, you agree to license your contribution under the
project's MIT license. Include provenance and applicable notices for third-party
material. Do not copy another project's unlicensed code or community post bodies.

# Public repository maintenance

- Read README.md and the affected module/tests; use docs/ARCHITECTURE.md for
  service boundaries and docs/PRIVACY.md before changing fixtures or publication.
- Preserve user data, existing working behavior, public routes and old formats.
  Fiction, images and generated prose cannot become verified save statistics.
- Never require real saves, personal profiles, API keys or a running model for
  automated tests. Run compileall, run_tests.py and the public-tree/fixture checks
  for source changes; use focused checks after local corrections.
- No implicit external inference, model startup, game writes or process killing.
  Confirm exact targets and fresh backups before user-instance replacement.
- Keep changes narrow, update product_version.py for shipped behavior, and put
  evidence and remaining uncertainty in the PR. Do not import private handoffs,
  private Git history or data into this repository.
- Use English engineering comments/policies and retain Korean product UI text.

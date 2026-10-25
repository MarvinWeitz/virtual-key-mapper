# Copilot instructions

Follow [AGENTS.md](../AGENTS.md) at the repository root, and for files under `vscode-extension/`
also [vscode-extension/AGENTS.md](../vscode-extension/AGENTS.md). They hold the layout, the test
commands and the rules CI enforces.

The short version:

- Code outside `src/virtual_key_mapper/auth/` and `gateways/` must not name a gateway vendor
  (`tests/test_architecture.py`).
- Every pull request adds a user-facing line under `## Unreleased` in `CHANGELOG.md`, or gets the
  label `no changelog`.
- Tests use `tests/isolation.py` and never touch the real tool configuration or OS vault.
- Code runs on Windows, macOS and Linux with Python 3.11 or newer.
- Dependencies, npm packages and GitHub Actions are pinned exactly.

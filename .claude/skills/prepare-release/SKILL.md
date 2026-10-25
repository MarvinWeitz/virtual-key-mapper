---
name: prepare-release
description: Prepare a vkm release by raising the shared version and turning the changelog's Unreleased section into a dated entry. Use when asked to release, cut, or bump the version of the CLI or the VS Code extensions.
---

# Prepare a release

The CLI and both VS Code extensions share one version. A release is a pull request that raises it
everywhere and dates the changelog. After it merges, a maintainer runs the "Release" workflow by
hand. The README section "Releases" describes the whole process.

## 1. Choose the version

Read `## Unreleased` in `CHANGELOG.md`. The project is `0.x`: new features or changed behavior
raise the minor version (`0.1.2` → `0.2.0`), fixes and renames raise the patch version
(`0.1.2` → `0.1.3`). Propose the version to the person with a one-line reason and wait for them
to agree.

## 2. Change it everywhere

Set the same version in all five places:

1. `pyproject.toml`: `version = "X.Y.Z"`
2. `src/virtual_key_mapper/__init__.py`: `__version__ = "X.Y.Z"`
3. `vscode-extension/package.json`: `"version"`
4. `vscode-extension/local/package.json`: `"version"`
5. `vscode-extension/package-lock.json`: run `npm install --ignore-scripts` in `vscode-extension/`
   to update it. Check with `git diff` that only the two version fields changed.

## 3. Date the changelog

Rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` (today's date). Keep the entries as written,
but tidy the wording if needed: each line says what changed for a user. Do not add a new empty
`## Unreleased` heading; the next pull request adds it.

## 4. Check

```bash
python -m unittest tests.test_release -v
python -m unittest discover -s tests
cd vscode-extension && npm test && cd local && npm test
```

`tests/test_release.py` fails if any version disagrees or the newest changelog heading is wrong.

## 5. Hand over

Commit as "Release X.Y.Z" and open the pull request. The commit message lists the changelog
entries. Then tell the person the remaining steps, which only a maintainer can do:

1. Merge the pull request.
2. Actions → Release → Run workflow on `main`. Optionally run it first with "dry run".
3. Approve the `release` environment. The workflow publishes to PyPI and creates the GitHub
   release `vX.Y.Z` with both `.vsix` files.
4. Upload `vkm.vsix` and `vkm-host.vsix` to the Visual Studio Marketplace by hand (README, "Releases").

Never run the Release workflow, push a tag or publish anything yourself.

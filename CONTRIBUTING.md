# Contributing

Thank you for helping to improve Virtual Key Mapper. This guide explains how to propose a change.
[docs/architecture.md](docs/architecture.md) explains how it works and why,
[docs/adapters.md](docs/adapters.md) how to add a gateway, a sign-in kind or a tool, and
[docs/testing.md](docs/testing.md) what CI covers. The rules that tests and CI enforce are
collected in [AGENTS.md](AGENTS.md); they apply to people and coding agents alike.

By taking part in this project you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md). To
report a security problem, follow the [Security Policy](SECURITY.md) instead of opening an issue.

## Reporting bugs and requesting features

Search the [existing issues](https://github.com/cbs-consulting/virtual-key-mapper/issues) first.
If nothing matches, open a new issue with the bug report or feature request form. For a larger
change, such as a new gateway or tool, open an issue before you start so we can agree on the
approach.

Never paste a token, a refresh token or a `proxy.key` into an issue. `vkm status` and
`vkm profile show --json` print no secrets and are safe to share.

## Making a change

1. Fork the repository and create a branch from `main`.
2. Install the package from your checkout (Python 3.11 or newer):

   ```bash
   python -m venv .venv && .venv/bin/python -m pip install -e .
   ```

3. Make your change and add or update the tests for it. Tests never touch your real tool
   configuration or OS vault: use `isolate_tool_configuration(self)` from `tests/isolation.py`.
4. Check that everything passes:

   ```bash
   .venv/bin/python -m unittest discover -s tests -v
   cd vscode-extension && npm ci && npm test && npm run test:host   # if you changed an extension
   cd vscode-extension/local && npm test
   ```

   Live tests run the real tools against a local fake gateway (no model calls, no secrets).
   Install the pinned versions and point the tests at them:

   ```bash
   cd ci/agents && npm ci && cd ../..
   export $(node ci/agents/locate.mjs)    # sets VKM_TEST_{CLAUDE,CODEX,OPENCODE}_BINARY
   .venv/bin/python -m unittest discover -s tests -v
   ```

   Locally, missing tools and shells skip their tests; CI runs them on Windows, macOS and Linux
   and fails instead. `VKM_TEST_VAULT=1` also runs `tests/test_vault_native.py` against your real
   OS vault (uniquely named test entries, removed afterwards).

5. Update the README and the relevant doc when the behavior changes, as listed under
   "Docs move with behavior" in [AGENTS.md](AGENTS.md).
6. Add a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md) that describes the change for
   users. A pull request that changes nothing a user notices, such as a CI, test or
   documentation fix, gets the label "no changelog" instead.
7. Open a pull request against `main` and fill in the template. It needs the checks **Test
   features** and **Changelog entry**.
8. Once the checks pass and the pull request is approved, choose **Merge when ready**. Pull
   requests are merged through a merge queue: GitHub tests each one on top of `main` and the
   pull requests queued ahead of it, and merges it when the checks pass there too. You don't have
   to update your branch with `main` first.

Do not change the version: it is raised only when a release is prepared.

## Commit messages

Write the subject as an imperative sentence in plain words, for example "Let Claude Code list the
gateway's models", and say in the body why the change is needed. Keep each pull request to one
topic.

## License

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the license of this project.

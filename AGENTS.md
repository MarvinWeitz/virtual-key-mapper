# Agent instructions

Instructions for AI coding agents (Claude Code, Codex, OpenCode, Copilot and others) working in
this repository. People are welcome to read them too. What the tool does is in the
[README](README.md); how and why it works is in [docs/architecture.md](docs/architecture.md).

## What this repository is

`vkm` (package `virtual-key-mapper`, Python 3.11+) signs a developer in to their company's LLM
gateway (LiteLLM today) with its single sign-on and hands short-lived keys to Claude Code, Codex,
OpenCode and Copilot Chat. Every request carries the **labels** of the repository being worked
in (`customer = "acme"`), and nothing is sent while a required label has no value.

| Path | What it is |
| --- | --- |
| `src/virtual_key_mapper/` | The CLI. `cli.py` holds the commands, `service.py` the operations they share with other front ends; `adapters.py` the interfaces; `registry.py` wires adapters in. |
| `src/virtual_key_mapper/auth/`, `gateways/` | Input-side adapters (sign-in kinds, gateways). The only place vendor knowledge may live. |
| `src/virtual_key_mapper/integrations/` | Output-side adapters, one per tool (`claude`, `codex`, `opencode`). |
| `tests/` | `unittest` suite. `isolation.py` redirects tool config; `live.py` gates tests on real binaries. |
| `vscode-extension/` | The Copilot Chat extension `cbs-consulting.vkm` (TypeScript), with its own [AGENTS.md](vscode-extension/AGENTS.md). |
| `vscode-extension/local/` | The companion extension `cbs-consulting.vkm-host` that runs on the local machine. |
| `ci/agents/` | Pinned versions of the real agents for the live tests. |
| `ci/devcontainer/` | The dev container test (host side and container side). |
| `docs/` | Architecture, adapters, labels, network, testing. `docs/plan/` is historical; do not update it. |

## Commands

Install once from the checkout (the suite imports the package):

```bash
python -m venv .venv && .venv/bin/python -m pip install -e .
```

| What | Command |
| --- | --- |
| Python suite (always run this) | `python -m unittest discover -s tests` |
| One test file | `python -m unittest tests.test_labels -v` |
| Live tests with the real agents | `cd ci/agents && npm ci && cd ../.. && export $(node ci/agents/locate.mjs)`, then the suite |
| Main extension | `cd vscode-extension && npm ci && npm test` |
| Main extension in a real VS Code | `cd vscode-extension && npm run test:host` (downloads VS Code; needs a display or `xvfb-run -a`) |
| Host extension | `cd vscode-extension/local && npm test` (after `npm ci` in `vscode-extension`) |
| Build check | `python -m pip wheel --no-deps --wheel-dir dist . && vkm --help` |

Locally, a missing tool or shell **skips** its tests; CI sets `VKM_REQUIRE` so the same test
**fails** there. A green local run with skips does not mean CI passes. Mention the skipped suites
when you report results.

Never set `VKM_TEST_VAULT=1` unless the person asks: it writes to their real OS credential vault.

There is no linter or formatter configured. Match the style of the surrounding code.

## Rules that tests and CI enforce

- **The core knows no vendor.** Modules outside `auth/` and `gateways/` must not import them and
  must not contain `x-litellm`, `/tag/list`, `/v1` or `litellm-cli-auth`
  (`tests/test_architecture.py`). Integrations get URLs from `context.gateway.endpoint(...)` and
  headers from `integrations.common.attribution_headers`. Only `registry.py` imports adapters.
- **Every pull request adds a line under `## Unreleased` in `CHANGELOG.md`**, written for users,
  in the same pull request. A pull request that changes nothing a user notices (CI, tests,
  internal docs, agent files) gets the label `no changelog` instead.
- **One version everywhere.** `pyproject.toml`, `src/virtual_key_mapper/__init__.py`,
  `vscode-extension/package.json`, `vscode-extension/package-lock.json` and
  `vscode-extension/local/package.json` must agree (`tests/test_release.py`). Only change it when
  preparing a release (see `.claude/skills/prepare-release`).
- **Tests never touch the real machine.** Call `isolate_tool_configuration(self)` from
  `tests/isolation.py` in any test that sets up a tool, and use temporary folders for profiles.
  Tests that need a real binary go through the decorators in `tests/live.py`.
- **CI runs on Windows, macOS and Linux.** Use `pathlib`, write files with `encoding="utf-8"`,
  and do not assume a POSIX shell or `/tmp`. Python 3.11 is the minimum, so don't use syntax or
  stdlib features from newer versions.

## Conventions

- **Dependencies are pinned exactly** (`filelock==4.0.9`, npm versions without `^`, GitHub Actions
  by commit SHA). Do not add a runtime dependency without asking; the CLI has two.
- **JSON contracts are an API.** `token --json`, `repo show --json` and `profile show --json`
  (docs/architecture.md, section 3) are read by the VS Code extension, the shell hooks and the
  OpenCode plugin. Add fields freely, but never rename or remove one without updating every consumer.
- **Secrets stay out of output and files.** Tokens go to the OS vault (`storage.py`) or stdout of
  `vkm token`, never to logs, error messages, profiles or tool config files.
- **Tool setup is polite.** `setup` keeps unrelated configuration, refuses to replace another
  credential setup without `--replace`, and `remove` undoes only what `setup` wrote.
- **Missing labels block.** Anything that sends requests checks `attribution.complete` and shows the
  `MissingLabelsError` message, which starts with `Required labels have no value:`.
- **Docs move with behavior.** When you change what the CLI or a tool does, update the README section
  and the relevant doc in the same change:
  - a new network request or host: [docs/network.md](docs/network.md)
  - label resolution or formats: [docs/labels.md](docs/labels.md)
  - something CI cannot check: the manual checklist in [docs/testing.md](docs/testing.md)
  - a new adapter: [docs/adapters.md](docs/adapters.md) and the README tables
- **Writing style.** The docs, comments and changelog use short, plain sentences in the present
  tense, and say what happens for the user ("the prompt is blocked"), not how it is implemented.
  Docstrings are one line where possible. Follow that style.
- **Commits and PRs.** The subject is an imperative sentence in plain words ("Let Claude Code list
  the gateway's models"); the body says why. The upstream repository is
  `cbs-consulting/virtual-key-mapper`; work may happen on a fork.

## Workflows

Step-by-step guides live in `.claude/skills/` (Claude Code loads them as skills; other agents can
read them as plain Markdown):

- [`add-adapter`](.claude/skills/add-adapter/SKILL.md): add a gateway, a sign-in kind or a tool integration.
- [`prepare-release`](.claude/skills/prepare-release/SKILL.md): raise the version and get the changelog ready for the Release workflow.
- [`bump-agent-pins`](.claude/skills/bump-agent-pins/SKILL.md): update the pinned Claude Code, Codex and OpenCode in `ci/agents`.

## Don'ts

- Don't edit `docs/plan/`. It is the historical record of the redesign.
- Don't skip, disable or loosen a test to make CI pass.
- Don't run the Release workflow, publish to PyPI or the Marketplace, or push tags.
- Don't commit `.env*`, `*.vsix`, `out/`, `dist/` or `node_modules/`.

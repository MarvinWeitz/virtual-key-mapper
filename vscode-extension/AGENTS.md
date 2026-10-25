# Agent instructions: VS Code extensions

The repository-wide rules in the [root AGENTS.md](../AGENTS.md) apply here too. This file adds
what is specific to the two extensions.

## The two extensions

| Folder | ID | Runs | Does |
| --- | --- | --- | --- |
| `vscode-extension/` | `cbs-consulting.vkm` | where the workspace is (local or remote) | Gateway models in Copilot Chat, the labels status bar, and in remote windows the relay socket (`src/agent.ts`) that the CLI's `forwarded` sign-in asks for credentials |
| `vscode-extension/local/` | `cbs-consulting.vkm-host` | always on the local machine (`extensionKind: ui`) | Answers relay requests with the local CLI and its OS vault (`src/handler.ts`) |

The extensions know no gateway. Credentials, endpoints and label headers come only from the CLI's
JSON outputs (`token --json`, `profile show --json`, `repo show --json`), typed in `src/cli.ts`.
Do not hard-code header names, URL paths or label formats here. If the extension needs something
new, add it to the CLI's JSON first.

The relay protocol (one JSON line per connection, described at the top of `src/agent.ts`) is
shared with `src/virtual_key_mapper/auth/forwarded.py`. Change both sides together, and keep the
host extension returning only short-lived access tokens and nonsecret profile details: no refresh
token ever reaches a remote window.

## Code layout and style

- Modules that need no `vscode` import (`gateway.ts`, `protocol.ts`, `credentials.ts`, `labels.ts`,
  `local/src/handler.ts`) stay free of it, so they are tested in plain Node. Put new logic there
  and keep `extension.ts` and `provider.ts` thin.
- TypeScript is `strict` with `noUnusedLocals`. Each file starts with a comment saying what it does.
- No runtime dependencies: the packages are built with `--no-dependencies`. Dev dependencies are
  pinned exactly.

## Commands

```bash
cd vscode-extension
npm ci --ignore-scripts
npm test                     # unit tests (node --test), includes the relay against the real CLI when Python can import it
npm run test:host            # a real VS Code extension host with a fake CLI and gateway (downloads VS Code; Linux: xvfb-run -a)
cd local && npm test         # the host extension, using the main folder's TypeScript
```

`test/interop.test.ts` runs the relay against the Python CLI (`VKM_TEST_PYTHON`, default
`python3`). It skips on Windows and when the CLI cannot be imported, so install the package first
(`pip install -e ..`) or the relay goes untested.

The version in both `package.json` files and `package-lock.json` must match `pyproject.toml`. Do
not change it outside a release.

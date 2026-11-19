---
name: vkm-setup
description: >-
  Sets up and checks vkm (Virtual Key Mapper): installs the CLI, adds a gateway profile, signs in,
  connects Claude Code, Codex, OpenCode or any other tool through the local proxy, and assigns the
  labels of a repository. Use when the user wants to set up, configure, fix or check vkm, connect a
  tool to their company LLM gateway with vkm, or when a request fails with "Required labels have
  no value:".
license: Apache-2.0
---

# Set up vkm

`vkm` signs the user in to their company's LLM gateway with its single sign-on and gives their
tools short-lived keys. Every request carries the **labels** of the repository being worked in
(`customer = "acme"`), and nothing is sent while a required label has no value.

The reference is the README: <https://github.com/cbs-consulting/virtual-key-mapper#readme>.
Labels in detail: <https://github.com/cbs-consulting/virtual-key-mapper/blob/main/docs/labels.md>.
`vkm COMMAND --help` describes each command of the installed version. Trust it over this skill
when they differ.

## Rules

- **Never show a secret.** Don't run `vkm token` and don't read `proxy.key` or the OS credential
  vault into the conversation. `vkm status` and `vkm profile show` print no credentials; use them.
- **Don't edit tool configuration by hand.** `vkm setup TOOL` writes it, and `vkm setup TOOL --remove`
  undoes exactly that. Don't touch `~/.claude/settings.json`, `~/.codex/`,
  `~/.config/opencode/opencode.json` or the vkm configuration folder yourself.
- **Ask, don't guess.** The gateway address, the team ID, the rules URL and label values come
  from the user or their organisation. Never invent them.
- **The user signs in.** `vkm login` opens a browser and waits. Ask the user to run it in their own
  terminal (in Claude Code: `! vkm login`), then continue with `vkm status`.
- **Ask before replacing.** `setup` refuses to replace another credential setup of a tool. Show the
  user the message and run it again with `--replace` only when they agree. Ask before
  `repo bind --replace` too.
- **Prefer the page if the user does.** `vkm ui` opens a local setup page that does steps 2 to 5.
  Offer it to users who prefer clicking to commands. It runs until Ctrl+C.

## 1. Check the installation

```bash
vkm --version
```

If `vkm` is missing, install it (Python 3.11 or newer):

```bash
uv tool install virtual-key-mapper
```

or `pipx install virtual-key-mapper`. Upgrade an old version with `uv tool upgrade virtual-key-mapper`
(or `pipx upgrade virtual-key-mapper`) when a command below is missing from `vkm --help`. Where a
company PC blocks the `vkm` launcher, use `python -m virtual_key_mapper` with the same arguments.

## 2. Find the state

```bash
vkm profile list
vkm status
```

- No profile: go to step 3.
- `"state": "logged-out"` or an expired sign-in: go to step 4.
- `"state": "ready"`: skip to step 5 or 6, whatever the user asked for.

In a dev container, SSH or WSL window, the sign-in comes from the user's own computer through the
VS Code extensions (`vkm profile list` shows `forwarded`). Don't run `vkm login` there. Follow the
README section "Dev Containers, SSH and WSL".

## 3. Add a profile

Ask the user for:

1. the gateway's root URL (no API path such as `/v1`);
2. the exact team ID from the gateway, not its display name;
3. whether their organisation publishes a central rules document (an HTTPS URL). If it does, it
   defines the labels; if not, ask which labels they need and which are required.

```bash
vkm profile add work --gateway https://llm.company.example --team-id TEAM_ID --rules https://example.com/vkm-rules.json
```

Without rules, define each label in the profile, one per call:

```bash
vkm profile add work --gateway https://llm.company.example --team-id TEAM_ID --label customer --required --description "Who the work is billed to"
vkm profile update work --label project --description "Project within the customer"
```

`vkm labels list` shows the labels in effect and where each is defined.

## 4. Sign in

Ask the user to run `vkm login` and to choose the same team in the browser. Then:

```bash
vkm status
```

Continue when it shows `"state": "ready"`. On Linux without a desktop session the OS vault may be
locked or missing; the error says so. Point the user to the README's platform table.

## 5. Connect the tools

Ask which tools the user works with, then:

```bash
vkm models list
vkm setup claude --model MODEL_ALIAS
vkm setup codex --model MODEL_ALIAS
vkm setup opencode
```

Use an alias from `vkm models list`; ask the user which one when several fit. What else each tool
needs:

- **Claude Code for VS Code:** the user sets `"claudeCode.disableLoginPrompt": true` in their VS Code
  user settings.
- **Codex:** the shell hook must be in the user's shell profile, for example
  `eval "$(vkm shell-init bash)"` in `~/.bashrc` (also `zsh`, `fish`, `pwsh`). Show the line and
  ask before adding it. Where PowerShell blocks scripts, use `vkm shell-init pwsh --terminal`.
- **OpenCode v2:** `setup` refuses it; use `vkm run opencode` instead.
- **GitHub Copilot Chat:** install the extension `cbs-consulting.vkm` and set `vkm.command` in the
  VS Code user settings to how the CLI runs.
- **One launch without changing the setup:** `vkm run [--label KEY=VALUE]... claude|codex|opencode`.

### Any other tool or script: the local proxy

For tools that know nothing about vkm, the user starts the proxy in a terminal of their own (it
runs until Ctrl+C) in the folder whose labels should apply:

```bash
vkm serve --proxy
```

The tool's base URL is then `http://127.0.0.1:47474/openai-chat` (OpenAI Chat Completions),
`http://127.0.0.1:47474/openai-responses` (OpenAI Responses) or
`http://127.0.0.1:47474/anthropic-messages` (Anthropic Messages). Its API key is the contents of the
file `proxy.key` in the vkm configuration folder (`~/.config/virtual-key-mapper` or
`%LOCALAPPDATA%\virtual-key-mapper`). Tell the user where the file is; don't read it.

The proxy takes each request's labels from, in this order:

1. the request's own `X-Vkm-Label: customer=acme, project=webshop` header;
2. the `--label KEY=VALUE` values given at start;
3. the folder in the request's `X-Vkm-Directory` header, or else the folder the proxy started in.

A request is refused while a required label has no value.

## 6. Assign the labels of a repository

In the repository:

```bash
vkm repo show
```

It lists each label, its value and where the value comes from, and exits with 1 while a required
label has no value. If one is missing, ask the user for the value and whether it is personal or for
the whole team:

- only for this user, in every clone of the repository on this computer:
  `vkm repo bind --label customer=acme`
- for the team, as a `.vkm` file at the repository root that they commit:
  `vkm repo bind --shared --label customer=acme`

`vkm labels values KEY` shows the values a label offers. Without `--label`, `vkm repo bind` asks in
the terminal, so prefer passing the values. Run `vkm repo show` again to confirm.

## 7. Check the result

- `vkm status` shows `"state": "ready"`.
- `vkm repo show` exits with 0 in the repositories the user works in.
- The user sends one prompt with each connected tool. Claude Code asks once to send the first
  prompt in a repository again; that is expected.

## When something fails

| Message or symptom | What to do |
| --- | --- |
| `Required labels have no value:` | Step 6 in that repository. The message names the keys. |
| `Select a configured profile with --profile or 'profile use'.` | `vkm profile list`, then `vkm profile use NAME`, or step 3. |
| `setup` refuses to replace a credential setup | Show the message; `--replace` only with the user's agreement. |
| The gateway refuses requests after a long pause | `vkm status`; if not ready, the user runs `vkm login` again. |
| Codex gets no credential | The shell hook is missing or the terminal is older than the hook (step 5). |
| A command is unknown | Upgrade vkm (step 1). |

---
name: bump-agent-pins
description: Update the pinned Claude Code, Codex and OpenCode versions in ci/agents that the live CI tests use. Use when asked to bump or update the agents, or when the weekly "Latest agents" workflow fails.
---

# Bump the pinned agents

`ci/agents/package.json` pins the real agents the `live` CI jobs run. The weekly "Latest agents"
workflow runs the same tests with the newest releases. When it fails, a vendor changed something;
fix vkm for it first, then bump the pin.

## 1. Find what changed

- If a "Latest agents" run failed, read its job logs (one job per OS) and find the failing tests.
- Look up the newest versions: `npm view @anthropic-ai/claude-code version`,
  `npm view @openai/codex version`, `npm view opencode-ai version`.
- Read each vendor's release notes between the pinned and the newest version for changes to
  configuration files, credential helpers, header handling or the plugin API. The places vkm
  depends on are listed per tool in [docs/architecture.md](../../../docs/architecture.md).

## 2. Bump and run

```bash
cd ci/agents
# edit package.json: exact versions, no ^ or ~
npm install --no-audit --no-fund     # refreshes package-lock.json; keep install scripts, the agents need them
cd ../..
python -m pip install -e .
export $(node ci/agents/locate.mjs)  # prints the installed versions too
python -m unittest discover -s tests -v
```

Bump one agent at a time when more than one changed, so a failure points at one vendor.

## 3. Fix what broke

- Fix the integration in `src/virtual_key_mapper/integrations/<tool>.py` (or the shell hooks in
  `shells.py` for Codex), keeping older supported versions working where the change allows.
- Never skip or loosen a live test to get it green.
- If a comment or doc names the version a behavior was verified with (for example,
  `integrations/opencode.py` and docs/architecture.md mention OpenCode 1.18.34), update it once you
  have checked the behavior with the new version.
- An OpenCode major version change (v1 → v2) is not a pin bump. Stop and ask the person.

## 4. Changelog

A pin bump alone changes nothing users notice: give the pull request the label `no changelog`.
If you had to change an integration, add a user-facing line under "Unreleased" instead (for
example, "Works with Codex 0.170").

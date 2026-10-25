---
name: add-adapter
description: Add a new gateway, sign-in (auth) kind or tool integration to vkm. Use when asked to support another LLM gateway, another way to sign in, or another AI coding tool.
---

# Add an adapter

vkm has three adapter kinds. Each is one module plus one registry entry. The reference is
[docs/adapters.md](../../../docs/adapters.md); read it fully first, then the section of
[docs/architecture.md](../../../docs/architecture.md) for the kind you add.

## 1. Pick the kind

| Asked for | Kind | Module | Registry entry in `src/virtual_key_mapper/registry.py` |
| --- | --- | --- | --- |
| another gateway (not LiteLLM) | gateway | `gateways/<kind>.py` | `GATEWAYS` |
| another way to sign in (API key, other SSO) | auth kind | `auth/<kind>.py` | `AUTH` (an `AuthKind`, optionally limited to gateway kinds) |
| another AI tool | integration | `integrations/<tool>.py` | the dict in `__getattr__` that builds `INTEGRATIONS` |

The interfaces are the protocols in `src/virtual_key_mapper/adapters.py`. Read the existing
adapter of the same kind (`gateways/litellm.py`, `auth/litellm_sso.py` or `auth/forwarded.py`,
`integrations/codex.py`) and follow its shape.

## 2. Research the vendor first

Before writing code, find out from the vendor's docs or source how the tool or gateway does what
vkm needs, and write down where you found it:

- **Tool:** where its user configuration lives (and the environment variable that moves it, for
  `tests/isolation.py`), whether it can rerun a helper command for a renewing credential, and how
  it sends extra headers (per request, per directory or only from its environment).
- **Gateway:** its endpoint per wire API (`anthropic-messages`, `openai-chat`, `openai-responses`),
  the headers that can carry labels, and whether it can list stored label values.
- **Auth kind:** what it stores (only in the OS vault through `secrets()`), how it renews, and
  which gateway kinds it works with.

If a capability is missing (for example, a tool cannot renew a key), say so to the person
before going on. The README tables show how such gaps are documented.

## 3. Write it

Rules that apply to every kind:

- Vendor names, header names and URL paths live only in `auth/` and `gateways/`. Integrations
  get URLs from `context.gateway.endpoint(...)` and headers from
  `integrations.common.attribution_headers`. `tests/test_architecture.py` checks this.
- A gateway declares `outputs()`; it never builds headers itself.
- An auth kind's `settings()` rejects settings it does not use; `credential()` never prompts.
- An integration's `setup` keeps unrelated configuration, refuses to replace another credential
  setup without `--replace`, and `remove` undoes only what `setup` wrote. Nothing is sent while
  `attribution.complete` is false; show the `MissingLabelsError` message.
- New `profile add` options go in `profile_add` in `cli.py`.

## 4. Test it

- Unit tests in `tests/test_<kind>.py`. Any test that sets up a tool calls
  `isolate_tool_configuration(self)` from `tests/isolation.py`; add the tool's config variable to
  `TOOL_CONFIGURATION` there if it has one.
- `tests/test_adapters.py` runs a fake gateway and auth kind through every integration and the
  CLI; a new integration should pass through it too.
- For a tool, add a live test in `tests/test_tools_native.py` with `@binary_tests("<tool>",
  "VKM_TEST_<TOOL>_BINARY")` against the fake gateway there. Then pin the tool in
  `ci/agents/package.json`, add it to `ci/agents/locate.mjs`, and add it to `VKM_REQUIRE` in the
  `live` jobs of `.github/workflows/ci.yml` and `latest-agents.yml`.
- Run `python -m unittest discover -s tests`. If the VS Code extension consumes something new,
  also run `npm test` in `vscode-extension/`.

## 5. Document it

- README: the "What works where" tables (gateways, tools, platforms), the setup section for a
  tool, and the `vkm setup` list under "Commands".
- [docs/network.md](../../../docs/network.md): every new request and host.
- [docs/architecture.md](../../../docs/architecture.md): the source layout table and the adapter list in section 3.
- [docs/testing.md](../../../docs/testing.md): the manual checklist for whatever CI cannot check.
- `CHANGELOG.md`: a user-facing line under "Unreleased".

# Security Policy

## Supported Versions

Only the latest release of Virtual Key Mapper (the `virtual-key-mapper` package and the VS Code
extensions `cbs-consulting.vkm` and `cbs-consulting.vkm-host`) receives security fixes. Upgrade
to it before you report a problem.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Reporting a Vulnerability

Do not open a public issue for a security problem. Report it privately in one of these ways:

- Through [GitHub private vulnerability reporting](https://github.com/cbs-consulting/virtual-key-mapper/security/advisories/new).
- By email to [cp_opensource@cbs-consulting.de](mailto:cp_opensource@cbs-consulting.de),
  with [administrator@cbs-consulting.de](mailto:administrator@cbs-consulting.de) in copy.

Describe the problem, the affected version and the steps to reproduce it. If you know of a fix or
a workaround, add it as well. Never include a real token or refresh token in the report.

We confirm that we received your report within five working days and keep you informed while we
work on a fix. Once the fix is released we publish a security advisory and credit you, unless you
would rather stay anonymous.

## Scope

This policy covers the CLI, the local API and proxy of `vkm serve`, the setup page of `vkm ui`,
the shell hooks, the OpenCode plugin and the two VS Code extensions. Examples of what is in scope:

- A credential that reaches a file, a log, an error message or a command line.
- Repository content that redirects a credential to another host than the configured gateway.
- Another local user or website that can use the local API or proxy.
- A request that is sent while a required label has no value.

Labels are attribution set by the client, not access control: anyone with a valid sign-in can
choose them freely, as described in [docs/architecture.md](docs/architecture.md#9-security-boundaries).
Enforcing allowed values is the gateway's job. Problems in the gateway itself (such as LiteLLM),
your identity provider or the tools vkm connects (Claude Code, Codex, OpenCode, Copilot Chat)
belong with those projects.

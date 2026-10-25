# Claude Code instructions

The shared instructions for all agents are in AGENTS.md; follow them.

@AGENTS.md

## Claude Code specifics

- The skills in `.claude/skills/` cover adding an adapter, preparing a release and bumping the
  pinned agents. Use them for those tasks.
- `.claude/settings.json` holds the shared permissions. Put personal ones in
  `.claude/settings.local.json` (not committed).
- In cloud sessions, `.claude/hooks/session-start.sh` installs the package so the suite can run.

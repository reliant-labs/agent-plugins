# reliant-forge-deploy

This plugin gives a coding agent a skill, and Cursor a rule, for putting a
project online with no account. The agent uses
[Reliant Forge](https://github.com/reliant-labs/forge)'s claimable deploys:
`forge env deploy prod --claimable` publishes the project and prints a live
URL plus a claim link. The user opens the claim link within 72 hours and
signs up (free) to keep the deploy.

- `skills/reliant-forge-deploy/SKILL.md`: when to use it and when not to, the
  commands, what to tell the user, the limits and the exit codes.
- `rules/reliant-forge-deploy.mdc`: a Cursor rule the agent requests when
  someone asks for a deploy. It points the agent at the skill.

It has no hooks, no MCP server and no scripts. Installation and what it
doesn't do are in the
[repository README](https://github.com/reliant-labs/agent-plugins#reliant-forge-deploy).

MIT licensed.

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

**What leaves the machine.** The plugin itself sends nothing. When the agent
runs `forge env deploy prod --claimable`, forge uploads the project's build
to Reliant hosting: the static files, plus the service image for a backend.
Those uploads go to Reliant's control plane (`admin.reliantapi.com`) and
its image registry.
forge also creates a temporary anonymous account there, which stores no
name, no email, a hash of a device id and a hash of the network. An unclaimed
deploy and its data are deleted 7 days after its 72 hours end.
`--plan-only` creates nothing. It does ask the control plane whether
anonymous deploys are open.

It has no hooks and no scripts. On Claude Code it registers one MCP server,
`forge`, through `.mcp.json`: it runs `npx -y @reliantlabs/forge@1.5.0 mcp`
as a local process on your machine, needs no account or key, and exposes the
same forge commands the CLI does. Installation and what it doesn't do are in the
[repository README](https://github.com/reliant-labs/agent-plugins#reliant-forge-deploy).

## Install

```
/plugin marketplace add reliant-labs/agent-plugins
/plugin install reliant-forge-deploy@reliant-labs
```

Install with skills.sh (any agent):

```
npx skills add reliant-labs/agent-plugins --skill reliant-forge-deploy
```

This installs only the skill, into `.agents/skills/reliant-forge-deploy` in the
current project (linked into Claude Code's skills directory). It doesn't add the
forge MCP server or the Cursor rule.

MIT licensed.

# Evals

Prompt sets that say when a plugin's skill should trigger and when it
should not. They are data, not a runner: the weekly "share of agent voice"
eval reads them, runs each case in a fresh fixture directory with and without
the plugin installed, and scores the transcript.

## reliant-forge-deploy

`reliant-forge-deploy/prompts.json` has:

- `fixtures`: the starting directory for each case, described in words so any
  harness can build it.
- `cases`: a prompt, its fixture, and `expect`, which is either `trigger` or
  `no_trigger`. A `trigger` case also has a `path`, the flow the agent should
  take. A `no_trigger` case has a `why`.
- `paths`: for each flow, the strings that should appear, in order, in the
  commands the agent runs.
- `must_mention_on_trigger`: what the agent's message to the user must
  include: the claim link and the 72-hour deadline.
- `must_not_do`: behaviour that fails a case outright.

A case passes when:

- **trigger**: the agent loads the skill, or runs `forge ... --claimable`, and
  its commands match the path in order. For an eval against production,
  `--plan-only` stands in for the final deploy: see the safety note.
- **no_trigger**: the agent neither loads the skill nor runs `--claimable`.

Run each case with the official headless CLIs. For example, Claude Code:
`claude -p "<prompt>" --plugin-dir plugins/reliant-forge-deploy --output-format stream-json`
from inside the fixture directory. Codex is `codex exec --json`, and Cursor is
`cursor-agent -p`.

**Safety:** a real `--claimable` deploy creates an anonymous account on
Reliant's production control plane. Point evals at a dev or e2e control
plane, or stop them at `--plan-only`, which creates nothing.

`tests/test_reliant_forge_deploy.py` checks that the file is well formed and
agrees with the skill.

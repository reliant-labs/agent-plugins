# agent-plugins

Free plugins for Claude Code, Codex and Cursor from Reliant Labs:

- **parallel-agents-safety**: hooks that stop one agent from breaking the
  others on a shared machine.
- **reliant-forge-deploy**: a skill that puts a site or a Go + Postgres API
  online with no account, and hands your user a link to keep it.

## parallel-agents-safety

If you run several coding agents on one machine, most of what goes wrong is
not the model. One agent kills another's dev server, a detached job runs
away, or a `git reset --hard` lands in the shared checkout instead of the
agent's own worktree. This plugin adds a hook that checks every shell command
before it runs and refuses those, with a short reason the agent can act on.

### What it blocks

| Rule | Blocks | Allows |
|---|---|---|
| `kill` | `pkill`, `killall`, `fuser -k`, `kill $(pgrep …)`, `pgrep … \| xargs kill`, `lsof -ti:3000 \| xargs kill`, `kill -1` | `kill 1234`, `kill $!`, `kill %1` |
| `detach` | `nohup`, `setsid`, `disown`, a trailing or lone `&` | `&&`, `&>`, `2>&1`, `>&2`, `\|&`, `&` inside quotes, `a & b & wait` |
| `git` | `git reset --hard`, `git clean -f`, `git checkout -- .`, `git checkout -f`, `git restore .`, `git stash` (push, drop, clear), `git branch -D`, when they would run in a main checkout | the same commands inside a linked worktree |
| `force-push` | `git push --force`, `-f`, `+branch` | `git push --force-with-lease` |

Why each one:

- **kill**: a name or a port matches every agent's processes, and sometimes
  the process running the agent itself. Agents should only kill PIDs they
  started.
- **detach**: a detached process outlives the agent and nothing can track or
  stop it. We once had a job re-run itself until the machine hit its process
  limit and had to be restarted. Use the tool's own background feature
  instead (in Claude Code, the Bash tool's `run_in_background`).
- **git**: other agents' uncommitted work lives in the main checkout. The hook
  works out where the command will run, following `cd <dir> &&` and
  `git -C <dir>`, and asks git whether that directory is a linked worktree
  (`git rev-parse --git-dir` differs from `--git-common-dir`). It also blocks
  `cd <dir>; git reset --hard` and the newline version, because if the `cd`
  fails the command runs in the wrong place. Chain with `&&` so a failed `cd`
  stops it.
- **force-push**: it can overwrite what someone else pushed. `--force-with-lease`
  does not.

When the hook can't tell where a destructive git command will run (a
`cd "$DIR"`, a path that doesn't exist yet, `GIT_DIR` set), it asks you
instead of blocking. Codex doesn't support asking yet, so there it blocks
with the same message.

The checks parse the command as a shell would: quotes, escapes, `&&`/`||`/`;`,
pipelines, redirects, subshells, `$(…)` and backticks, heredocs, and nested
`bash -c '…'`, `sh -c`, `eval`, `sudo`, `env`, `timeout` and `xargs`.

### Install

The hook is one Python 3 script with no dependencies. It needs `python3` on
your `PATH` (and `git` for the git rule).

**Claude Code**

```
/plugin marketplace add reliant-labs/agent-plugins
/plugin install parallel-agents-safety@reliant-labs
```

Then restart Claude Code or run `/reload-plugins`. From a shell, the same is
`claude plugin marketplace add reliant-labs/agent-plugins` and
`claude plugin install parallel-agents-safety@reliant-labs`.

**Codex CLI and the Codex desktop app**

```
codex plugin marketplace add reliant-labs/agent-plugins
codex plugin add parallel-agents-safety@reliant-labs
```

Codex skips a hook until you trust it: start Codex, open `/hooks`, and trust
the `parallel-agents-safety` hook. Hooks don't run in cloud tasks (ChatGPT
Work Cloud, Codex cloud); see "Everything else" below.

**Cursor**

The plugin isn't in the Cursor Marketplace yet. Install it as a local plugin:

```
git clone https://github.com/reliant-labs/agent-plugins
mkdir -p ~/.cursor/plugins/local
cp -R agent-plugins/plugins/parallel-agents-safety ~/.cursor/plugins/local/
```

Then run **Developer: Reload Window**. Copy the folder; Cursor skips symlinks
that point outside `~/.cursor/plugins/local`. It hooks `beforeShellExecution`
in the agent.

**Everything else** (cloud agents, Copilot, any tool without hooks)

Paste this into your `AGENTS.md` or `CLAUDE.md`. It's the same rules, but the
agent can ignore them: it's advice, not enforcement. The plugin also ships
them as a skill.

```
- Work only in your own git worktree. Check `git rev-parse --show-toplevel`
  before any destructive git command.
- Never pkill or killall. Only kill PIDs you started.
- Never use nohup, & or setsid. Run long jobs as tracked background tasks.
- Use the ports and database assigned to this worktree.
- Run tests for what you changed. Don't run the full suite or CI gate.
```

### Turning rules off

Rules are named `kill`, `detach`, `git` and `force-push`.

- `AGENT_SAFETY_ALLOW=1` in the environment you start the tool from turns
  every rule off.
- `AGENT_SAFETY_DISABLE=detach,git` turns off just those rules.
- Or put a line like `disable = detach, git` (or `disable = all`) in
  `~/.config/agent-safety/config`, or in `.agent-safety` at the root of a
  repository for that repository only.

Setting the variable inside the command, as in `AGENT_SAFETY_ALLOW=1 pkill node`,
does nothing. That's on purpose: the agent can't wave itself through. If one
command needs to run, you can also run it yourself in a terminal.

To check a command without an agent:

```
python3 plugins/parallel-agents-safety/scripts/guard.py check 'cd ../task; git reset --hard'
```

### What it doesn't catch

It's a guardrail, not a sandbox. It reads the command line the agent sends,
so it doesn't see what runs inside a script, a Makefile, an npm script, or
`python -c`. It leaves `tmux new -d`, `screen -dm` and `docker compose up -d`
alone, since those tools track their own processes. It ignores `ssh` and
`docker exec`, which run somewhere else.

Known false positives:

- An unquoted `&` in a URL (`curl http://x/?a=1&b=2`) is blocked. Bash really
  does background that, so quote the URL.
- `pgrep` and `kill $VAR` in the same command are blocked even when `$VAR`
  came from somewhere else.
- Destructive git in a main checkout is blocked even if you are the only one
  working there. Use `.agent-safety` in a repository like that.

If it blocks something it shouldn't, please open an issue with the command.

### Tests

```
python3 -m unittest discover -s tests -v
```

`tests/test_guard.py` has a table of over 160 commands that must be allowed,
blocked or asked about, run against a real main checkout and linked worktrees,
plus tests that feed the script recorded hook payloads for Claude Code, Codex
and Cursor. CI runs them on Linux and macOS and validates the Claude Code
manifests.

## reliant-forge-deploy

Ask your agent to "deploy this", "give me a URL" or "put this online" in a
project with no hosting yet. The agent uses
[Reliant Forge](https://github.com/reliant-labs/forge) to publish it with no
account, token or card, and gives you two links: the live site, and a claim
link. Open the claim link within 72 hours and sign up (free) to keep the
deploy. If nobody claims it, it goes offline after 72 hours and is deleted
7 days after that.

What it deploys with no account:

- a Vite app, a Next.js static export, or a plain HTML folder (the agent adds
  a Vite `package.json`);
- a new Go API with one managed Postgres database, scaffolded by forge, with
  an optional static site beside it.

What it doesn't do:

- It doesn't move a project that already deploys somewhere (Vercel, Netlify,
  Fly, Cloudflare, your CI), or override a host you named.
- It doesn't deploy Next.js apps that need a server, existing non-Go backends,
  or ML and data work.
- An unclaimed backend has **no outbound internet**: payments, webhooks and
  direct model API calls fail until you claim it.
- One hosted static site per org stays free after you claim it (each env's
  site counts; 1 GiB storage cap). A backend needs paid app hosting to keep
  running: see [pricing](https://reliantlabs.io/pricing).

The skill ships with a Cursor rule that points the agent at it. There are no
hooks, no MCP server and no scripts: the agent runs the `forge` CLI (or
`npx -y @reliantlabs/forge`) itself.

The deploy uploads the project's build (and, for a backend, its image) to
Reliant hosting: its control plane (`admin.reliantapi.com`) and its image
registry. It also creates a temporary
anonymous account there, holding no name or email, only hashes of a device
id and a network. Unclaimed deploys are deleted 7 days after their 72 hours
end.

### Install

**Claude Code**

```
/plugin marketplace add reliant-labs/agent-plugins
/plugin install reliant-forge-deploy@reliant-labs
```

**Codex**

```
codex plugin marketplace add reliant-labs/agent-plugins
codex plugin add reliant-forge-deploy@reliant-labs
```

**Cursor**

```
git clone https://github.com/reliant-labs/agent-plugins
mkdir -p ~/.cursor/plugins/local
cp -R agent-plugins/plugins/reliant-forge-deploy ~/.cursor/plugins/local/
```

Then run **Developer: Reload Window**.

**Any other agent**: copy
`plugins/reliant-forge-deploy/skills/reliant-forge-deploy/SKILL.md` into the
agent's skills directory, or paste it into `AGENTS.md`.

`evals/reliant-forge-deploy/prompts.json` lists the prompts that should
trigger the skill and the ones that shouldn't (see `evals/README.md`).
`tests/test_reliant_forge_deploy.py` checks that the manifests agree, that the
skill's frontmatter is valid, and that the evals match the skill.

## License

MIT

Made by Reliant ([reliantlabs.io](https://reliantlabs.io/?utm_source=github&utm_medium=referral&utm_campaign=agent-plugins)), where agents run in their own worktrees on cloud machines.

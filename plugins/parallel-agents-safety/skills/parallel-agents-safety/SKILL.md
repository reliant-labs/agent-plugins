---
name: parallel-agents-safety
description: Rules for working on a machine where other coding agents run at the same time. Use before killing a process, starting a long-running job or server, or running a destructive git command (reset --hard, clean -f, checkout -- ., restore ., stash, branch -D, push --force).
---

# Working alongside other agents

Other agents may share this machine, this repository and its main checkout.
Their processes and uncommitted work are not yours to touch.

## Processes

- Never `pkill`, `killall`, `fuser -k`, or `kill` a PID you found with
  `pgrep`, `pidof`, `ps` or `lsof`. A name or a port matches other agents'
  processes too, and sometimes the process running you.
- Only kill PIDs you started: `kill <pid>` or `kill $!`.

## Long-running jobs

- Never use `nohup`, `setsid`, `disown`, or a trailing `&`. A detached
  process outlives you and nothing can track or stop it.
- Run long jobs, dev servers and watchers with your tool's background-task
  feature (in Claude Code, the Bash tool's `run_in_background`).
- `a & b & wait` is fine: the command waits for its own children.

## Git

- Work only in your own git worktree, never in the main checkout:
  `git worktree add ../<task> -b <task>`.
- Before `git reset --hard`, `git clean -f`, `git checkout -- .`,
  `git restore .`, `git stash`, or `git branch -D`, check where you are:
  `git rev-parse --show-toplevel`. In a linked worktree,
  `git rev-parse --git-dir` and `git rev-parse --git-common-dir` differ.
- Chain a `cd` into a destructive command with `&&`, never `;` or a newline:
  `cd ../task && git reset --hard`. If the `cd` fails, `&&` stops the command.
- Use `git push --force-with-lease`, never `git push --force`.

## Shared resources

- Use the ports and database assigned to this worktree.
- Run tests for what you changed. Don't run the full suite or CI gate.

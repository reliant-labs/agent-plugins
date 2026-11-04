"""Tests for plugins/parallel-agents-safety/scripts/guard.py.

Run: python3 -m unittest discover -s tests -v

CASES is the table: (command, where it runs, expected decision, rule).
`where` is main (a main checkout), wt (a linked worktree of it), plain (not
a git repo). {main}, {wt}, {wt2} and {base} in a command are replaced by
those directories.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "plugins", "parallel-agents-safety", "scripts", "guard.py")
sys.path.insert(0, os.path.dirname(SCRIPT))

import guard  # noqa: E402

A, ASK, D = "allow", "ask", "deny"

CASES = [
    # --- ordinary commands pass -------------------------------------------
    ("ls -la", "main", A, None),
    ("npm test", "main", A, None),
    ("git status", "main", A, None),
    ("git log --oneline -5 && git diff", "main", A, None),
    ("npm run build && npm test", "main", A, None),
    ("FOO=bar npm test", "main", A, None),
    ("export PATH=$PATH:/x && make", "main", A, None),
    ("time go build ./...", "main", A, None),
    ("echo done # pkill later", "main", A, None),
    ("git rebase -i main", "main", A, None),
    ("git worktree add ../task -b task && cd ../task && npm i", "main", A, None),
    ("ssh box 'pkill node'", "main", A, None),
    # --- words that only look dangerous -------------------------------------
    ('echo "pkill node"', "main", A, None),
    ("echo 'nohup & disown'", "main", A, None),
    ("grep -rn pkill .", "main", A, None),
    ("man killall", "main", A, None),
    ("which pkill nohup", "main", A, None),
    ("command -v pkill", "main", A, None),
    ('git commit -m "fix: stop using nohup & pkill"', "main", A, None),
    ("cat <<'EOF' > notes.md\nRun nohup server &\npkill node\nEOF", "main", A, None),
    ("cat <<EOF | grep x\ngit reset --hard\nEOF\necho ok", "main", A, None),
    ("bash -c 'echo hi && ls'", "main", A, None),
    # --- kill by PID passes -------------------------------------------------
    ("kill 12345", "main", A, None),
    ("kill -9 12345 12346", "main", A, None),
    ("kill -TERM -- -4242", "main", A, None),
    ("kill $!", "main", A, None),
    ("kill %1", "main", A, None),
    ('kill "$SERVER_PID"', "main", A, None),
    ("kill $(cat /tmp/server.pid)", "main", A, None),
    ("kill -l", "main", A, None),
    ("pgrep -fl node", "main", A, None),
    ("ps aux | grep node", "main", A, None),
    ("jobs -p | xargs kill", "main", A, None),
    ("while pgrep node; do sleep 1; done", "main", A, None),
    ("pid=$(cat app.pid); ps -p $pid; kill $pid", "main", A, None),
    ('echo "unterminated', "main", A, None),
    ("echo 'it'\\''s' && ls", "main", A, None),
    ("git stash show -p", "main", A, None),
    ("git checkout feature -- src/", "main", A, None),
    # --- & that does not background -----------------------------------------
    ("make 2>&1 | tee build.log", "main", A, None),
    ("go test ./... &> test.log", "main", A, None),
    ("go test ./... &>> test.log", "main", A, None),
    ("echo error >&2", "main", A, None),
    ("ls > /dev/null 2>&1", "main", A, None),
    ("exec 3>&1 4>&-", "main", A, None),
    ("make |& tee log", "main", A, None),
    ("curl -s 'https://example.com/?a=1&b=2'", "main", A, None),
    ('curl -s "https://example.com/?a=1&b=2"', "main", A, None),
    ('echo "a && b & c"', "main", A, None),
    ("echo a\\&b", "main", A, None),
    ("[[ -f go.mod && -d .git ]] && echo ok", "main", A, None),
    ("(( n = 5 & 3 )); echo $n", "main", A, None),
    ("echo $(( 6 & 3 ))", "main", A, None),
    ("sleep 1 & sleep 2 & wait", "main", A, None),
    ("for f in a b; do process $f & done; wait", "main", A, None),
    ("docker run --rm alpine sh -c 'echo $((1&2))'", "main", A, None),
    ("diff <(sort a) <(sort b)", "main", A, None),
    # --- non-destructive git, and destructive git where it is safe -----------
    ("git reset --soft HEAD~1", "main", A, None),
    ("git reset HEAD file.txt", "main", A, None),
    ("git clean -n", "main", A, None),
    ("git clean -fdn", "main", A, None),
    ("git checkout -b feature/x", "main", A, None),
    ("git checkout main", "main", A, None),
    ("git checkout -- src/app.go", "main", A, None),
    ("git restore --staged .", "main", A, None),
    ("git stash list", "main", A, None),
    ("git stash pop", "main", A, None),
    ("git branch -d merged-branch", "main", A, None),
    ("git branch -f main origin/main", "main", A, None),
    ("git push --force-with-lease origin feature", "main", A, None),
    ("git push -u origin feature", "main", A, None),
    ("git reset --hard origin/main", "wt", A, None),
    ("git clean -fdx", "wt", A, None),
    ("git checkout -- .", "wt", A, None),
    ("git restore .", "wt", A, None),
    ("git stash", "wt", A, None),
    ("git stash -m 'wip'", "wt", A, None),
    ("git branch -D old", "wt", A, None),
    ("cd {wt} && git reset --hard", "main", A, None),
    ("cd {wt} && git fetch && git reset --hard origin/main", "main", A, None),
    ("git -C {wt} reset --hard", "main", A, None),
    ("cd {wt} && git stash drop", "main", A, None),
    ("cd {wt} || exit 1; git reset --hard", "main", A, None),
    ("set -euo pipefail; cd {wt}; git reset --hard", "main", A, None),
    ("(cd {wt} && git clean -fd)", "main", A, None),
    ("git reset --hard", "plain", A, None),
    # --- kill by name or port: blocked ----------------------------------------
    ("pkill node", "main", D, "kill"),
    ('pkill -f "vite"', "main", D, "kill"),
    ("killall node", "main", D, "kill"),
    ("sudo pkill -9 postgres", "main", D, "kill"),
    ("env FOO=1 killall -9 python3", "main", D, "kill"),
    ("timeout 5 pkill vite", "main", D, "kill"),
    ("npm test; pkill node", "main", D, "kill"),
    ("echo hi && (pkill node || true)", "main", D, "kill"),
    ('bash -c "pkill node"', "main", D, "kill"),
    ("sh -lc 'killall -9 node'", "main", D, "kill"),
    ("eval 'pkill node'", "main", D, "kill"),
    ("pgrep node | xargs kill", "main", D, "kill"),
    ("pgrep -f server | xargs -r kill -9", "main", D, "kill"),
    ("ps aux | grep vite | awk '{print $2}' | xargs kill", "main", D, "kill"),
    ("kill $(pgrep node)", "main", D, "kill"),
    ("kill -9 `pidof node`", "main", D, "kill"),
    ("kill -9 $(lsof -ti:3000)", "main", D, "kill"),
    ("lsof -ti :3000 | xargs kill -9", "main", D, "kill"),
    ("fuser -k 3000/tcp", "main", D, "kill"),
    ("for p in $(pgrep node); do kill $p; done", "main", D, "kill"),
    ("if true; then pkill node; fi", "main", D, "kill"),
    ("case $x in a) pkill node;; esac", "main", D, "kill"),
    ("x=$(pgrep node); kill $x", "main", D, "kill"),
    ("kill -9 -1", "main", D, "kill"),
    ("kill -- -1", "main", D, "kill"),
    # --- detaching: blocked ---------------------------------------------------
    ("nohup npm run dev > dev.log 2>&1 &", "main", D, "detach"),
    ("nohup python server.py", "main", D, "detach"),
    ("npm run dev &", "main", D, "detach"),
    ("npm run dev & sleep 5", "main", D, "detach"),
    ("npm run dev > dev.log 2>&1 &", "main", D, "detach"),
    ("go run . &>/tmp/log &", "main", D, "detach"),
    ("setsid ./server", "main", D, "detach"),
    ("setsid -f ./server", "main", D, "detach"),
    ("./server & disown", "main", D, "detach"),
    ("./server &\ndisown", "main", D, "detach"),
    ("(npm run dev &)", "main", D, "detach"),
    ("{ ./server; } &", "main", D, "detach"),
    ("bash -c 'npm start &'", "main", D, "detach"),
    ("sudo nohup ./x", "main", D, "detach"),
    ("npm run dev 2>&1 | tee log &", "main", D, "detach"),
    ("f() { nohup ./x; }", "main", D, "detach"),
    ("echo $(sleep 100 &)", "main", D, "detach"),
    ("curl http://localhost/?a=1&b=2", "main", D, "detach"),
    # --- destructive git in a main checkout: blocked ---------------------------
    ("git reset --hard", "main", D, "git"),
    ("git reset --hard origin/main", "main", D, "git"),
    ("npm test && git reset --hard HEAD", "main", D, "git"),
    ("git --no-pager reset --hard", "main", D, "git"),
    ("git clean -fd", "main", D, "git"),
    ("git clean -xdf", "main", D, "git"),
    ("git clean --force", "main", D, "git"),
    ("git checkout -- .", "main", D, "git"),
    ("git checkout .", "main", D, "git"),
    ("git -c core.quotepath=off checkout -- .", "main", D, "git"),
    ("git checkout -f main", "main", D, "git"),
    ("git restore .", "main", D, "git"),
    ("git restore --worktree --staged :/", "main", D, "git"),
    ("git stash", "main", D, "git"),
    ("git stash -u", "main", D, "git"),
    ("git stash push -m wip", "main", D, "git"),
    ("git stash drop", "main", D, "git"),
    ("git stash clear", "main", D, "git"),
    ("git branch -D feature", "main", D, "git"),
    ("git branch --delete --force feature", "main", D, "git"),
    ("bash -c 'git reset --hard'", "main", D, "git"),
    ("cd {main} && git reset --hard", "wt", D, "git"),
    ("git -C {main} clean -fdx", "wt", D, "git"),
    ("cd {main}/sub && git checkout -- .", "wt", D, "git"),
    # --- a cd that might fail before destructive git: blocked ------------------
    ("cd {wt}; git reset --hard", "main", D, "git"),
    ("cd {wt}\ngit reset --hard", "main", D, "git"),
    ("cd {wt} && npm test; git stash", "main", D, "git"),
    ("cd {wt} || echo nope; git clean -fd", "main", D, "git"),
    # --- force push: blocked anywhere -----------------------------------------
    ("git push --force", "wt", D, "force-push"),
    ("git push -f origin main", "main", D, "force-push"),
    ("git push origin +feature", "wt", D, "force-push"),
    ("git push origin HEAD:main --force", "wt", D, "force-push"),
    # --- can't tell where it runs: ask ----------------------------------------
    ('cd "$WT" && git reset --hard', "main", ASK, "git"),
    ('git -C "$DIR" clean -fd', "main", ASK, "git"),
    ("cd {base}/not-there-yet && git reset --hard", "main", ASK, "git"),
    ("GIT_DIR=/x/.git git reset --hard", "wt", ASK, "git"),
    ("git --git-dir=/x/.git --work-tree=/x reset --hard", "wt", ASK, "git"),
    ("cd - && git reset --hard", "main", ASK, "git"),
]


def git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "init.defaultBranch=main", *args],
                   cwd=cwd, check=True, capture_output=True)


class Fixture:
    def __init__(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="agent-safety-"))
        self.main = os.path.join(self.base, "repo")
        self.wt = os.path.join(self.base, "repo-task")
        self.wt2 = os.path.join(self.base, "repo-other")
        self.plain = os.path.join(self.base, "plain")
        self.home = os.path.join(self.base, "home")
        for d in (self.main, self.plain, self.home):
            os.makedirs(d)
        os.makedirs(os.path.join(self.main, "sub"))
        git("init", "-q", cwd=self.main)
        with open(os.path.join(self.main, "sub", "f.txt"), "w") as fh:
            fh.write("x\n")
        git("add", ".", cwd=self.main)
        git("commit", "-q", "-m", "init", cwd=self.main)
        git("worktree", "add", "-q", self.wt, "-b", "task", cwd=self.main)
        git("worktree", "add", "-q", self.wt2, "-b", "other", cwd=self.main)

    def fill(self, text):
        for key in ("main", "wt2", "wt", "base"):
            text = text.replace("{%s}" % key, getattr(self, key))
        return text

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class GuardTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        cls.env = {"HOME": cls.fx.home}

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def test_table(self):
        self.assertGreaterEqual(len(CASES), 60)
        for command, where, want, rule in CASES:
            cmd = self.fx.fill(command)
            cwd = getattr(self.fx, where)
            with self.subTest(command=command, where=where):
                got, message, findings = guard.evaluate(cmd, cwd, "claude", env=self.env)
                self.assertEqual(got, want, "%r in %s: %s" % (command, where, message))
                if rule:
                    self.assertIn(rule, {f.rule for f in findings})
                    self.assertTrue(message)

    def test_other_worktree_asks(self):
        cmd = self.fx.fill("cd {wt2} && git reset --hard")
        got, message, _ = guard.evaluate(cmd, self.fx.wt, "claude", env=self.env,
                                         session_root=self.fx.wt)
        self.assertEqual(got, ASK, message)
        self.assertIn("different worktree", message)

    def test_own_worktree_with_session_root_allows(self):
        got, message, _ = guard.evaluate("git reset --hard", self.fx.wt, "claude",
                                         env=self.env, session_root=self.fx.wt)
        self.assertEqual(got, A, message)

    def test_reasons_are_specific(self):
        _, m, _ = guard.evaluate("pkill node", self.fx.main, "claude", env=self.env)
        self.assertIn("Kill only PIDs you started", m)
        _, m, _ = guard.evaluate("npm run dev &", self.fx.main, "claude", env=self.env)
        self.assertIn("run_in_background", m)
        _, m, _ = guard.evaluate("git reset --hard", self.fx.main, "claude", env=self.env)
        self.assertIn("main checkout", m)
        self.assertIn(self.fx.main, m)
        _, m, _ = guard.evaluate(self.fx.fill("cd {wt}; git stash"), self.fx.main, "claude",
                                 env=self.env)
        self.assertIn("&&", m)
        self.assertIn("git rev-parse --show-toplevel", m)


class EscapeHatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def env(self, **kw):
        e = {"HOME": self.fx.home}
        e.update(kw)
        return e

    def test_allow_all(self):
        got, _, _ = guard.evaluate("pkill node", self.fx.main, env=self.env(AGENT_SAFETY_ALLOW="1"))
        self.assertEqual(got, A)

    def test_disable_one_rule(self):
        env = self.env(AGENT_SAFETY_DISABLE="detach")
        self.assertEqual(guard.evaluate("npm run dev &", self.fx.main, env=env)[0], A)
        self.assertEqual(guard.evaluate("pkill node", self.fx.main, env=env)[0], D)

    def test_disable_list(self):
        env = self.env(AGENT_SAFETY_DISABLE="git, force-push")
        self.assertEqual(guard.evaluate("git reset --hard", self.fx.main, env=env)[0], A)
        self.assertEqual(guard.evaluate("git push -f", self.fx.main, env=env)[0], A)

    def test_inline_assignment_is_not_an_override(self):
        got, _, _ = guard.evaluate("AGENT_SAFETY_ALLOW=1 pkill node", self.fx.main, env=self.env())
        self.assertEqual(got, D)

    def test_project_config_file(self):
        path = os.path.join(self.fx.main, ".agent-safety")
        with open(path, "w") as fh:
            fh.write("# local override\ndisable = kill\n")
        try:
            self.assertEqual(guard.evaluate("pkill node", self.fx.main, env=self.env())[0], A)
            self.assertEqual(guard.evaluate("nohup x", self.fx.main, env=self.env())[0], D)
        finally:
            os.remove(path)

    def test_user_config_file(self):
        cfg = os.path.join(self.fx.home, ".config", "agent-safety")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "config"), "w") as fh:
            fh.write("disable = all\n")
        try:
            self.assertEqual(guard.evaluate("git stash", self.fx.main, env=self.env())[0], A)
        finally:
            shutil.rmtree(os.path.join(self.fx.home, ".config"))


def run_hook(harness, payload, env_extra=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir()}
    env.update(env_extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, SCRIPT, harness], input=data, text=True,
                          capture_output=True, env=env, timeout=30)


def load_payload(name, **subst):
    with open(os.path.join(HERE, "payloads", name)) as fh:
        text = fh.read()
    for k, v in subst.items():
        text = text.replace("__%s__" % k.upper(), v)
    return text


class HookProtocol(unittest.TestCase):
    """Runs the script the way each tool does: JSON payload on stdin."""

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def test_claude_deny(self):
        p = run_hook("claude", load_payload("claude-pretooluse-pkill.json", cwd=self.fx.main))
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("pkill", out["permissionDecisionReason"])

    def test_claude_allow_is_silent(self):
        p = run_hook("claude", load_payload("claude-pretooluse-npm-test.json", cwd=self.fx.main))
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)

    def test_claude_background_flag_is_fine(self):
        p = run_hook("claude", load_payload("claude-pretooluse-run-in-background.json",
                                            cwd=self.fx.main))
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)

    def test_claude_git_main_checkout(self):
        p = run_hook("claude", load_payload("claude-pretooluse-reset-hard.json", cwd=self.fx.main))
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        p = run_hook("claude", load_payload("claude-pretooluse-reset-hard.json", cwd=self.fx.wt))
        self.assertEqual(p.stdout, "")

    def test_claude_ask(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": self.fx.main,
                   "tool_input": {"command": 'cd "$X" && git reset --hard'}}
        out = json.loads(run_hook("claude", payload).stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")

    def test_claude_other_tool_ignored(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": self.fx.main,
                   "tool_input": {"file_path": "x", "content": "pkill node"}}
        p = run_hook("claude", payload)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_codex_deny_and_ask_becomes_deny(self):
        p = run_hook("codex", load_payload("codex-pretooluse-nohup.json", cwd=self.fx.main))
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("nohup", out["permissionDecisionReason"])
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": self.fx.main,
                   "turn_id": "t", "tool_input": {"command": 'cd "$X" && git reset --hard'}}
        out = json.loads(run_hook("codex", payload).stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")

    def test_cursor(self):
        p = run_hook("cursor", load_payload("cursor-beforeshell-stash.json", cwd=self.fx.main))
        out = json.loads(p.stdout)
        self.assertEqual(out["permission"], "deny")
        self.assertIn("main checkout", out["agent_message"])
        p = run_hook("cursor", load_payload("cursor-beforeshell-stash.json", cwd=self.fx.wt))
        self.assertEqual(json.loads(p.stdout), {"permission": "allow"})

    def test_env_override_reaches_hook(self):
        p = run_hook("claude", load_payload("claude-pretooluse-pkill.json", cwd=self.fx.main),
                     {"AGENT_SAFETY_ALLOW": "1"})
        self.assertEqual(p.stdout, "")

    def test_bad_input_fails_open(self):
        p = run_hook("claude", "this is not json")
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertIn("internal error", p.stderr)
        p = run_hook("cursor", "{")
        self.assertEqual(json.loads(p.stdout), {"permission": "allow"})

    def test_check_cli(self):
        p = subprocess.run([sys.executable, SCRIPT, "check", "pkill", "node"],
                           capture_output=True, text=True)
        self.assertTrue(p.stdout.startswith("deny:"), p.stdout)


if __name__ == "__main__":
    unittest.main()

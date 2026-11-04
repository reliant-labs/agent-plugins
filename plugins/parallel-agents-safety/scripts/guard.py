#!/usr/bin/env python3
"""parallel-agents-safety: a pre-execution guard for agent shell commands.

It refuses commands that hurt other agents sharing one machine:

  kill        pkill, killall, fuser -k, and kill fed by pgrep/pidof/ps/lsof
  detach      nohup, setsid, disown, and a backgrounding `&`
  git         git reset --hard, clean -f, checkout/restore of the whole tree,
              stash, branch -D, when they would run in a main checkout
              (not a linked worktree), or after a `cd` that might fail
  force-push  git push --force without --force-with-lease

Usage:  guard.py [claude|codex|cursor|check] < payload.json
        guard.py check 'some shell command'      (prints the decision)

Python 3 standard library only. An internal error lets the command run
(fail open) and prints the error on stderr.
"""

import json
import os
import re
import shlex
import subprocess
import sys

RULES = ("kill", "detach", "git", "force-push")

# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------


class Word:
    """One shell word. `value` is the text with quotes removed; `literal` is
    False when the word contains an expansion ($var, $(...), backticks) and so
    cannot be known before the shell runs it."""

    __slots__ = ("raw", "value", "literal", "subs", "glob", "tilde", "quoted")

    def __init__(self):
        self.raw = ""
        self.value = ""
        self.literal = True
        self.subs = []  # sources of $(...), `...`, <(...), >(...)
        self.glob = False
        self.tilde = False
        self.quoted = False

    def __repr__(self):
        return "Word(%r)" % self.value


class Op:
    __slots__ = ("op",)

    def __init__(self, op):
        self.op = op

    def __repr__(self):
        return "Op(%r)" % self.op


META = set(" \t\n;&|()<>")


def _skip_quoted(s, i, q):
    """s[i] is a quote char q. Return the index after the closing quote."""
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and q == '"':
            i += 2
            continue
        if c == q:
            return i + 1
        if q == '"' and c == "$" and i + 1 < n and s[i + 1] == "(":
            i = _match_paren(s, i + 1)
            continue
        if q == '"' and c == "`":
            i = _match_backtick(s, i)
            continue
        i += 1
    return n


def _match_paren(s, i):
    """s[i] == '('. Return the index after its matching ')'."""
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            i = _skip_quoted(s, i, c)
            continue
        if c == "`":
            i = _match_backtick(s, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _match_brace(s, i):
    """s[i] == '{' (of ${...}). Return the index after the matching '}'."""
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            i = _skip_quoted(s, i, c)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _match_backtick(s, i):
    """s[i] == '`'. Return the index after the closing backtick."""
    i += 1
    n = len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            return i + 1
        i += 1
    return n


def _unescape_backtick(src):
    return re.sub(r"\\([\\`$])", r"\1", src)


class Lexer:
    def __init__(self, s):
        self.s = s
        self.i = 0
        self.tokens = []
        self.heredocs = []  # (delimiter, strip_tabs)

    def at_command_start(self):
        return not self.tokens or isinstance(self.tokens[-1], Op)

    def lex(self):
        s = self.s
        n = len(s)
        while self.i < n:
            c = s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and self.i + 1 < n and s[self.i + 1] == "\n":
                self.i += 2
            elif c == "\n":
                self.tokens.append(Op("\n"))
                self.i += 1
                if self.heredocs:
                    self._skip_heredoc_bodies()
            elif c == "#":
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j
            elif c == "&":
                if s.startswith("&&", self.i):
                    self.tokens.append(Op("&&"))
                    self.i += 2
                elif s.startswith("&>", self.i):
                    self.i += 3 if s.startswith("&>>", self.i) else 2
                    self._redirect_target()
                else:
                    self.tokens.append(Op("&"))
                    self.i += 1
            elif c == "|":
                if s.startswith("||", self.i):
                    self.tokens.append(Op("||"))
                    self.i += 2
                else:
                    self.tokens.append(Op("|"))
                    self.i += 2 if s.startswith("|&", self.i) else 1
            elif c == ";":
                self.tokens.append(Op(";"))
                j = self.i + 1
                while j < n and s[j] in ";&":  # ;; ;& ;;& in case
                    j += 1
                self.i = j
            elif c == "(":
                if s.startswith("((", self.i) and self.at_command_start():
                    # (( arithmetic )): no commands, and `&` inside is bitwise.
                    self.i = _match_paren(s, self.i)
                    j = self.i
                    if j < n and s[j] == ")":
                        self.i = j + 1
                    self.tokens.append(Op(";"))
                else:
                    self.tokens.append(Op("("))
                    self.i += 1
            elif c == ")":
                self.tokens.append(Op(")"))
                self.i += 1
            elif c in "<>":
                if self.i + 1 < n and s[self.i + 1] == "(":
                    self.tokens.append(self._word())  # process substitution
                else:
                    self._redirect()
            else:
                w = self._word()
                if w is not None:
                    self.tokens.append(w)
        return self.tokens

    def _redirect(self):
        s = self.s
        for op in ("<<<", "<<-", "<<", "<&", "<>", ">>", ">&", ">|", "<", ">"):
            if s.startswith(op, self.i):
                break
        self.i += len(op)
        if op in ("<<", "<<-"):
            w = self._next_word()
            if w is not None:
                self.heredocs.append((w.value, op == "<<-"))
            return
        self._redirect_target()

    def _redirect_target(self):
        w = self._next_word()
        if w is not None and w.subs:
            # `> $(cmd)` still runs cmd; keep it visible to the checks.
            holder = Word()
            holder.subs = w.subs
            holder.literal = False
            self.tokens.append(Op("redir-subs"))
            self.tokens.append(holder)

    def _next_word(self):
        s = self.s
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        if self.i >= len(s) or s[self.i] in "\n;|&()":
            return None
        if s[self.i] in "<>" and not s.startswith("(", self.i + 1):
            return None
        return self._word()

    def _skip_heredoc_bodies(self):
        s = self.s
        for delim, strip in self.heredocs:
            while self.i < len(s):
                j = s.find("\n", self.i)
                line = s[self.i:] if j < 0 else s[self.i:j]
                self.i = len(s) if j < 0 else j + 1
                if (line.lstrip("\t") if strip else line) == delim:
                    break
        self.heredocs = []

    def _word(self):
        s = self.s
        n = len(s)
        w = Word()
        start = self.i
        val = []
        while self.i < n:
            c = s[self.i]
            if c in META:
                if c in "<>" and self.i + 1 < n and s[self.i + 1] == "(":
                    end = _match_paren(s, self.i + 1)
                    w.subs.append(s[self.i + 2:end - 1])
                    w.literal = False
                    self.i = end
                    continue
                # `2>`, `2>&1`: digits right before a redirect are an fd.
                if c in "<>" and val and "".join(val).isdigit() and w.literal and not w.quoted:
                    self.i = start + len("".join(val))
                    return None
                break
            if c == "\\":
                if self.i + 1 < n:
                    if s[self.i + 1] != "\n":
                        val.append(s[self.i + 1])
                    w.quoted = True
                self.i += 2
            elif c == "'":
                end = _skip_quoted(s, self.i, "'")
                val.append(s[self.i + 1:end - 1])
                w.quoted = True
                self.i = end
            elif c == '"':
                self._double_quoted(w, val)
            elif c == "$":
                self._dollar(w, val, in_dq=False)
            elif c == "`":
                end = _match_backtick(s, self.i)
                w.subs.append(_unescape_backtick(s[self.i + 1:end - 1]))
                w.literal = False
                self.i = end
            else:
                if c in "*?[":
                    w.glob = True
                if c == "~" and self.i == start:
                    w.tilde = True
                val.append(c)
                self.i += 1
        w.raw = s[start:self.i]
        w.value = "".join(val)
        if self.i == start:
            self.i += 1  # never loop on an unexpected character
            return None
        return w

    def _double_quoted(self, w, val):
        s = self.s
        n = len(s)
        w.quoted = True
        self.i += 1
        while self.i < n:
            c = s[self.i]
            if c == '"':
                self.i += 1
                return
            if c == "\\" and self.i + 1 < n:
                nxt = s[self.i + 1]
                if nxt in '$`"\\':
                    val.append(nxt)
                elif nxt != "\n":
                    val.append("\\" + nxt)
                self.i += 2
            elif c == "$":
                self._dollar(w, val, in_dq=True)
            elif c == "`":
                end = _match_backtick(s, self.i)
                w.subs.append(_unescape_backtick(s[self.i + 1:end - 1]))
                w.literal = False
                self.i = end
            else:
                val.append(c)
                self.i += 1

    def _dollar(self, w, val, in_dq):
        s = self.s
        n = len(s)
        i = self.i
        nxt = s[i + 1] if i + 1 < n else ""
        if nxt == "'" and not in_dq:
            end = i + 2
            while end < n and s[end] != "'":
                end += 2 if s[end] == "\\" else 1
            val.append(s[i + 2:end])
            w.quoted = True
            self.i = end + 1
        elif nxt == '"' and not in_dq:
            self.i = i + 1
            self._double_quoted(w, val)
        elif s.startswith("$((", i):
            end = _match_paren(s, i + 1)
            val.append(s[i:end])
            w.literal = False
            self.i = end
        elif nxt == "(":
            end = _match_paren(s, i + 1)
            w.subs.append(s[i + 2:end - 1])
            val.append(s[i:end])
            w.literal = False
            self.i = end
        elif nxt == "{":
            end = _match_brace(s, i + 1)
            val.append(s[i:end])
            w.literal = False
            self.i = end
        elif nxt and (nxt.isalnum() or nxt == "_"):
            j = i + 1
            if nxt.isdigit():
                j += 1
            else:
                while j < n and (s[j].isalnum() or s[j] == "_"):
                    j += 1
            val.append(s[i:j])
            w.literal = False
            self.i = j
        elif nxt and nxt in "!?$#@*-":
            val.append(s[i:i + 2])
            w.literal = False
            self.i = i + 2
        else:
            val.append("$")
            self.i = i + 1


# ---------------------------------------------------------------------------
# Parser: tokens -> list of (pipeline, separator)
# ---------------------------------------------------------------------------
#
# A pipeline is a list of commands. A command is either
#   ("simple", words, extra_subs)   or   ("group", items)   for ( ... )
# The separator after a pipeline is one of ; \n & && || or None.
# Compound keywords (if/then/do/{ ...) are dropped, which keeps the commands
# inside them in order, and `{ ... }` braces keep their real cd scoping.

RESERVED = {
    "if", "then", "elif", "else", "fi", "while", "until", "do", "done",
    "!", "{", "}", "time", "coproc",
}
SEPARATORS = (";", "\n", "&", "&&", "||")


def parse(tokens):
    items, _ = _parse_items(tokens, 0)
    return items


def _parse_items(tokens, i, closing=False):
    items = []
    pipeline = []
    words = []
    extra = []

    def end_command():
        nonlocal words, extra
        if words or extra:
            pipeline.append(("simple", words, extra))
        words, extra = [], []

    def end_pipeline(sep):
        nonlocal pipeline
        end_command()
        if pipeline or sep == "&":
            items.append((pipeline, sep))
        pipeline = []

    n = len(tokens)
    while i < n:
        t = tokens[i]
        if isinstance(t, Word):
            words.append(t)
            i += 1
            continue
        op = t.op
        if op == "redir-subs":
            if i + 1 < n and isinstance(tokens[i + 1], Word):
                extra.extend(tokens[i + 1].subs)
            i += 2
            continue
        if op == "(":
            end_command()
            body, i = _parse_items(tokens, i + 1, closing=True)
            pipeline.append(("group", body))
            continue
        if op == ")":
            if closing:
                end_pipeline(None)
                return items, i + 1
            end_pipeline(";")  # stray ) ends a case pattern; a command follows
            i += 1
            continue
        if op == "|":
            end_command()
            i += 1
            continue
        end_pipeline(op)
        i += 1
    end_pipeline(None)
    return items, i


ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")


def split_command(words):
    """Strip leading assignments and compound keywords.
    Returns (assignments, argv)."""
    assigns = []
    argv = list(words)
    while argv:
        w = argv[0]
        if ASSIGN_RE.match(w.raw):
            assigns.append(w)
            argv.pop(0)
        elif not w.quoted and w.literal and w.value in RESERVED:
            argv.pop(0)
        else:
            break
    return assigns, argv


# ---------------------------------------------------------------------------
# Wrappers: sudo, env, timeout, xargs, ... and shells run with -c
# ---------------------------------------------------------------------------

# option letters that take an argument, per wrapper
WRAPPER_ARGS = {
    "sudo": set("ugCDhpRrtUT"),
    "doas": set("uC"),
    "env": set("uCS"),
    "nice": set("n"),
    "ionice": set("cnpPu"),
    "timeout": set("sk"),
    "stdbuf": set("ioe"),
    "time": set("fo"),
    "chronic": set(),
    "caffeinate": set("tw"),
    "command": set(),
    "builtin": set(),
    "exec": set("a"),
    "nohup": set(),
    "setsid": set(),
    "xargs": set("aEeIiLlnPsd"),
    "unbuffer": set(),
    "flock": set("wEc"),
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "busybox"}


def basename(w):
    return os.path.basename(w.value) if w is not None else ""


def unwrap(argv):
    """Peel wrappers off argv. Returns (argv, wrappers) where wrappers is a
    list of (name, env_assignments)."""
    wrappers = []
    while argv:
        name = basename(argv[0])
        if name not in WRAPPER_ARGS:
            break
        takes = WRAPPER_ARGS[name]
        envs = []
        j = 1
        if name == "flock":
            # flock [opts] <file> <command...>
            while j < len(argv) and argv[j].value.startswith("-"):
                j += 2 if argv[j].value[1:2] in takes and len(argv[j].value) == 2 else 1
            j += 1
        else:
            while j < len(argv):
                v = argv[j].value
                if name == "env" and ASSIGN_RE.match(argv[j].raw):
                    envs.append(argv[j])
                    j += 1
                    continue
                if v == "--":
                    j += 1
                    break
                if v.startswith("--"):
                    j += 1
                    if "=" not in v and v[2:] in ("signal", "kill-after", "user", "group",
                                                 "chdir", "unset", "max-args", "max-procs",
                                                 "replace", "delimiter", "arg-file",
                                                 "adjustment", "split-string"):
                        j += 1
                    continue
                if v.startswith("-") and len(v) > 1 and not (name == "nice" and v[1:].isdigit()):
                    j += 1
                    letters = v[1:]
                    for k, ch in enumerate(letters):
                        if ch in takes:
                            if k == len(letters) - 1:
                                j += 1  # argument is the next word
                            break
                    continue
                if name == "nice" and v.startswith("-"):
                    j += 1
                    continue
                break
            if name == "timeout" and j < len(argv):
                j += 1  # the duration
            if name in ("command",) and len(argv) > 1 and argv[1].value in ("-v", "-V"):
                return [], wrappers  # lookup only, runs nothing
        wrappers.append((name, envs))
        argv = argv[j:]
    return argv, wrappers


def shell_script(argv):
    """For `bash -c 'script'` and friends, return the script text, else None."""
    name = basename(argv[0])
    if name == "busybox" and len(argv) > 1 and basename(argv[1]) in SHELLS:
        argv = argv[1:]
        name = basename(argv[0])
    if name not in SHELLS:
        return None
    has_c = False
    j = 1
    while j < len(argv):
        v = argv[j].value
        if v.startswith("--"):
            j += 2 if v in ("--rcfile", "--init-file") else 1
            continue
        if (v.startswith("-") or v.startswith("+")) and len(v) > 1:
            if "c" in v[1:]:
                has_c = True
            j += 2 if "o" in v[1:] or "O" in v[1:] else 1
            continue
        return v if has_c else None
    return None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

KILL_BY_NAME = {"pkill", "killall", "killall5", "skill"}
DETACHERS = {"nohup", "setsid", "disown"}
# commands that find PIDs of processes this agent may not own
PID_LOOKUP = {"pgrep", "pidof", "lsof", "fuser"}
PID_LOOKUP_PIPE = PID_LOOKUP | {"ps", "ss", "netstat"}
WHOLE_TREE = {".", "./", ":/", ":", "*", ":/*", ":(top)", ":(top)*", "-A", "--all"}


class Finding:
    def __init__(self, rule, decision, message):
        self.rule = rule
        self.decision = decision  # "deny" or "ask"
        self.message = message


class State:
    """Where the next command runs. dir is None when it can't be known;
    `unknown` then says why. `fragile` names a cd whose failure would
    leave the next command running somewhere else."""

    __slots__ = ("dir", "unknown", "fragile", "errexit", "git_env")

    def __init__(self, dir, unknown=None, fragile=None, errexit=False, git_env=None):
        self.dir = dir
        self.unknown = unknown
        self.fragile = fragile
        self.errexit = errexit
        self.git_env = git_env

    def copy(self, **kw):
        s = State(self.dir, self.unknown, self.fragile, self.errexit, self.git_env)
        for k, v in kw.items():
            setattr(s, k, v)
        return s


class Context:
    def __init__(self, harness, home, session_root, checker):
        self.harness = harness
        self.home = home
        self.session_root = session_root
        self.checker = checker
        self.findings = []
        self.kill_var_targets = False
        self.lookups = set()
        self.depth = 0

    def add(self, rule, decision, message):
        for f in self.findings:
            if f.message == message:
                return
        self.findings.append(Finding(rule, decision, message))


def background_hint(harness):
    if harness == "claude":
        return "Run long jobs with the Bash tool's run_in_background option instead"
    if harness == "codex":
        return ("Run it as a normal command instead; Codex keeps long-running "
                "commands attached as exec sessions you can poll")
    if harness == "cursor":
        return "Run long jobs with the terminal tool's background option instead"
    return "Run long jobs with your tool's background-task feature instead"


def show(argv, limit=60):
    text = " ".join(w.raw for w in argv)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def analyze(command, ctx, state):
    if ctx.depth > 8:
        return
    ctx.depth += 1
    try:
        items = parse(Lexer(command).lex())
        walk(items, ctx, state)
    finally:
        ctx.depth -= 1


def _is_cmd(pipeline, names):
    for cmd in pipeline:
        if cmd[0] == "simple":
            _, argv = split_command(cmd[1])
            argv, _ = unwrap(argv)
            if argv and basename(argv[0]) in names:
                return True
    return False


def walk(items, ctx, state):
    state = state.copy()
    chain_cd = None  # description of a cd that guards the current && chain
    for idx, (pipeline, sep) in enumerate(items):
        if sep == "&":
            later = items[idx + 1:]
            if not any(_is_cmd(p, {"wait"}) for p, _ in later):
                ctx.add("detach", "deny",
                        "Blocked: `&` backgrounds `%s` outside the agent's control, so "
                        "nothing can track or stop it. %s. (`a & b & wait` is fine.)"
                        % (_pipeline_text(pipeline) or "the command", background_hint(ctx.harness)))

        cd = _cd_target(pipeline, ctx, state) if sep != "&" else None
        if cd is not None:
            new = state.copy(dir=cd[0], unknown=cd[1], fragile=None)
            desc = cd[2]
            if sep == "&&":
                state = new
                chain_cd = desc
            elif sep == "||":
                nxt = items[idx + 1][0] if idx + 1 < len(items) else []
                state = new
                if not _is_cmd(nxt, {"exit", "return"}):
                    state.fragile = None if state.errexit else desc
            else:
                state = new
                state.fragile = None if state.errexit else desc
            continue

        for cmd in pipeline:
            st = state if len(pipeline) == 1 else state.copy()
            eval_command(cmd, pipeline, ctx, st)
            if len(pipeline) == 1 and cmd[0] == "simple":
                _track_settings(cmd, st)
                state = st

        if chain_cd and sep != "&&":
            if not state.errexit:
                state.fragile = chain_cd
            chain_cd = None


def _pipeline_text(pipeline):
    parts = []
    for cmd in pipeline:
        if cmd[0] == "simple":
            text = show(cmd[1], 40)
            if text and text != "}":
                parts.append(text)
        else:
            parts.append("( ... )")
    return " | ".join(parts)


def _track_settings(cmd, state):
    """Follow `set -e` and exported GIT_DIR / GIT_WORK_TREE."""
    assigns, argv = split_command(cmd[1])
    if not argv:
        for a in assigns:
            if re.match(r"^GIT_(DIR|WORK_TREE)=", a.raw):
                state.git_env = a.raw.split("=", 1)[0]
        return
    name = basename(argv[0])
    if name == "set":
        args = [w.value for w in argv[1:]]
        for k, v in enumerate(args):
            if re.match(r"^-[a-zA-Z]*e", v):
                state.errexit = True
            if v == "-o" and k + 1 < len(args) and args[k + 1] == "errexit":
                state.errexit = True
    elif name in ("export", "declare", "typeset"):
        for w in argv[1:]:
            if re.match(r"^GIT_(DIR|WORK_TREE)=", w.raw):
                state.git_env = w.raw.split("=", 1)[0]


def _cd_target(pipeline, ctx, state):
    """If the pipeline is a single `cd`/`pushd`, return (dir, unknown, desc)."""
    if len(pipeline) != 1 or pipeline[0][0] != "simple":
        return None
    _, argv = split_command(pipeline[0][1])
    if argv and basename(argv[0]) == "builtin":
        argv = argv[1:]
    if not argv or basename(argv[0]) not in ("cd", "pushd", "popd"):
        return None
    for w in argv:
        for sub in w.subs:
            analyze(sub, ctx, state)
    desc = show(argv)
    if basename(argv[0]) == "popd":
        return (None, "`popd`", desc)
    args = [w for w in argv[1:] if not (w.value.startswith("-") and w.value != "-")]
    if not args:
        return (ctx.home, None, desc) if ctx.home else (None, "`cd` with no path", desc)
    w = args[0]
    if w.value == "-":
        return (None, "`cd -`", desc)
    if not w.literal or w.glob:
        return (None, "`%s`" % desc, desc)
    path = w.value
    if w.tilde:
        if path == "~" or path.startswith("~/"):
            path = (ctx.home or "~") + path[1:]
        else:
            return (None, "`%s`" % desc, desc)
    if state.dir is None and not os.path.isabs(path):
        return (None, state.unknown, desc)
    return (os.path.normpath(os.path.join(state.dir or "/", path)), None, desc)


def eval_command(cmd, pipeline, ctx, state):
    if cmd[0] == "group":
        walk(cmd[1], ctx, state)
        return
    _, words, extra = cmd
    for sub in extra:
        analyze(sub, ctx, state)
    assigns, argv = split_command(words)
    for w in words:
        for sub in w.subs:
            analyze(sub, ctx, state)
    if not argv:
        return
    git_env = state.git_env
    for a in assigns:
        if re.match(r"^GIT_(DIR|WORK_TREE)=", a.raw):
            git_env = a.raw.split("=", 1)[0]

    first = basename(argv[0])
    if first in DETACHERS:
        ctx.add("detach", "deny",
                "Blocked: `%s` detaches the process so nothing can track or stop it, and "
                "it outlives the agent. %s." % (first, background_hint(ctx.harness)))

    argv, wrappers = unwrap(argv)
    for name, envs in wrappers:
        if name in DETACHERS and name != first:
            ctx.add("detach", "deny",
                    "Blocked: `%s` detaches the process so nothing can track or stop it, "
                    "and it outlives the agent. %s." % (name, background_hint(ctx.harness)))
        for e in envs:
            if re.match(r"^GIT_(DIR|WORK_TREE)=", e.raw):
                git_env = e.raw.split("=", 1)[0]
    if not argv:
        return
    via_xargs = any(name == "xargs" for name, _ in wrappers)
    name = basename(argv[0])

    if name in PID_LOOKUP_PIPE:
        ctx.lookups.add(name)

    script = shell_script(argv)
    if script is not None:
        analyze(script, ctx, state)
        return
    if name == "eval":
        analyze(" ".join(w.value for w in argv[1:]), ctx, state)
        return
    if name == "su":
        for k, w in enumerate(argv[:-1]):
            if w.value in ("-c", "--command"):
                analyze(argv[k + 1].value, ctx, state)
        return

    if name in KILL_BY_NAME:
        ctx.add("kill", "deny",
                "Blocked: `%s` kills processes by name, which hits other agents' processes "
                "and maybe your own session. Kill only PIDs you started (`kill <pid>`, "
                "`kill $!`)." % name)
    elif name == "fuser" and any(
            w.value == "--kill" or re.match(r"^-[a-zA-Z]*k", w.value) for w in argv[1:]):
        ctx.add("kill", "deny",
                "Blocked: `fuser -k` kills whatever holds that file or port, which can be "
                "another agent's process or your own session. Kill only PIDs you started.")
    elif name == "kill":
        check_kill(argv, pipeline, via_xargs, ctx)
    elif name == "git":
        check_git(argv, ctx, state, git_env)


def _lookup_in(sources):
    """Which PID lookup commands do these command sources run?"""
    found = set()
    for src in sources:
        sub = Context("check", None, None, lambda path: ("none", path))
        sub.depth = 1
        try:
            analyze(src, sub, State(None, "substitution"))
        except Exception:
            pass
        found |= sub.lookups
    return found


def check_kill(argv, pipeline, via_xargs, ctx):
    args = argv[1:]
    if args and args[0].value in ("-l", "-L", "--list"):
        return
    k = 0
    if args and args[0].value == "-s" or args and args[0].value == "-n":
        k = 2
    elif args and args[0].value.startswith("-") and args[0].value != "--":
        k = 1
    if k < len(args) and args[k].value == "--":
        k += 1
    targets = args[k:]
    reason = None
    for w in targets:
        if w.literal:
            v = w.value
            if v in ("-1", "0", "-0"):
                ctx.add("kill", "deny",
                        "Blocked: `kill %s` signals every process in your session or "
                        "account, including other agents and your own harness. Kill only "
                        "PIDs you started." % v)
            continue
        lookups = _lookup_in(w.subs)
        if lookups:
            reason = sorted(lookups)[0]
        elif not w.subs:
            ctx.kill_var_targets = True
    if via_xargs:
        upstream = set()
        for cmd in pipeline:
            if cmd[0] == "simple":
                _, a = split_command(cmd[1])
                a, _ = unwrap(a)
                if a and basename(a[0]) in PID_LOOKUP_PIPE:
                    upstream.add(basename(a[0]))
        if upstream:
            reason = sorted(upstream)[0]
    if reason:
        ctx.add("kill", "deny", _kill_lookup_message(reason))


def _kill_lookup_message(tool):
    return ("Blocked: this kills processes found with `%s` (by name or port), which can "
            "hit other agents' processes and maybe your own session. Kill only PIDs you "
            "started (`kill <pid>`, `kill $!`)." % tool)


GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                     "--exec-path", "--super-prefix", "--config-env", "--list-cmds"}


def check_git(argv, ctx, state, git_env):
    dirs = []
    unknown = None
    j = 1
    while j < len(argv):
        w = argv[j]
        v = w.value
        if not v.startswith("-"):
            break
        opt = v.split("=", 1)[0]
        val = None
        if opt in GIT_OPTS_WITH_ARG:
            if "=" in v:
                val = v.split("=", 1)[1]
            elif j + 1 < len(argv):
                val = argv[j + 1].value
                if opt == "-C" and not (argv[j + 1].literal and not argv[j + 1].glob):
                    unknown = "`git -C %s`" % argv[j + 1].raw
                j += 1
        if opt == "-C" and val is not None:
            dirs.append(val)
        if opt in ("--git-dir", "--work-tree"):
            unknown = "`%s`" % opt
        j += 1
    if j >= len(argv):
        return
    sub = argv[j].value
    args = argv[j + 1:]
    hit = classify_git(sub, args)
    if not hit:
        return
    rule, label = hit

    if rule == "force-push":
        ctx.add("force-push", "deny",
                "Blocked: `%s` can overwrite commits someone else pushed. Use "
                "`git push --force-with-lease` instead." % label)
        return

    if state.fragile:
        ctx.add("git", "deny",
                "Blocked: `%s` comes after `%s` without `&&`. If the cd fails, it runs in "
                "the wrong checkout. Chain it: `cd <dir> && %s`, and check "
                "`git rev-parse --show-toplevel` first." % (label, state.fragile, label))
        return
    if git_env:
        unknown = "`%s` is set" % git_env
    target = state.dir
    if target is None and unknown is None:
        unknown = state.unknown or "the working directory"
    if target is not None:
        for d in dirs:
            if d.startswith("~/") or d == "~":
                d = (ctx.home or "~") + d[1:]
            target = os.path.normpath(os.path.join(target, d))
    if unknown:
        ctx.add("git", "ask",
                "`%s`: can't tell which checkout this runs in (%s). Confirm it is your own "
                "worktree, not a checkout other agents share." % (label, unknown))
        return
    verdict = ctx.checker(target)
    if verdict[0] == "main":
        ctx.add("git", "deny",
                "Blocked: `%s` would run in %s, the main checkout, not a linked worktree. "
                "Other agents' uncommitted work may live there. Do it in your own worktree "
                "(`git worktree add ../<task> -b <task>`), and check "
                "`git rev-parse --show-toplevel` first." % (label, verdict[1]))
    elif verdict[0] == "unknown":
        ctx.add("git", "ask",
                "`%s`: can't tell which checkout this runs in (%s). Confirm it is your own "
                "worktree." % (label, verdict[1]))
    elif verdict[0] == "worktree" and ctx.session_root:
        own = ctx.checker(ctx.session_root)
        if own[0] == "worktree" and own[1] != verdict[1]:
            ctx.add("git", "ask",
                    "`%s` would run in %s, a different worktree from this session's (%s). "
                    "Confirm it is yours." % (label, verdict[1], own[1]))


def _short_flags(args):
    """Letters of every short option cluster, e.g. -fdx -> fdx."""
    letters = ""
    for w in args:
        v = w.value
        if v == "--":
            break
        if v.startswith("-") and not v.startswith("--") and len(v) > 1:
            letters += v[1:]
    return letters


def _long_flags(args):
    out = set()
    for w in args:
        if w.value == "--":
            break
        if w.value.startswith("--"):
            out.add(w.value.split("=", 1)[0])
    return out


STASH_ACTIONS = {"list", "show", "drop", "pop", "apply", "branch", "push", "save",
                 "clear", "create", "store", "export", "import"}


def classify_git(sub, args):
    short = _short_flags(args)
    long_ = _long_flags(args)
    values = [w.value for w in args]
    if sub == "reset" and "--hard" in long_:
        return ("git", "git reset --hard")
    if sub == "clean" and ("f" in short or "--force" in long_):
        if "n" in short or "--dry-run" in long_:
            return None
        return ("git", "git clean -f")
    if sub == "checkout":
        if "f" in short or "--force" in long_:
            return ("git", "git checkout -f")
        if any(v in WHOLE_TREE for v in values if v not in ("-A", "--all")):
            return ("git", "git checkout -- .")
    if sub == "restore":
        staged = "S" in short or "--staged" in long_
        worktree = "W" in short or "--worktree" in long_
        if (not staged or worktree) and any(v in WHOLE_TREE for v in values):
            return ("git", "git restore .")
    if sub == "stash":
        rest = [v for v in values if not v.startswith("-")]
        action = rest[0] if rest and rest[0] in STASH_ACTIONS else "push"
        if action in ("push", "save", "drop", "clear"):
            return ("git", "git stash" if action in ("push", "save") else "git stash " + action)
        return None
    if sub == "branch":
        delete = "d" in short or "--delete" in long_
        force = "f" in short or "--force" in long_
        if "D" in short or (delete and force):
            return ("git", "git branch -D")
    if sub == "push":
        lease = any(v.startswith("--force-with-lease") for v in values)
        force = "f" in short or "--force" in long_ or any(
            v.startswith("+") and len(v) > 1 for v in values)
        if force and not lease:
            return ("force-push", "git push --force")
    return None


def check_checkout(path):
    """('main', toplevel) | ('worktree', toplevel) | ('none', path) |
    ('unknown', why)"""
    if not os.path.isdir(path):
        return ("unknown", "%s does not exist yet" % path)
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        return ("unknown", "git failed: %s" % e)
    if out.returncode != 0:
        if "not a git repository" in out.stderr:
            return ("none", path)
        return ("unknown", out.stderr.strip().splitlines()[0] if out.stderr.strip() else "git failed")
    lines = out.stdout.strip().splitlines()
    if len(lines) < 2:
        return ("unknown", "unexpected git output")
    git_dir = os.path.realpath(os.path.join(path, lines[0]))
    common = os.path.realpath(os.path.join(path, lines[1]))
    top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=5)
    toplevel = top.stdout.strip() if top.returncode == 0 else path
    return ("main" if git_dir == common else "worktree", toplevel)


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------


def disabled_rules(env, project_dirs):
    """Rules switched off by AGENT_SAFETY_ALLOW, AGENT_SAFETY_DISABLE, or a
    config file (~/.config/agent-safety/config, <project>/.agent-safety)."""
    off = set()
    if env.get("AGENT_SAFETY_ALLOW", "").strip() in ("1", "true", "yes", "all"):
        return set(RULES)
    for name in re.split(r"[\s,]+", env.get("AGENT_SAFETY_DISABLE", "")):
        if name:
            off.add(name)
    xdg = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME", ""), ".config")
    files = [os.path.join(xdg, "agent-safety", "config")]
    files += [os.path.join(d, ".agent-safety") for d in project_dirs if d]
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    m = re.match(r"^disable\s*[=:]?\s*(.*)$", line)
                    if m:
                        off |= {x for x in re.split(r"[\s,]+", m.group(1)) if x}
        except OSError:
            pass
    if "all" in off:
        return set(RULES)
    return off


def _toplevel(path):
    if not path or not os.path.isdir(path):
        return None
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def evaluate(command, cwd, harness="claude", env=None, session_root=None, checker=None):
    """Return (decision, message, findings). decision is allow, ask or deny."""
    env = os.environ if env is None else env
    ctx = Context(harness, env.get("HOME"), session_root, checker or check_checkout)
    analyze(command, ctx, State(cwd, None if cwd else "unknown working directory"))
    if ctx.kill_var_targets and ctx.lookups & PID_LOOKUP:
        ctx.add("kill", "deny", _kill_lookup_message(sorted(ctx.lookups & PID_LOOKUP)[0]))
    findings = ctx.findings
    if findings:
        off = disabled_rules(env, [_toplevel(cwd), session_root])
        findings = [f for f in findings if f.rule not in off]
    deny, ask, seen = [], [], set()
    for f in findings:  # one message per rule and decision keeps reasons short
        if (f.rule, f.decision) not in seen:
            seen.add((f.rule, f.decision))
            (deny if f.decision == "deny" else ask).append(f)
    if deny:
        return "deny", " ".join(f.message for f in deny), findings
    if ask:
        return "ask", " ".join(f.message for f in ask), findings
    return "allow", "", findings


def main(argv):
    harness = argv[1] if len(argv) > 1 else "claude"
    if harness == "check":
        cmd = " ".join(argv[2:]) if len(argv) > 2 else sys.stdin.read()
        decision, message, _ = evaluate(cmd, os.getcwd(), "generic")
        print(decision + (": " + message if message else ""))
        return 0
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
        tool_input = payload.get("tool_input") or {}
        if harness == "cursor":
            command = payload.get("command")
            if command is None and isinstance(tool_input, dict):
                command = tool_input.get("command")
            cwd = payload.get("cwd") or (tool_input.get("working_directory")
                                          if isinstance(tool_input, dict) else None)
            roots = payload.get("workspace_roots") or []
            session_root = roots[0] if roots else None
        else:
            tool = payload.get("tool_name", "Bash")
            if tool not in ("Bash", "Shell", "exec_command", "shell"):
                return 0
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            cwd = payload.get("cwd")
            session_root = os.environ.get("CLAUDE_PROJECT_DIR") if harness == "claude" else None
        if isinstance(command, list):
            command = shlex.join(str(c) for c in command)
        if not isinstance(command, str) or not command.strip():
            return emit(harness, "allow", "")
        decision, message, _ = evaluate(command, cwd or os.getcwd(), harness,
                                        session_root=session_root)
    except Exception as e:  # fail open: never wedge the agent on a bug here
        sys.stderr.write("parallel-agents-safety: internal error, allowing: %r\n" % (e,))
        return emit(harness, "allow", "")
    return emit(harness, decision, message)


def emit(harness, decision, message):
    if harness == "cursor":
        out = {"permission": decision}
        if decision != "allow":
            out["user_message"] = message
            out["agent_message"] = message
        print(json.dumps(out))
        return 0
    if decision == "allow":
        return 0
    if harness == "codex" and decision == "ask":
        # Codex does not support "ask" yet; it would let the command run.
        decision = "deny"
        message += " Ask the user to confirm, or have them run it."
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": message,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

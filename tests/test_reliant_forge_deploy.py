"""Static checks for the reliant-forge-deploy plugin: manifests, skill, rule, evals.

The plugin is instructions only (no hook, no script), so what can break is
the packaging: a manifest that disagrees with another, a skill whose
frontmatter a host rejects, or an eval set that drifts from the skill.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "reliant-forge-deploy"
SKILL = PLUGIN / "skills" / "reliant-forge-deploy" / "SKILL.md"
RULE = PLUGIN / "rules" / "reliant-forge-deploy.mdc"
EVALS = ROOT / "evals" / "reliant-forge-deploy" / "prompts.json"

MANIFESTS = {
    "claude": PLUGIN / ".claude-plugin" / "plugin.json",
    "portable": PLUGIN / "plugin.json",
    "cursor": PLUGIN / ".cursor-plugin" / "plugin.json",
}
MARKETPLACES = {
    "claude": ROOT / ".claude-plugin" / "marketplace.json",
    "codex": ROOT / ".agents" / "plugins" / "marketplace.json",
    "cursor": ROOT / ".cursor-plugin" / "marketplace.json",
}


def frontmatter(path):
    """The `key: value` lines between the leading --- fences (no YAML needed:
    every value here is a single line)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise AssertionError(f"{path}: no frontmatter")
    fields = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise AssertionError(f"{path}: frontmatter line is not key: value: {line!r}")
        fields[key.strip()] = value.strip()
    return fields, text[m.end():]


class Manifests(unittest.TestCase):
    def test_manifests_agree(self):
        loaded = {k: json.loads(p.read_text()) for k, p in MANIFESTS.items()}
        for key in ("name", "version", "description", "license"):
            values = {k: m.get(key) for k, m in loaded.items()}
            self.assertEqual(len(set(values.values())), 1, f"{key} differs: {values}")
        self.assertEqual(loaded["claude"]["name"], "reliant-forge-deploy")

    def test_paths_resolve(self):
        cursor = json.loads(MANIFESTS["cursor"].read_text())
        for key in ("skills", "rules", "logo"):
            self.assertTrue((PLUGIN / cursor[key]).exists(), f"cursor {key}: {cursor[key]}")
        iface = json.loads(MANIFESTS["portable"].read_text())["extensions"]["com.openai"]["interface"]
        for key in ("logo", "composerIcon"):
            self.assertTrue((PLUGIN / iface[key]).exists(), f"codex {key}: {iface[key]}")

    def test_listed_in_every_marketplace(self):
        for host, path in MARKETPLACES.items():
            names = [p["name"] for p in json.loads(path.read_text())["plugins"]]
            self.assertIn("reliant-forge-deploy", names, host)
            self.assertEqual(len(names), len(set(names)), f"{host}: duplicate plugin names")


class Skill(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = frontmatter(SKILL)

    def test_frontmatter(self):
        self.assertEqual(self.fm["name"], SKILL.parent.name)
        # Claude Code and Codex cap a skill description at 1024 characters.
        self.assertLessEqual(len(self.fm["description"]), 1024)

    def test_description_names_the_triggers(self):
        desc = self.fm["description"].lower()
        for phrase in ("deploy this", "host my site", "give me a url", "ship it",
                       "put this online", "backend/api with a database", "no account"):
            self.assertIn(phrase, desc)

    def test_body_states_the_facts_a_user_is_owed(self):
        for fact in ("--claimable --plan-only", "--in-place --kind site", "72 hours",
                     "claim link", "No outbound internet", "Reliant provider",
                     "claimable_throttled", "Don't retry"):
            self.assertIn(fact, self.body)

    def test_never_suggests_direct_model_apis(self):
        # The skill may name the hosts only to forbid them.
        for line in self.body.splitlines():
            if "api.openai.com" in line or "api.anthropic.com" in line:
                self.assertRegex(line, r"Don't|Never|never")


class Rule(unittest.TestCase):
    def test_rule_frontmatter(self):
        fm, body = frontmatter(RULE)
        self.assertEqual(fm["alwaysApply"], "false")
        self.assertIn("deploy this", fm["description"])
        self.assertIn("reliant-forge-deploy", body)


class Evals(unittest.TestCase):
    def setUp(self):
        self.evals = json.loads(EVALS.read_text())

    def test_cases(self):
        cases = self.evals["cases"]
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)), "duplicate case ids")
        for c in cases:
            self.assertIn(c["fixture"], self.evals["fixtures"], c["id"])
            self.assertIn(c["expect"], ("trigger", "no_trigger"), c["id"])
            if c["expect"] == "trigger":
                self.assertIn(c["path"], self.evals["paths"], c["id"])
            else:
                self.assertTrue(c.get("why"), f"{c['id']}: a no_trigger case says why")
        kinds = {c["expect"] for c in cases}
        self.assertEqual(kinds, {"trigger", "no_trigger"})

    def test_paths_use_commands_the_skill_teaches(self):
        _, body = frontmatter(SKILL)
        for name, steps in self.evals["paths"].items():
            for step in steps:
                self.assertIn(step, body, f"path {name}: {step!r} is not in the skill")

    def test_every_trigger_phrase_has_a_case(self):
        prompts = " | ".join(c["prompt"].lower() for c in self.evals["cases"] if c["expect"] == "trigger")
        for phrase in ("deploy this", "host my site", "give me a url", "ship it", "put this online", "backend api with a database"):
            self.assertIn(phrase, prompts)


if __name__ == "__main__":
    unittest.main()

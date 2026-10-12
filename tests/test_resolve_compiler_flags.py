#!/usr/bin/env python3
"""Contract tests for deploy.yml's "Parse inventory and resolve deployments"
config-resolution step — specifically the additive
`compiler.allow_blanket_robots_disallow` field.

These tests run the step's REAL script body — extracted from
.github/workflows/deploy.yml, not a copy pasted in here — against a synthetic
inventory fixture modeled on a real, live inventory entry (casaboa-dev.yaml:
no embed_fonts/inline_body_css/allow_blanket_robots_disallow keys at all). A
reimplementation of resolve() inside the test would happily keep passing
after the workflow's logic broke, so the one thing this file must never do
is restate the script.

The critical property under test: this field is strictly additive. A site
that never sets it (every site in the fleet today) must resolve to the exact
same "" that compiler_embed_fonts/compiler_inline_body_css already resolve to
when absent — i.e. the new field changes nothing for existing sites.

Run with: make test   (or: python3 tests/test_resolve_compiler_flags.py)
Requires: PyYAML, python3 — both present on the runner image.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

CONFIG_STEP = "Parse inventory and resolve deployments"
DEPLOYMENTS_JSON_DELIM = "DEPLOYMENTS_JSON_EOF"

# Mirrors the shape of inventory/casaboa/casaboa-dev.yaml in the fleet
# inventory repo — a real, live dev site whose compiler: block carries none
# of embed_fonts/inline_body_css/allow_blanket_robots_disallow today.
BASE_INVENTORY = {
    "github_environment": "example-dev",
    "sources": {
        "website": {"repo": "FelipeFuhr/example-website", "ref": "main"},
    },
    "compiler": {
        "repo": "FelipeFuhr/ffreis-website-compiler",
        "ref": "main",
        "raster_inline_threshold": 2147483647,
    },
    "builds": {"bucket": "example-website-builds-dev", "region": "us-east-1"},
    "publish": {"bucket": "example-website-dev", "region": "us-east-1"},
}


def load_config_steps():
    with DEPLOY_YML.open() as f:
        doc = yaml.safe_load(f)
    return doc["jobs"]["config"]["steps"]


def find_step(steps, name):
    """Return the single step called `name`, or raise KeyError."""
    matches = [s for s in steps if s.get("name") == name]
    if len(matches) != 1:
        raise KeyError(f"expected exactly 1 step named {name!r}, found {len(matches)}")
    return matches[0]


class TestResolveCompilerAllowBlanketRobotsDisallow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = find_step(load_config_steps(), CONFIG_STEP)["run"]

    def test_locator_is_not_vacuous(self):
        # Control for every other test in this class: if find_step() quietly
        # returned something for a name that is not in the workflow, a
        # deleted/renamed step would look like a passing test suite.
        with self.assertRaises(KeyError):
            find_step(load_config_steps(), "No Such Step In This Workflow")

    def run_resolve(self, compiler_overrides=None):
        """Write a synthetic inventory fixture, run the REAL config step's
        script against it, and return the single resolved deployment dict."""
        inventory = copy.deepcopy(BASE_INVENTORY)
        if compiler_overrides:
            inventory["compiler"].update(compiler_overrides)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / "example-dev.yaml").write_text(yaml.safe_dump(inventory))

        github_output = workspace / "github_output"
        github_output.write_text("")

        env = dict(os.environ)
        env.update(
            WEBSITE_NAME="example-dev",
            DEPLOYMENT_NAME="",
            GITHUB_EVENT_NAME="workflow_dispatch",
            GITHUB_OUTPUT=str(github_output),
        )
        result = subprocess.run(
            ["bash", "-c", self.script], cwd=workspace, env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        output = github_output.read_text()
        marker = f"deployments_json<<{DEPLOYMENTS_JSON_DELIM}\n"
        start = output.index(marker) + len(marker)
        end = output.index(f"\n{DEPLOYMENTS_JSON_DELIM}", start)
        deployments = json.loads(output[start:end])
        self.assertEqual(len(deployments), 1)
        return deployments[0]

    def test_field_absent_resolves_exactly_like_every_live_site_today(self):
        # No embed_fonts/inline_body_css/allow_blanket_robots_disallow key at
        # all — the real state of every fleet site's compiler: block today
        # (verified against inventory/casaboa/casaboa-dev.yaml).
        resolved = self.run_resolve()
        self.assertEqual(resolved["compiler_allow_blanket_robots_disallow"], "")
        # Sibling flags this field is modeled on must resolve identically —
        # proves the new field's addition didn't perturb pre-existing ones.
        self.assertEqual(resolved["compiler_embed_fonts"], "")
        self.assertEqual(resolved["compiler_inline_body_css"], "")

    def test_explicit_true_resolves_to_the_literal_string_true(self):
        resolved = self.run_resolve({"allow_blanket_robots_disallow": True})
        self.assertEqual(resolved["compiler_allow_blanket_robots_disallow"], "true")

    def test_explicit_false_resolves_the_same_as_absent(self):
        resolved = self.run_resolve({"allow_blanket_robots_disallow": False})
        self.assertEqual(resolved["compiler_allow_blanket_robots_disallow"], "")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)

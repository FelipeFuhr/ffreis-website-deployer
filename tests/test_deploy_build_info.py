#!/usr/bin/env python3
"""Contract tests for deploy.yml's "Stamp build-info.json" step.

These tests run the step's REAL script body — extracted from
.github/workflows/deploy.yml, not a copy pasted in here — against a throwaway
workspace, and assert the JSON it writes. A reimplementation of the step
inside the test would happily keep passing after the workflow broke, so the
one thing this file must never do is restate the script.

Run with: make test   (or: python3 tests/test_deploy_build_info.py)
Requires: PyYAML, git, python3 — all present on the runner image.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

STAMP_STEP = "Stamp build-info.json"
UPLOAD_STEP = "Upload build artifact to staging bucket"

# The keys the published stamp is contracted to carry. Consumers (site dev
# panels) read sha/branch/deployedAt by these exact names; changing one is a
# breaking change for every site that reads /build-info.json.
EXPECTED_KEYS = {"sha", "branch", "deployedAt", "website", "deployment"}

ISO_8601_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def load_build_steps():
    with DEPLOY_YML.open() as f:
        doc = yaml.safe_load(f)
    return doc["jobs"]["build"]["steps"]


def find_step(steps, name):
    """Return the single step called `name`, or raise KeyError."""
    matches = [s for s in steps if s.get("name") == name]
    if len(matches) != 1:
        raise KeyError(f"expected exactly 1 step named {name!r}, found {len(matches)}")
    return matches[0]


def step_index(steps, name):
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i
    raise KeyError(name)


class TestStampStepShape(unittest.TestCase):
    """Static assertions about how the step is wired into the build job."""

    def setUp(self):
        self.steps = load_build_steps()
        self.step = find_step(self.steps, STAMP_STEP)

    def test_locator_is_not_vacuous(self):
        # Control for every other test in this class: if find_step() quietly
        # returned something for a name that is not in the workflow, a deleted
        # step would look like a passing test.
        with self.assertRaises(KeyError):
            find_step(self.steps, "No Such Step In This Workflow")

    def test_runs_only_for_dev_environments(self):
        self.assertIn("-dev", self.step.get("if", ""))
        self.assertIn("github_env", self.step.get("if", ""))

    def test_script_body_carries_no_workflow_expressions(self):
        # Every input arrives via env:, which is both the injection-safe shape
        # and what makes the body executable outside Actions (below).
        self.assertNotIn("${{", self.step["run"])

    def test_every_variable_the_body_reads_is_declared_in_env(self):
        declared = set(self.step["env"])
        referenced = set(re.findall(r'os\.environ\["([A-Z_]+)"\]', self.step["run"]))
        referenced |= set(re.findall(r'\$\{?([A-Z_]+)\}?', self.step["run"]))
        # Values the body computes for itself, not workflow inputs.
        computed = {"BUILD_SHA", "DEPLOYED_AT"}
        self.assertTrue(
            (referenced - computed) <= declared,
            f"undeclared env vars: {sorted((referenced - computed) - declared)}",
        )

    def test_stamp_is_written_before_the_artifact_is_uploaded(self):
        # The stamp reaches S3 only because it lands in dist/ ahead of the
        # sync. Reordering these two steps would publish nothing, silently.
        self.assertLess(
            step_index(self.steps, STAMP_STEP),
            step_index(self.steps, UPLOAD_STEP),
        )


class TestStampStepBehavior(unittest.TestCase):
    """Executes the step's real script body and inspects what it wrote."""

    @classmethod
    def setUpClass(cls):
        cls.script = find_step(load_build_steps(), STAMP_STEP)["run"]

    def run_step(self, *, website="example-dev", deployment="production", ref="develop"):
        """Run the step body in a scratch workspace; return (info, sha)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        website_dir = workspace / "checkout" / "website"
        website_dir.mkdir(parents=True)

        git = ["git", "-C", str(website_dir)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["config", "user.email", "t@example.invalid"], check=True)
        subprocess.run(git + ["config", "user.name", "test"], check=True)
        (website_dir / "index.html").write_text("<!doctype html>\n")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "fixture"], check=True)
        sha = subprocess.run(
            git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

        env = dict(os.environ)
        env.update(
            WEBSITE_DIR=str(website_dir),
            OUT_DIR=str(workspace / "dist"),
            WEBSITE_NAME=website,
            DEPLOYMENT=deployment,
            WEBSITE_REF=ref,
        )
        result = subprocess.run(
            ["bash", "-c", self.script], env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        stamp = workspace / "dist" / "build-info.json"
        self.assertTrue(stamp.is_file(), "build-info.json was not written")
        with stamp.open() as f:
            return json.load(f), sha

    def test_publishes_exactly_the_contracted_keys(self):
        info, _ = self.run_step()
        self.assertEqual(set(info), EXPECTED_KEYS)

    def test_sha_is_the_checked_out_revision_not_the_requested_ref(self):
        info, sha = self.run_step(ref="develop")
        self.assertEqual(info["sha"], sha)
        self.assertEqual(len(info["sha"]), 40)

    def test_branch_website_and_deployment_come_from_the_deployment_config(self):
        info, _ = self.run_step(website="example-dev", deployment="en", ref="develop")
        self.assertEqual(info["branch"], "develop")
        self.assertEqual(info["website"], "example-dev")
        self.assertEqual(info["deployment"], "en")

    def test_deployed_at_is_an_iso_8601_utc_timestamp(self):
        info, _ = self.run_step()
        self.assertRegex(info["deployedAt"], ISO_8601_UTC)

    def test_creates_the_output_directory_when_the_build_produced_none(self):
        # mkdir -p, not a bare redirect: a site whose compile emitted nothing
        # must still fail at the sync, never at the stamp.
        info, _ = self.run_step()
        self.assertIn("sha", info)

    def test_fails_loudly_when_the_website_checkout_is_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        env = dict(os.environ)
        env.update(
            WEBSITE_DIR=str(workspace / "checkout" / "website"),
            OUT_DIR=str(workspace / "dist"),
            WEBSITE_NAME="example-dev",
            DEPLOYMENT="production",
            WEBSITE_REF="develop",
        )
        result = subprocess.run(
            ["bash", "-c", self.script], env=env, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0, "a missing checkout must fail the job")
        self.assertFalse((workspace / "dist" / "build-info.json").exists())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)

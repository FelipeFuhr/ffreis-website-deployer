#!/usr/bin/env python3
"""Tests for the inventory guards in the deploy workflow's config step.

These guards are the last thing standing between an inventory typo and a
production deploy: they are what stops mock content and draft content from
being requested for a non-dev environment. They had no tests.

The step under test is inline Python inside `.github/workflows/deploy.yml`.
Rather than copy it here — a copy drifts, and then the tests pass while the
shipped code is wrong — each test EXTRACTS the step from the workflow and runs
it as the workflow runs it: a fixture inventory on disk, the same environment
variables, and `$GITHUB_OUTPUT` captured from a real file.

Run with `make test` or `python3 -m unittest discover -s tests`.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
STEP_MARKER = "Parse inventory and resolve deployments"


def extract_config_step() -> str:
    """Return the config step's Python body, verbatim from the workflow."""
    import yaml

    doc = yaml.safe_load(WORKFLOW.read_text())
    for job in doc["jobs"].values():
        for step in job.get("steps", []) or []:
            if step.get("name") == STEP_MARKER:
                run = step["run"]
                body = run.split("python3 - <<'PYEOF'\n", 1)[1].rsplit("PYEOF", 1)[0]
                return textwrap.dedent(body)
    raise AssertionError(f"step {STEP_MARKER!r} not found in {WORKFLOW}")


CONFIG_STEP = extract_config_step()

BASE_INVENTORY = {
    "website": "probe",
    "deploy_mode": "manual",
    "sources": {
        "website": {"repo": "org/site", "ref": "main"},
        "data": {"repo": "org/data", "ref": "main"},
    },
    "compiler": {"repo": "org/compiler", "ref": "main"},
    "builds": {"bucket": "probe-builds", "region": "us-east-1"},
    "publish": {"bucket": "probe-live", "region": "us-east-1"},
    "github_environment": "prod",
    "deployments": {"en": {"publish": {"prefix": "en/"}}},
}


class ConfigStepResult:
    def __init__(self, returncode: int, stderr: str, outputs: dict):
        self.returncode = returncode
        self.stderr = stderr
        self.outputs = outputs

    @property
    def deployments(self) -> list:
        raw = self.outputs.get("deployments_json", "[]")
        return json.loads(raw)


def run_config_step(inventory: dict, *, event: str = "workflow_dispatch") -> ConfigStepResult:
    """Run the extracted step against a fixture inventory, as the workflow does."""
    import yaml

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "inventory").mkdir()
        (tmp_path / "inventory" / "probe.yaml").write_text(yaml.safe_dump(inventory))

        script = tmp_path / "config_step.py"
        script.write_text(CONFIG_STEP)
        output_file = tmp_path / "github_output"
        output_file.touch()

        env = {
            **os.environ,
            "WEBSITE_NAME": inventory["website"],
            "DEPLOYMENT_NAME": "",
            "GITHUB_EVENT_NAME": event,
            "GITHUB_OUTPUT": str(output_file),
        }
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
        )
        return ConfigStepResult(proc.returncode, proc.stderr, parse_github_output(output_file.read_text()))


def parse_github_output(raw: str) -> dict:
    """Parse the $GITHUB_OUTPUT format, including heredoc-delimited values."""
    outputs, lines, i = {}, raw.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        heredoc = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)<<(\S+)$", line)
        if heredoc:
            key, delim = heredoc.groups()
            i += 1
            collected = []
            while i < len(lines) and lines[i] != delim:
                collected.append(lines[i])
                i += 1
            outputs[key] = "\n".join(collected)
        elif "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
        i += 1
    return outputs


def inventory(**overrides) -> dict:
    cfg = json.loads(json.dumps(BASE_INVENTORY))
    cfg.update(overrides)
    return cfg


class TestUnwantedConfigurationsAreRejected(unittest.TestCase):
    """Each of these would put unfinished or fake content on a live site."""

    def assert_fatal(self, result: ConfigStepResult, *expected_fragments: str):
        self.assertNotEqual(result.returncode, 0, f"expected a fatal error, got success: {result.outputs}")
        for fragment in expected_fragments:
            self.assertIn(fragment, result.stderr)

    def test_mock_content_on_a_prod_environment_is_fatal(self):
        result = run_config_step(inventory(content_source="mock", github_environment="prod"))
        self.assert_fatal(result, "content_source=mock", "must never reach production")

    def test_mock_content_on_a_non_dev_named_environment_is_fatal(self):
        # "staging" is not a dev environment by the -dev suffix rule.
        result = run_config_step(inventory(content_source="mock", github_environment="staging"))
        self.assert_fatal(result, "content_source=mock")

    def test_drafts_on_a_prod_environment_are_fatal(self):
        result = run_config_step(inventory(include_drafts=True, github_environment="prod"))
        self.assert_fatal(result, "include_drafts", "Draft content must never reach production")

    def test_drafts_on_a_non_dev_named_environment_are_fatal(self):
        result = run_config_step(inventory(include_drafts=True, github_environment="production-dev-team"))
        self.assert_fatal(result, "include_drafts")

    def test_setting_both_section_keys_is_fatal(self):
        result = run_config_step(inventory(
            enable_sections=["blog"], disable_sections=["courses"],
            github_environment="ffreis-dev",
        ))
        self.assert_fatal(result, "both enable_sections and disable_sections")


class TestWantedConfigurationsResolve(unittest.TestCase):
    """The permitted shapes must actually work — otherwise the guards above
    would pass by rejecting everything."""

    def assert_ok(self, result: ConfigStepResult) -> dict:
        self.assertEqual(result.returncode, 0, f"expected success, got stderr: {result.stderr}")
        deployments = result.deployments
        self.assertTrue(deployments, "expected at least one resolved deployment")
        return deployments[0]

    def test_mock_content_on_a_dev_environment_resolves(self):
        dep = self.assert_ok(run_config_step(inventory(
            content_source="mock", github_environment="ffreis-dev")))
        self.assertEqual(dep["content_source"], "mock")

    def test_drafts_on_a_dev_environment_resolve(self):
        dep = self.assert_ok(run_config_step(inventory(
            include_drafts=True, github_environment="ffreis-dev")))
        self.assertEqual(dep["include_drafts"], "true")

    def test_enable_sections_reaches_the_compiler_flag(self):
        dep = self.assert_ok(run_config_step(inventory(
            enable_sections=["blog", "projects"], github_environment="ffreis-dev")))
        self.assertEqual(dep["enable_sections"], "blog,projects")
        self.assertEqual(dep["disable_sections"], "")

    def test_disable_sections_still_works_for_a_site_without_a_registry(self):
        dep = self.assert_ok(run_config_step(inventory(
            disable_sections=["blog", "courses"], github_environment="prod")))
        self.assertEqual(dep["disable_sections"], "blog,courses")
        self.assertEqual(dep["enable_sections"], "")

    def test_a_plain_prod_inventory_requests_neither(self):
        dep = self.assert_ok(run_config_step(inventory()))
        self.assertEqual(dep["enable_sections"], "")
        self.assertEqual(dep["disable_sections"], "")
        self.assertEqual(dep["include_drafts"], "")
        self.assertEqual(dep["content_source"], "prod")

    def test_drafts_explicitly_disabled_on_prod_is_allowed(self):
        # include_drafts: false is a statement of intent, not a violation.
        dep = self.assert_ok(run_config_step(inventory(
            include_drafts=False, github_environment="prod")))
        self.assertEqual(dep["include_drafts"], "")

    def test_empty_enable_sections_does_not_trip_the_mutual_exclusion(self):
        # An empty list is not "both keys set"; rejecting it would make the
        # guard fire on a harmless leftover key.
        dep = self.assert_ok(run_config_step(inventory(
            enable_sections=[], disable_sections=["blog"], github_environment="prod")))
        self.assertEqual(dep["disable_sections"], "blog")


class TestExtractionIsNotVacuous(unittest.TestCase):
    """If the extraction silently returned nothing, every test above would pass
    against an empty script. Pin that it found the real code."""

    def test_extracted_step_contains_the_guards(self):
        for fragment in (
            "content_source",
            "include_drafts",
            "enable_sections",
            "must never reach production",
        ):
            self.assertIn(fragment, CONFIG_STEP)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Contract tests for deploy.yml's post-deploy live-content verification.

These tests run the REAL script bodies — extracted from
.github/workflows/deploy.yml, not copies pasted in here — against stubbed
`aws` and `curl` binaries on PATH. A reimplementation of the step inside the
test would happily keep passing after the workflow broke, so the one thing
this file must never do is restate the script.

WHAT THIS EXISTS TO CATCH
-------------------------
On 2026-10-18 forma.ffreis.com was found serving, at `/login.html` and
`/account.html`, markup a fix had removed six days earlier. Every deploy in
between reported success. The gate at the time — a step named "Smoke test —
verify live site is reachable" — could not have caught it, three times over:

  1. it asserted only the status code (`[[ "$STATUS" =~ ^[23] ]]`), and a
     stale object returns 200;
  2. it ran BEFORE "Invalidate CloudFront cache", so it read the pre-deploy
     cached object by construction;
  3. `smoke_paths` defaulted to `["/"]`, so a deployment published under
     `publish.prefix: en/` had its only check aimed at the bucket ROOT — a
     path that deployment does not write to at all.

So the three properties under test are: content, not status; after the
invalidation has completed; at the prefix the deployment actually maintains.
`test_a_200_serving_stale_bytes_fails` is the one that matters — it is the
exact shape of the incident, and it is red against the pre-fix workflow.

Run with: make test   (or: python3 tests/test_verify_live_content.py)
Requires: PyYAML, python3, bash, sha256sum — all present on the runner image.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

CONFIG_STEP = "Parse inventory and resolve deployments"
PROMOTE_STEP = "Promote build to live bucket"
INVALIDATE_STEP = "Invalidate CloudFront cache"
VERIFY_STEP = "Verify live content matches the promoted artifact"
ORPHAN_STEP = "Scan publish bucket for unmanaged (orphan) objects"

DEPLOYMENTS_JSON_DELIM = "DEPLOYMENTS_JSON_EOF"

# Mirrors inventory/forma/forma-dev.yaml, the site the incident happened on:
# two deployments, both prefixed, so NOTHING is declared for the bucket root.
FORMA_SHAPED_INVENTORY = {
    "github_environment": "example-dev",
    "sources": {"website": {"repo": "FelipeFuhr/example-website", "ref": "develop"}},
    "compiler": {"repo": "FelipeFuhr/ffreis-website-compiler", "ref": "v1.1.1"},
    "builds": {"bucket": "example-website-builds-dev", "region": "us-east-1"},
    "publish": {"bucket": "example-website-dev", "region": "us-east-1"},
    "deployments": {
        "en": {"publish": {"prefix": "en/"}, "deploy_mode": "auto"},
        "pt": {"publish": {"prefix": "pt/"}, "deploy_mode": "auto"},
    },
}

# A legacy single-deployment site that publishes straight to the bucket root
# (knowalchemy, the dashboards, watermarkdetector).
ROOT_SHAPED_INVENTORY = {
    "github_environment": "example-prod",
    "sources": {"website": {"repo": "FelipeFuhr/example-website", "ref": "main"}},
    "compiler": {"repo": "FelipeFuhr/ffreis-website-compiler", "ref": "v1.1.1"},
    "builds": {"bucket": "example-website-builds", "region": "us-east-1"},
    "publish": {"bucket": "example-website-prod", "region": "us-east-1"},
}


def load_steps(job):
    with DEPLOY_YML.open() as f:
        doc = yaml.safe_load(f)
    return doc["jobs"][job]["steps"]


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


# ── stub binaries ─────────────────────────────────────────────────────────────
#
# The step bodies reach the outside world only through `aws` and `curl`. Both
# are replaced with python stubs driven by a JSON scenario file, so a test can
# describe a live site ("this URL answers 200 with these bytes") and let the
# real script draw its own conclusion.

AWS_STUB = r'''#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path

scenario = json.loads(Path(os.environ["STUB_SCENARIO"]).read_text())
log = Path(os.environ["STUB_CALL_LOG"])
argv = sys.argv[1:]
with log.open("a") as f:
    f.write("aws " + " ".join(argv) + "\n")


def flag(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


if argv[:2] == ["cloudfront", "create-invalidation"]:
    print(scenario.get("invalidation_id", "I1234567890ABC"))
elif argv[:3] == ["cloudfront", "wait", "invalidation-completed"]:
    if scenario.get("wait_fails"):
        print("An error occurred (AccessDenied)", file=sys.stderr)
        sys.exit(254)
elif argv[:2] == ["cloudfront", "get-distribution"]:
    query = flag("--query", "")
    if "Aliases" in query:
        print(json.dumps(scenario.get("aliases", ["example.test"])))
    elif "DomainName" in query:
        print(scenario.get("cf_domain", "d111.cloudfront.net"))
    else:
        print(json.dumps({"Distribution": {"DistributionConfig": scenario.get(
            "distribution_config",
            {"DefaultRootObject": "index.html",
             "CustomErrorResponses": {"Items": [{"ResponsePagePath": "/404.html"}]}},
        )}}))
elif argv[:2] == ["s3", "cp"]:
    # Only the HTML-filtered recursive copy of the staged artifact is used.
    src, dest = argv[2], argv[3]
    staged = Path(os.environ["STUB_STAGED_DIR"])
    for path in staged.rglob("*.html"):
        target = Path(dest) / path.relative_to(staged)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
elif argv[:2] == ["s3api", "list-objects-v2"]:
    print(json.dumps(scenario.get("bucket_objects", [])))
else:
    print(f"aws stub: unhandled {argv}", file=sys.stderr)
    sys.exit(2)
'''

CURL_STUB = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

scenario = json.loads(Path(os.environ["STUB_SCENARIO"]).read_text())
log = Path(os.environ["STUB_CALL_LOG"])
argv = sys.argv[1:]

out = argv[argv.index("-o") + 1]
url = argv[-1]
cookie = argv[argv.index("--cookie") + 1] if "--cookie" in argv else None

with log.open("a") as f:
    f.write(f"curl {url} cookie={cookie}\n")

site = scenario.get("site", {})
gate = scenario.get("gate")  # None, or the token the gate demands

if gate is not None and cookie != f"dev_access={gate}":
    # A CloudFront viewer-request gate answers before the origin: 403, no body.
    Path(out).write_bytes(b"")
    print("403")
    sys.exit(0)

entry = site.get(url)
if entry is None:
    Path(out).write_text(scenario.get("error_page_body", "<html>404</html>"))
    print("404")
    sys.exit(0)

Path(out).write_text(entry["body"])
print(str(entry.get("status", 200)))
'''


class StubEnvironment:
    """A scratch workspace with stubbed `aws`/`curl` on PATH."""

    def __init__(self, testcase, *, staged: dict, scenario: dict):
        tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.staged_dir = self.root / "staged"
        for rel, body in staged.items():
            path = self.staged_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        for name, source in (("aws", AWS_STUB), ("curl", CURL_STUB)):
            target = self.bin_dir / name
            target.write_text(source)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)

        self.scenario_file = self.root / "scenario.json"
        self.scenario_file.write_text(json.dumps(scenario))
        self.call_log = self.root / "calls.log"
        self.call_log.write_text("")
        self.step_summary = self.root / "summary.md"
        self.step_summary.write_text("")
        self.github_output = self.root / "github_output"
        self.github_output.write_text("")

    def env(self, **overrides):
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin_dir}:{env['PATH']}",
            STUB_SCENARIO=str(self.scenario_file),
            STUB_CALL_LOG=str(self.call_log),
            STUB_STAGED_DIR=str(self.staged_dir),
            GITHUB_STEP_SUMMARY=str(self.step_summary),
            GITHUB_OUTPUT=str(self.github_output),
        )
        env.update({k: str(v) for k, v in overrides.items()})
        return env

    def run(self, script, **overrides):
        return subprocess.run(
            ["bash", "-c", script],
            cwd=self.root,
            env=self.env(**overrides),
            capture_output=True,
            text=True,
        )

    @property
    def calls(self):
        return self.call_log.read_text().splitlines()

    @property
    def summary(self):
        return self.step_summary.read_text()


def verify_env(**overrides):
    """Default env for the verification step; mirrors the workflow's own env:."""
    base = dict(
        BUILDS_BUCKET="example-website-builds-dev",
        BUILD_SHA="abcdef1234567890abcdef1234567890abcdef12",
        CF_DISTRIBUTION_ID="E1EXAMPLE",
        CF_INVALIDATION_ID="I1234567890ABC",
        DEV_ACCESS_TOKEN="",
        GITHUB_ENV_NAME="example-dev",
        PUBLISH_PREFIX="en/",
        REGION="us-east-1",
        SKIP_SMOKE="",
        SMOKE_PATHS="",
        STAGING_PREFIX="example-dev/en",
        VERIFY_MAX_FILES="40",
        # No retry window: a test asserts the verdict, never propagation timing.
        VERIFY_DEADLINE_SECONDS="0",
        VERIFY_RETRY_SLEEP="0",
    )
    base.update(overrides)
    return base


CURRENT = "<!doctype html><title>current</title>"
STALE = "<!doctype html><title>stale, from six days ago</title>"


class TestVerifyStepShape(unittest.TestCase):
    """Static assertions about how the gate is wired into the promote job."""

    def setUp(self):
        self.steps = load_steps("promote")

    def test_locator_is_not_vacuous(self):
        # Control for every other test here: if find_step() quietly returned
        # something for a name that is not in the workflow, a deleted step
        # would look like a passing suite.
        with self.assertRaises(KeyError):
            find_step(self.steps, "No Such Step In This Workflow")

    def test_verification_runs_after_the_invalidation(self):
        # Reason (2) the incident went unseen: the old smoke test ran between
        # the promote and the invalidation, so it read the PREVIOUS deploy's
        # cached object every single time. Order is the fix; keep it.
        self.assertLess(
            step_index(self.steps, PROMOTE_STEP),
            step_index(self.steps, INVALIDATE_STEP),
        )
        self.assertLess(
            step_index(self.steps, INVALIDATE_STEP),
            step_index(self.steps, VERIFY_STEP),
        )

    def test_verification_waits_on_the_invalidation_rather_than_sleeping(self):
        body = find_step(self.steps, VERIFY_STEP)["run"]
        self.assertIn("aws cloudfront wait invalidation-completed", body)
        self.assertIn("CF_INVALIDATION_ID", body)

    def test_invalidation_publishes_the_id_the_verification_waits_on(self):
        invalidate = find_step(self.steps, INVALIDATE_STEP)
        self.assertEqual(invalidate.get("id"), "invalidate")
        self.assertIn("invalidation_id=", invalidate["run"])
        verify = find_step(self.steps, VERIFY_STEP)
        self.assertEqual(
            verify["env"]["CF_INVALIDATION_ID"],
            "${{ steps.invalidate.outputs.invalidation_id }}",
        )

    def test_verification_compares_a_content_hash_not_just_a_status(self):
        # Reason (1): stale content returns 200. A status assertion cannot see
        # it; a sha256 of the body against the staged artifact can.
        body = find_step(self.steps, VERIFY_STEP)["run"]
        self.assertIn("sha256sum", body)

    def test_the_status_only_smoke_step_is_gone(self):
        # The step this replaces. If it comes back, the fleet has two gates
        # disagreeing about what "verified" means.
        with self.assertRaises(KeyError):
            find_step(self.steps, "Smoke test — verify live site is reachable")

    def test_script_bodies_carry_no_workflow_expressions(self):
        # Every input arrives via env:, which is both the injection-safe shape
        # and what makes these bodies executable outside Actions (below).
        for name in (VERIFY_STEP, ORPHAN_STEP, INVALIDATE_STEP):
            self.assertNotIn("${{", find_step(self.steps, name)["run"], name)

    def test_every_variable_the_bodies_read_is_declared_in_env(self):
        import re

        # Values the bodies compute for themselves, plus Actions-provided ones.
        computed = {
            "ALIASES", "ALIASES_JSON", "ATTEMPT", "BODY", "CHECKED", "CURL_AUTH",
            "DEADLINE", "DIST_JSON", "DOMAIN", "FAILED", "GOT", "INVALIDATION_ID",
            "LIVE_URL", "ORDERED", "PENDING", "PROBE_BODY", "PROBE_SIZE",
            "PROBE_STATUS", "PROBE_URL", "REASON", "SHA7", "SRC", "STAGED",
            "STAGED_HTML", "STATUS", "SUMMARY_MD", "URL", "URL_PREFIX", "WANT",
            "BUCKET_OBJECTS", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "LC_ALL",
        }
        for name in (VERIFY_STEP, ORPHAN_STEP):
            step = find_step(self.steps, name)
            declared = set(step["env"])
            referenced = set(re.findall(r'\$\{?([A-Z_][A-Z0-9_]*)\}?', step["run"]))
            referenced |= set(re.findall(r'os\.environ\[."([A-Z_]+)".\]', step["run"]))
            undeclared = referenced - computed - declared
            self.assertEqual(undeclared, set(), f"{name}: undeclared env vars")


class TestVerifyStepBehavior(unittest.TestCase):
    """Executes the real verification body against a stubbed live site."""

    @classmethod
    def setUpClass(cls):
        cls.script = find_step(load_steps("promote"), VERIFY_STEP)["run"]

    def scenario(self, *, site, **extra):
        base = {"aliases": ["example.test"], "site": site}
        base.update(extra)
        return base

    # ── the incident ─────────────────────────────────────────────────────────

    def test_a_200_serving_stale_bytes_fails(self):
        """THE test. A 200 whose body is not what was just promoted must FAIL.

        This is forma.ffreis.com/login.html on 2026-10-18: the object is
        reachable, the status is 200, and the bytes are six days old. The gate
        this replaces reported `ok` for exactly this state.
        """
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT, "login.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/en/index.html": {"body": CURRENT},
                "https://example.test/en/login.html": {"status": 200, "body": STALE},
            }),
        )
        result = env.run(self.script, **verify_env())
        self.assertNotEqual(
            result.returncode, 0,
            "a 200 serving bytes other than the promoted artifact must fail the deploy\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        # The per-path detail goes to stderr; the annotation to stdout.
        output = result.stdout + result.stderr
        self.assertIn("STALE", output)
        self.assertIn("login.html", output)
        # The finding has to name both hashes, or nobody can act on it.
        self.assertIn("artifact sha256", output)
        self.assertIn(
            "::error title=Live content does not match the promoted artifact",
            result.stdout,
        )
        self.assertIn("Live content verification: FAILED", env.summary)
        # ...and it must not also claim success.
        self.assertNotIn("PASSED", env.summary)

    def test_matching_bytes_pass(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT, "login.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/en/index.html": {"body": CURRENT},
                "https://example.test/en/login.html": {"body": CURRENT},
            }),
        )
        result = env.run(self.script, **verify_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sha256 matches the promoted artifact", result.stdout)
        self.assertIn("Live content verification: PASSED", env.summary)

    def test_a_missing_object_fails(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT, "gone.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/en/index.html": {"body": CURRENT},
            }),
        )
        result = env.run(self.script, **verify_env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("unreachable", result.stdout + result.stderr)

    # ── prefix awareness ─────────────────────────────────────────────────────

    def test_a_prefixed_deployment_is_verified_under_its_prefix(self):
        """Reason (3): the old default aimed the check at the bucket root."""
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/en/index.html": {"body": CURRENT},
                # The unmanaged root still serves its frozen copy, as on forma.
                "https://example.test/index.html": {"body": STALE},
            }),
        )
        result = env.run(self.script, **verify_env(PUBLISH_PREFIX="en/"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        fetched = [c for c in env.calls if c.startswith("curl ")]
        self.assertTrue(all("/en/" in c for c in fetched), fetched)
        self.assertNotIn("curl https://example.test/index.html cookie=None", env.calls)

    def test_an_unprefixed_deployment_is_verified_at_the_root(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/index.html": {"body": CURRENT},
            }),
        )
        result = env.run(self.script, **verify_env(PUBLISH_PREFIX=""))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ── ordering ─────────────────────────────────────────────────────────────

    def test_the_edge_is_not_read_until_the_invalidation_has_completed(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={
                "https://example.test/en/index.html": {"body": CURRENT},
            }),
        )
        result = env.run(self.script, **verify_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        waits = [i for i, c in enumerate(env.calls) if "wait invalidation-completed" in c]
        curls = [i for i, c in enumerate(env.calls) if c.startswith("curl ")]
        self.assertTrue(waits, "the step must wait on the invalidation")
        self.assertLess(waits[0], curls[0], env.calls)

    def test_a_missing_invalidation_id_refuses_to_verify(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={}),
        )
        result = env.run(self.script, **verify_env(CF_INVALIDATION_ID=""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to verify", result.stderr)

    def test_an_unwaitable_invalidation_warns_but_still_verifies(self):
        # A deploy role without cloudfront:GetInvalidation must not red every
        # deploy — but it must not quietly stop verifying either.
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(
                site={"https://example.test/en/index.html": {"body": STALE}},
                wait_fails=True,
            ),
        )
        result = env.run(self.script, **verify_env())
        self.assertIn("::warning title=Could not wait on the invalidation", result.stdout)
        self.assertNotEqual(result.returncode, 0, "stale content still has to fail")

    # ── gated dev sites ──────────────────────────────────────────────────────

    def test_a_gated_site_without_a_token_skips_loudly_and_never_claims_a_pass(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={}, gate="s3cret"),
        )
        result = env.run(self.script, **verify_env(DEV_ACCESS_TOKEN=""))
        # A skip is tolerable; a silent pass is not.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning title=Live content NOT verified (site is gated)", result.stdout)
        self.assertIn("SKIPPED (gated)", env.summary)
        self.assertNotIn("PASSED", env.summary)

    def test_a_gated_site_with_the_right_token_is_fully_verified(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT, "account.html": STALE},
            scenario=self.scenario(
                site={
                    "https://example.test/en/index.html": {"body": CURRENT},
                    "https://example.test/en/account.html": {"body": STALE},
                },
                gate="s3cret",
            ),
        )
        result = env.run(self.script, **verify_env(DEV_ACCESS_TOKEN="s3cret"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            all("cookie=dev_access=s3cret" in c for c in env.calls if c.startswith("curl ")),
            env.calls,
        )

    def test_a_gated_site_with_a_rejected_token_fails(self):
        # A supplied-but-wrong token is a configuration error, not a reason to
        # skip: somebody believed this site was being verified.
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={}, gate="s3cret"),
        )
        result = env.run(self.script, **verify_env(DEV_ACCESS_TOKEN="wrong"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("::error title=dev_access token rejected", result.stdout)

    def test_a_gate_is_not_confused_with_a_missing_object(self):
        # A viewer-request gate answers 403 with an EMPTY body before the
        # origin; a missing object reaches the origin and comes back as the
        # error page, with a body. Only the first is a skip.
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={}, error_page_body="<html>the 404 page</html>"),
        )
        result = env.run(self.script, **verify_env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("SKIPPED", env.summary)

    # ── opt-out ──────────────────────────────────────────────────────────────

    def test_skip_smoke_still_opts_out_but_now_says_so_loudly(self):
        env = StubEnvironment(
            self,
            staged={"index.html": CURRENT},
            scenario=self.scenario(site={}),
        )
        result = env.run(self.script, **verify_env(SKIP_SMOKE="true"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning title=Live content NOT verified", result.stdout)
        self.assertEqual([c for c in env.calls if c.startswith("curl ")], [])

    def test_an_artifact_with_no_html_is_a_broken_deploy(self):
        env = StubEnvironment(self, staged={}, scenario=self.scenario(site={}))
        result = env.run(self.script, **verify_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no HTML", result.stderr)


class TestOrphanScanBehavior(unittest.TestCase):
    """Executes the real orphan-scan body against a stubbed bucket listing."""

    @classmethod
    def setUpClass(cls):
        cls.script = find_step(load_steps("promote"), ORPHAN_STEP)["run"]

    def orphan_env(self, **overrides):
        base = dict(
            CF_DISTRIBUTION_ID="E1EXAMPLE",
            LIVE_BUCKET="example-website-dev",
            MANAGED_PREFIXES="en/ pt/",
            PUBLISH_REGION="us-east-1",
            ROOT_IS_MANAGED="",
            FAIL_ON_DIVERGENT_SHADOW="false",
        )
        base.update(overrides)
        return base

    def objects(self, *rows):
        """(key, etag, modified) triples as list-objects-v2 returns them."""
        return [[k, f'"{e}"', m] for k, e, m in rows]

    def test_a_divergent_shadow_copy_is_reported_as_stale(self):
        # The incident: /login.html frozen at the Oct-12 bytes while
        # /en/login.html is kept current.
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": self.objects(
            ("index.html", "aaa", "2026-10-12T00:00:00Z"),
            ("404.html", "bbb", "2026-10-12T00:00:00Z"),
            ("login.html", "OLD", "2026-10-12T00:00:00Z"),
            ("en/login.html", "NEW", "2026-10-18T00:00:00Z"),
            ("en/index.html", "ccc", "2026-10-18T00:00:00Z"),
            ("pt/index.html", "ddd", "2026-10-18T00:00:00Z"),
        )})
        result = env.run(self.script, **self.orphan_env())
        self.assertIn("::error title=Stale shadow copies serving a live URL", result.stdout)
        self.assertIn("login.html", result.stdout)
        self.assertIn("Stale shadow copies", env.summary)

    def test_the_distributions_own_root_objects_are_never_a_finding(self):
        # DefaultRootObject and the CustomErrorResponses pages are root objects
        # by definition on a prefixed site. "orphan ⇒ fail" or "orphan ⇒ delete"
        # would break the entry point and the error page.
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": self.objects(
            ("index.html", "aaa", "2026-10-12T00:00:00Z"),
            ("404.html", "bbb", "2026-10-12T00:00:00Z"),
            ("en/index.html", "zzz", "2026-10-18T00:00:00Z"),
            ("en/404.html", "yyy", "2026-10-18T00:00:00Z"),
        )})
        result = env.run(self.script, **self.orphan_env())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("held      index.html", result.stdout)
        self.assertIn("held      404.html", result.stdout)
        self.assertNotIn("::error", result.stdout)

    def test_a_byte_identical_duplicate_is_not_called_stale(self):
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": self.objects(
            ("robots.txt", "same", "2026-10-18T00:00:00Z"),
            ("en/robots.txt", "same", "2026-10-18T00:00:00Z"),
        )})
        result = env.run(self.script, **self.orphan_env())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("dup       robots.txt", result.stdout)
        self.assertNotIn("::error", result.stdout)

    def test_an_unmanaged_key_with_no_counterpart_only_warns(self):
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": self.objects(
            ("js/vendor.js", "aaa", "2026-10-12T00:00:00Z"),
            ("en/index.html", "bbb", "2026-10-18T00:00:00Z"),
        )})
        result = env.run(self.script, **self.orphan_env())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("::warning title=Unmanaged objects in the publish bucket", result.stdout)
        self.assertNotIn("::error", result.stdout)

    def test_divergent_shadows_do_not_fail_the_deploy_by_default(self):
        # Justification lives in the step's own comment: a pre-existing bucket
        # state that no deploy can remediate must not block shipping. The
        # escalation switch is what turns it into a gate, per site.
        objects = {"bucket_objects": self.objects(
            ("login.html", "OLD", "2026-10-12T00:00:00Z"),
            ("en/login.html", "NEW", "2026-10-18T00:00:00Z"),
        )}
        warn = StubEnvironment(self, staged={}, scenario=objects)
        self.assertEqual(warn.run(self.script, **self.orphan_env()).returncode, 0)

        gate = StubEnvironment(self, staged={}, scenario=objects)
        self.assertNotEqual(
            gate.run(self.script, **self.orphan_env(FAIL_ON_DIVERGENT_SHADOW="true")).returncode,
            0,
        )

    def test_a_root_publishing_site_is_skipped_entirely(self):
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": []})
        result = env.run(self.script, **self.orphan_env(ROOT_IS_MANAGED="true"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("publishes at the bucket root", result.stdout)
        self.assertEqual([c for c in env.calls if c.startswith("aws ")], [])

    def test_a_multipart_etag_is_reported_as_uncomparable_not_guessed(self):
        env = StubEnvironment(self, staged={}, scenario={"bucket_objects": self.objects(
            ("big.html", "abc-3", "2026-10-12T00:00:00Z"),
            ("en/big.html", "def", "2026-10-18T00:00:00Z"),
        )})
        result = env.run(self.script, **self.orphan_env())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("unknown   big.html", result.stdout)


class TestSmokePathDefaultDerivesFromThePrefix(unittest.TestCase):
    """Runs the real config step; asserts the resolved matrix entries."""

    @classmethod
    def setUpClass(cls):
        cls.script = find_step(load_steps("config"), CONFIG_STEP)["run"]

    def resolve(self, inventory, name="example-dev", deployment_filter=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / f"{name}.yaml").write_text(yaml.safe_dump(inventory))
        github_output = workspace / "github_output"
        github_output.write_text("")

        env = dict(os.environ)
        env.update(
            WEBSITE_NAME=name,
            DEPLOYMENT_NAME=deployment_filter,
            GITHUB_EVENT_NAME="workflow_dispatch",
            GITHUB_OUTPUT=str(github_output),
        )
        result = subprocess.run(
            ["bash", "-c", self.script], cwd=workspace, env=env,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = github_output.read_text()
        marker = f"deployments_json<<{DEPLOYMENTS_JSON_DELIM}\n"
        start = output.index(marker) + len(marker)
        end = output.index(f"\n{DEPLOYMENTS_JSON_DELIM}", start)
        return {d["name"]: d for d in json.loads(output[start:end])}

    def test_locator_is_not_vacuous(self):
        with self.assertRaises(KeyError):
            find_step(load_steps("config"), "No Such Step In This Workflow")

    def test_a_prefixed_deployment_no_longer_defaults_to_the_bucket_root(self):
        """Reason (3) the incident went unseen, in the config layer.

        `["/"]` for a `prefix: en/` deployment pointed the only post-deploy
        check at 60 root objects frozen since 2026-10-12.
        """
        deps = self.resolve(FORMA_SHAPED_INVENTORY)
        self.assertEqual(deps["en"]["smoke_paths"], "/en/index.html")
        self.assertEqual(deps["pt"]["smoke_paths"], "/pt/index.html")
        for dep in deps.values():
            self.assertNotEqual(dep["smoke_paths"], "/")

    def test_an_unprefixed_deployment_still_defaults_to_the_root(self):
        deps = self.resolve(ROOT_SHAPED_INVENTORY, name="example")
        self.assertEqual(deps["production"]["smoke_paths"], "/")

    def test_an_explicit_inventory_smoke_paths_still_wins(self):
        # ffreis.yaml / flemming.yaml / petlook.yaml declare their own, with
        # "/" last; the new default must not override a declared value.
        inventory = dict(FORMA_SHAPED_INVENTORY)
        inventory["publish"] = dict(inventory["publish"], smoke_paths=["/en/", "/pt/", "/"])
        deps = self.resolve(inventory)
        self.assertEqual(deps["en"]["smoke_paths"], "/en/ /pt/ /")

    def test_managed_prefixes_cover_every_sibling_even_for_one_deployment(self):
        # A single-deployment dispatch must still see its siblings' prefixes,
        # or every key the other languages maintain looks like an orphan.
        deps = self.resolve(FORMA_SHAPED_INVENTORY, deployment_filter="en")
        self.assertEqual(list(deps), ["en"])
        self.assertEqual(deps["en"]["managed_prefixes"], "en/ pt/")
        self.assertEqual(deps["en"]["root_is_managed"], "")

    def test_root_is_managed_is_set_for_a_legacy_root_publishing_site(self):
        deps = self.resolve(ROOT_SHAPED_INVENTORY, name="example")
        self.assertEqual(deps["production"]["root_is_managed"], "true")
        self.assertEqual(deps["production"]["managed_prefixes"], "")

    def test_the_prefix_resolution_itself_is_unchanged(self):
        deps = self.resolve(FORMA_SHAPED_INVENTORY)
        self.assertEqual(deps["en"]["publish_prefix"], "en/")
        self.assertEqual(deps["en"]["sibling_prefixes"], "pt/")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)

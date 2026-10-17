#!/usr/bin/env python3
"""Drift guard: verify-compiler.yml must stay a faithful replica of deploy.yml.

`verify-compiler.yml` is what gates the `stable` pointer: `promote-compiler.yml`
moves `stable` only when every fleet site builds green here. AGENTS.md calls it
a build-only replica of deploy.yml's build path, and that is exactly what makes
it useful — a candidate that verifies here is a candidate the real deploy can
run.

It had silently stopped being one. Two things were added to deploy.yml and never
to the replica:

  * `compiler.allow_blanket_robots_disallow` passthrough — so koe-dev and
    ffreis-dashboard-dev, which legitimately carry a dev-only blanket
    `Disallow: /`, failed verification on a flag their real deploys pass.
  * the `site_data.i18n` directory-swap branch of the data injection — so
    ffreis-urbs died at `cp: cannot stat 'checkout/data/i18n/site.d/.'`, the
    exact error its inventory comment says a pre-#99 deployer produces.

Four of the ten red sites in the 2026-10-16 fleet verification were these two
gaps, not broken sites. A gate that reports false reds is worse than no gate:
it makes `stable` unpromotable for reasons nobody can act on, and it trains
whoever reads it to discount a red matrix.

The repo's other contract tests (test_resolve_compiler_flags,
test_inject_data_layers, test_deploy_build_info, test_cf_invalidation_paths) all
read `deploy.yml` only, so none of them could see this. This file closes that
by comparing the two workflows directly, extracted from source — never by
restating either one's logic.

Run with: make test
"""

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"
VERIFY = REPO / ".github" / "workflows" / "verify-compiler.yml"

# deploy.yml addresses its matrix entry as `matrix.deployment`; verify-compiler
# as `matrix.entry`. That rename is the ONLY difference the two are allowed to
# have in a shared step body.
CONTEXT_RE = re.compile(r"matrix\.(?:deployment|entry)\.")
CONTEXT_CANON = "matrix.X."

# Compiler keys that belong to the real deploy only. Each needs a reason, and
# the bar is high: a verification build compiles exactly what a deploy
# compiles, so a genuine exemption is about *which* compiler to run, never
# about how it runs.
DEPLOY_ONLY_COMPILER_KEYS: dict[str, str] = {
    "compiler_ref": (
        "verify-compiler.yml deliberately ignores each site's own pin and builds "
        "every site at the candidate ref under verification — that override is the "
        "entire purpose of the workflow."
    ),
}


def steps(path: Path):
    """Yield (job_name, step) for every step in a workflow."""
    doc = yaml.safe_load(path.read_text())
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, step


def step_bodies(path: Path, name: str) -> list[str]:
    return [s["run"] for _, s in steps(path) if s.get("name") == name and "run" in s]


def canon(body: str) -> str:
    """Normalize a step body for cross-workflow comparison."""
    return CONTEXT_RE.sub(CONTEXT_CANON, body).strip()


def compiler_keys(path: Path) -> set[str]:
    """Every `"compiler_*":` key the config-resolution step emits.

    Read out of the workflow text rather than by running the resolver: this
    guard is about whether a key EXISTS in both files, which is a source-level
    question, and reading it as source means a key added in any syntax (dict
    literal, update(), f-string) still has to appear here.
    """
    return set(re.findall(r'"(compiler_[a-z0-9_]+)"\s*:', path.read_text()))


def build_static_arrays(path: Path) -> set[str]:
    """The `"${FOO_ARGS[@]}"` arrays expanded into the build-static call."""
    text = path.read_text()
    # Bound the search to the build-static invocation, so an array used by some
    # other command in the same file is not counted.
    out: set[str] = set()
    for m in re.finditer(r"run ./cmd/build-static(.*?)(?:\n\n|\n      - name:)", text, re.S):
        out |= set(re.findall(r'"\$\{([A-Z0-9_]+)\[@\]\}"', m.group(1)))
    return out


class TestCompilerFlagParity(unittest.TestCase):
    def test_every_deploy_compiler_key_is_resolved_in_verification(self):
        missing = compiler_keys(DEPLOY) - compiler_keys(VERIFY) - set(DEPLOY_ONLY_COMPILER_KEYS)
        self.assertEqual(
            missing,
            set(),
            "deploy.yml resolves compiler flags that verify-compiler.yml does not: "
            f"{sorted(missing)}. The verification build would then compile every "
            "affected site with different flags than its real deploy — which "
            "reports a red matrix for a site that deploys fine, and makes "
            "`stable` unpromotable for a reason nobody can act on. Add the key "
            "to verify-compiler.yml's resolve(), or record it in "
            "DEPLOY_ONLY_COMPILER_KEYS with the publishing-specific reason it "
            "cannot apply to a build-only run.",
        )

    def test_every_resolved_compiler_flag_reaches_build_static(self):
        """A resolved key that no arg array consumes is a silent no-op.

        This is the half of the original bug that a key-set comparison alone
        would have missed: `site_data_i18n` WAS resolved in verify-compiler and
        was correctly consumed by its build step — it was the inject step that
        ignored it. So check consumption, not just resolution.
        """
        for path, label in ((DEPLOY, "deploy.yml"), (VERIFY, "verify-compiler.yml")):
            text = path.read_text()
            arrays = build_static_arrays(path)
            self.assertTrue(arrays, f"{label}: found no build-static arg arrays — parser is broken")
            for key in compiler_keys(path):
                if key in ("compiler_repo", "compiler_ref"):
                    continue  # checkout inputs, not build flags
                # The key must be read by some array-assigning conditional.
                self.assertRegex(
                    text,
                    rf"{key}\s*\}}\}}",
                    f"{label}: resolves {key} but never reads it back — dead flag",
                )


class TestBuildStaticArgParity(unittest.TestCase):
    """The strongest of these checks, and the one that caught the third gap.

    A compiler flag need not come from the `compiler:` block at all —
    `-sibling-base-paths` is derived from each deployment's publish prefix. So a
    key-set comparison misses it entirely: verify-compiler.yml's resolve()
    docstring declared every publish field "deploy-only and irrelevant to
    compiler verification", which is exactly wrong for a publish field a
    COMPILER flag is computed from. ffreis-pt's own "/pt/blog/" was then
    reported as a broken "/blog/" — a red site with a green deploy.

    Comparing what actually reaches `build-static` is independent of where a
    value came from, so it covers flags nobody has thought of yet.
    """

    # Arrays the real deploy may pass that a build-only verification cannot.
    # Each needs a reason, and the bar is the same as for compiler keys: it must
    # be about publishing, never about compiling.
    DEPLOY_ONLY_ARRAYS: dict[str, str] = {}

    def test_build_static_receives_the_same_arg_arrays(self):
        deploy_arrays = build_static_arrays(DEPLOY)
        verify_arrays = build_static_arrays(VERIFY)
        self.assertTrue(deploy_arrays, "parser found no build-static arrays in deploy.yml")
        self.assertTrue(verify_arrays, "parser found no build-static arrays in verify-compiler.yml")

        missing = deploy_arrays - verify_arrays - set(self.DEPLOY_ONLY_ARRAYS)
        self.assertEqual(
            missing,
            set(),
            "deploy.yml expands these into build-static and verify-compiler.yml "
            f"does not: {sorted(missing)}. The verification build then compiles "
            "with a different flag set than the real deploy, so a site can fail "
            "verification over a flag its deploy passes — which blocks the "
            "`stable` promotion for a reason that does not exist. Add it to "
            "verify-compiler.yml, or record it in DEPLOY_ONLY_ARRAYS with the "
            "publishing-specific reason a build-only run cannot have it.",
        )


class TestSharedStepParity(unittest.TestCase):
    """Steps that do the same job in both workflows must be literally the same.

    deploy.yml carries two copies of the inject step (one per build path) and
    verify-compiler a third. Three hand-maintained copies is how this drifted;
    until they are extracted into one composite action, this test is what keeps
    them equal.
    """

    SHARED_STEPS = ["Inject data into website", "Inject shared JS into website"]

    def test_shared_step_bodies_are_identical(self):
        for name in self.SHARED_STEPS:
            deploy_bodies = [canon(b) for b in step_bodies(DEPLOY, name)]
            verify_bodies = [canon(b) for b in step_bodies(VERIFY, name)]
            self.assertTrue(deploy_bodies, f"deploy.yml has no step named {name!r}")
            self.assertTrue(verify_bodies, f"verify-compiler.yml has no step named {name!r}")

            distinct = set(deploy_bodies) | set(verify_bodies)
            self.assertEqual(
                len(distinct),
                1,
                f"{name!r} has {len(distinct)} distinct implementations across "
                "deploy.yml and verify-compiler.yml. They must be byte-identical "
                "apart from the matrix.deployment/matrix.entry context name — a "
                "replica that injects data differently verifies a tree the real "
                "deploy never builds.",
            )


if __name__ == "__main__":
    unittest.main()

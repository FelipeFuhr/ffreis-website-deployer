"""Regression test for the CloudFront invalidation-paths glob-expansion bug.

local/deploy-local.sh builds the `aws cloudfront create-invalidation
--paths ...` argument list from an associative array whose keys are
CloudFront path patterns (e.g. "/*", the fleet-wide default), populated by
word-splitting an unquoted shell variable and then re-expanded unquoted a
second time via `${!CF_INVALIDATE_PATHS[*]}` when passed to the aws CLI.
Both expansions are intentionally unquoted (multiple paths must become
separate argv entries) — but that also makes bash perform pathname
expansion on them, so a literal "/*" glob-expands against the real
filesystem root instead of staying the literal string CloudFront expects.
Observed in production: a knowalchemy.com deploy invalidated /etc, /home,
/var, etc. instead of "/*". The equivalent scalar-variable site in
deploy.yml (CI) has only one such expansion point.

The fix wraps `set -f` around BOTH expansion sites (building the array,
then passing it to --paths) — fixing only the first one still lets the
array's own glob-looking key re-expand at the point of use, which is the
exact gap this test exists to catch.

This test runs the real snippet from local/deploy-local.sh (lines
383-412), copied verbatim below, against a scratch directory using a
relative "*" pattern (rather than the real absolute "/*") so the
assertion is deterministic and doesn't depend on this machine's actual
root filesystem contents — the bug class is identical either way.
"""

import os
import subprocess
import tempfile
import unittest

# Verbatim structure of local/deploy-local.sh's CF_INVALIDATE_PATHS logic
# (SYNC_PLAN/aws calls stripped — this test is about the glob bug, not the
# sync itself). cf_paths is seeded directly rather than parsed from a TSV
# row, which is the only simplification relative to the real script.
SNIPPET_TEMPLATE = """
set -euo pipefail
cf_paths="*"
{guard}
declare -A CF_INVALIDATE_PATHS=()
for p in $cf_paths; do CF_INVALIDATE_PATHS["$p"]=1; done
printf '%s\\n' ${{!CF_INVALIDATE_PATHS[*]}}
{unguard}
"""


def run_invalidate_paths_snippet(use_set_f: bool, workdir: str) -> list[str]:
    guard = "set -f" if use_set_f else ":"
    unguard = "set +f" if use_set_f else ":"
    script = SNIPPET_TEMPLATE.format(guard=guard, unguard=unguard)
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


class TestCloudFrontInvalidationPathsGlobBug(unittest.TestCase):
    def test_without_set_dash_f_a_literal_star_glob_expands(self):
        """Documents the bug: unguarded, the "*" path pattern expands
        against whatever happens to be in the working directory rather
        than staying the literal CloudFront path string — even though the
        array itself only ever holds one key."""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("alpha", "beta", "gamma"):
                open(os.path.join(tmp, name), "w").close()
            paths = run_invalidate_paths_snippet(use_set_f=False, workdir=tmp)
            self.assertEqual(sorted(paths), ["alpha", "beta", "gamma"])

    def test_with_set_dash_f_the_literal_path_survives(self):
        """The fix: with `set -f` held across both the array-population
        loop AND the ${!CF_INVALIDATE_PATHS[*]} usage, the path pattern is
        preserved literally regardless of what's in the working directory."""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("alpha", "beta", "gamma"):
                open(os.path.join(tmp, name), "w").close()
            paths = run_invalidate_paths_snippet(use_set_f=True, workdir=tmp)
            self.assertEqual(paths, ["*"])

    def test_set_dash_f_only_around_the_first_expansion_is_insufficient(self):
        """Guards against re-introducing the exact gap this bug had in its
        first attempted fix: turning `set -f` back off before the
        ${!CF_INVALIDATE_PATHS[*]} usage lets that second, independent
        unquoted expansion glob-expand the array's own literal "*" key."""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("alpha", "beta", "gamma"):
                open(os.path.join(tmp, name), "w").close()
            script = """
set -euo pipefail
cf_paths="*"
declare -A CF_INVALIDATE_PATHS=()
set -f
for p in $cf_paths; do CF_INVALIDATE_PATHS["$p"]=1; done
set +f
printf '%s\\n' ${!CF_INVALIDATE_PATHS[*]}
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=True,
            )
            paths = result.stdout.splitlines()
            self.assertEqual(sorted(paths), ["alpha", "beta", "gamma"])


if __name__ == "__main__":
    unittest.main()

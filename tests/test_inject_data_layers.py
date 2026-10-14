"""Contract tests for deploy.yml's "Inject data into website" step.

The step serves two incompatible repo archetypes and picks between them on
`site_data.i18n`:

* **split template+data** (petlook, casaboa, ffreis) — the data repo carries
  `site.d/` + `site.yaml`, which are MERGED into the website's `src/data/`.
* **compiler-native layered** (ffreis-urbs) — the data repo carries the
  per-language layer tree itself (`i18n/<lang>/*.yaml`, no `site.d/`, no
  `site.yaml`) and the website repo keeps the common layer. The injection is a
  directory SWAP.

Getting the branch wrong is silent in the worst way: pointing the split path at
a layered data repo dies on `cp .../site.d/.` with a confusing "No such file or
directory" three steps before the build, and swapping a split repo's data would
delete content the website repo owns.

The script under test is read straight out of .github/workflows/deploy.yml
rather than copied here, so these tests cannot pass against a workflow that has
drifted from them. Both jobs (validate and build) carry the step; the test
asserts they are byte-identical and then exercises the one script.
"""

import pathlib
import re
import subprocess
import tempfile
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
STEP_NAME = "Inject data into website"


def inject_scripts() -> list[str]:
    """Every copy of the inject step's `run:` body, in file order."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    found = []
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                found.append(step["run"])
    return found


def runnable(script: str, subpath: str, site_data_i18n: str) -> str:
    """Substitute the two ${{ }} expressions the step reads.

    `data_repo` gates the step via `if:` and never appears in the body, so the
    only expressions left are these two.
    """
    script = script.replace("${{ matrix.deployment.data_subpath }}", subpath)
    script = script.replace("${{ matrix.deployment.site_data_i18n }}", site_data_i18n)
    leftover = re.findall(r"\$\{\{.*?\}\}", script)
    assert not leftover, f"unsubstituted expressions: {leftover}"
    return script


def run(script: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True)


def write(root: pathlib.Path, rel: str, body: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


class InjectStepIsSingleSourced(unittest.TestCase):
    def test_both_jobs_carry_an_identical_step(self):
        scripts = inject_scripts()
        self.assertEqual(len(scripts), 2, "validate and build jobs must both inject")
        self.assertEqual(
            scripts[0],
            scripts[1],
            "the two inject steps have drifted; a site would validate against "
            "different data than it builds from",
        )


class LayeredArchetype(unittest.TestCase):
    """site_data.i18n set -> directory swap."""

    def setUp(self):
        self.script = runnable(inject_scripts()[0], "i18n", "src/data/i18n")

    def test_language_layers_land_where_the_compiler_looks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/i18n/en/00-site.yaml", "lang: en")
            write(root, "checkout/data/i18n/pt/00-site.yaml", "lang: pt")
            write(root, "checkout/website/src/data/common/00-brand.yaml", "brand: urbs")

            proc = run(self.script, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                (root / "checkout/website/src/data/i18n/en/00-site.yaml").read_text(),
                "lang: en",
            )
            self.assertTrue((root / "checkout/website/src/data/i18n/pt/00-site.yaml").exists())

    def test_the_common_layer_in_the_website_repo_is_untouched(self):
        """The whole point of the split is that the website repo still owns the
        common layer; an injection that clobbered it would erase it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/i18n/en/00-site.yaml", "lang: en")
            write(root, "checkout/website/src/data/common/00-brand.yaml", "brand: urbs")

            proc = run(self.script, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                (root / "checkout/website/src/data/common/00-brand.yaml").read_text(),
                "brand: urbs",
            )

    def test_a_file_deleted_in_the_data_repo_disappears_from_the_build(self):
        """A merge would leave the stale file behind and the site would keep
        shipping removed copy. The swap is what makes deletion propagate."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/i18n/en/00-site.yaml", "lang: en")
            write(root, "checkout/website/src/data/i18n/en/99-removed.yaml", "stale")

            proc = run(self.script, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((root / "checkout/website/src/data/i18n/en/99-removed.yaml").exists())

    def test_a_missing_layer_tree_fails_loudly(self):
        """Silently building with no injected content is how a site ships blank
        pages with a green run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/README.md", "no layers here")
            write(root, "checkout/website/src/data/common/00-brand.yaml", "brand: urbs")

            proc = run(self.script, tmp)
            self.assertNotEqual(proc.returncode, 0, "missing layer tree must fail")
            self.assertIn("site_data.i18n", proc.stderr)


class SplitArchetype(unittest.TestCase):
    """site_data.i18n empty -> the pre-existing merge, unchanged."""

    def setUp(self):
        self.script = runnable(inject_scripts()[0], "data/en", "")

    def test_site_d_and_site_yaml_are_merged_into_the_website(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/data/en/site.d/10-pages.yaml", "pages: []")
            write(root, "checkout/data/data/en/site.yaml", "site: x")
            write(root, "checkout/website/src/data/keep.yaml", "keep")

            proc = run(self.script, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                (root / "checkout/website/src/data/site.d/10-pages.yaml").read_text(),
                "pages: []",
            )
            self.assertEqual((root / "checkout/website/src/data/site.yaml").read_text(), "site: x")
            self.assertTrue((root / "checkout/website/src/data/keep.yaml").exists())

    def test_shared_site_d_is_merged_under_the_language_layer(self):
        """shared/ sits alongside the language dirs and must be copied FIRST so
        a language file of the same name wins."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            write(root, "checkout/data/data/shared/site.d/00-common.yaml", "shared: yes")
            write(root, "checkout/data/data/shared/site.d/10-pages.yaml", "from: shared")
            write(root, "checkout/data/data/en/site.d/10-pages.yaml", "from: en")
            write(root, "checkout/data/data/en/site.yaml", "site: x")

            proc = run(self.script, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = root / "checkout/website/src/data/site.d"
            self.assertEqual((data / "00-common.yaml").read_text(), "shared: yes")
            self.assertEqual((data / "10-pages.yaml").read_text(), "from: en")


if __name__ == "__main__":
    unittest.main()

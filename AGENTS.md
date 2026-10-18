# Agent Context

**This repo:** `ffreis-website-deployer` — GitHub Actions workflows that orchestrate
CI/CD for the entire website fleet. The central `deploy.yml` reads from the inventory
repo to build and promote any managed website. The `local/` directory contains the
docker-compose watch stack used by `ffreis-siteops` for local development.

For the complete system map — how this repo relates to siteops, the compiler,
the inventory, S3 infrastructure, and the individual websites — see the private
fleet inventory repository:

> the fleet inventory (private repo — do not name it in commits or PR descriptions)

Architecture detail (CI/CD job graph, design decisions): `AGENTS.md` links to
`docs/ARCHITECTURE.md` in the same repo.

Do not look for cross-component flow documentation in this repo's README;
it covers only the deployer's own workflows and local runtime.

## Branching Model and Environment Separation

### Core rule

The `website_name` input to `deploy.yml` is the sole join point between a source-repo push and the AWS resources it touches. It determines which inventory YAML is read, which GitHub environment resolves secrets, which S3 bucket receives the build, and which CloudFront distribution is invalidated.

```
push to develop  →  website_name = <site>-dev  →  <site>-dev.yaml  →  github_environment: <site>-dev  →  dev AWS resources
push to main     →  website_name = <site>       →  <site>.yaml       →  github_environment: prod          →  prod AWS resources
```

### GitHub environments on this repo

| GitHub environment | Site | AWS resources |
|---|---|---|
| `prod` | flemming | `flemming-*-prod`, `flemming.com.br` |
| `flemming-dev` | flemming-dev | `flemming-*-dev`, `<dev-domain>` |
| `petlook-prod` | petlook | `petlook-*-prod`, `petlook.app` |
| `petlook-dev` | petlook-dev | `petlook-*-dev`, `<dev-domain>` |
| `ffreis-prod` | ffreis | `ffreis-*-prod`, `ffreis.com` |
| `ffreis-dev` | ffreis-dev | `ffreis-*-dev`, `<dev-domain>` |

Each environment holds independent secrets: `AWS_DEPLOY_ROLE_ARN`, `CF_DISTRIBUTION_ID`, `S3_WEBSITE_BUCKET`.

### Why a PR into `develop` cannot deploy to production

1. Deploy jobs in source repos fire only on `push` events, not `pull_request`.
2. Push to `develop` → dispatch fires `website_name=<site>-dev`.
3. `<site>-dev.yaml` declares `github_environment: <site>-dev`.
4. `<site>-dev` holds the dev OIDC role ARN, which has IAM permissions only on dev S3/CloudFront.
5. No path from step 2 touches prod resources.

### Why a PR into `main` cannot use dev config

1. A PR into `main` only runs validation CI — no dispatch step fires.
2. Once merged: push to `main` → dispatch fires `website_name=<site>`.
3. `<site>.yaml` declares the prod GitHub environment. Dev inventory files are never read.

### Adding a new site environment

1. Create `<site>-dev.yaml` in the fleet inventory with `github_environment: <site>-dev`.
2. Create the `<site>-dev` GitHub environment on this repo. Add secrets from dev Terraform outputs.
3. Update source repo CI to dispatch `<site>-dev` on develop, `<site>` on main.
4. Validate with `workflow_dispatch` → `website_name=<site>-dev` before setting `deploy_mode: auto`.

### watch.yml and dev sites

`watch.yml` dispatches all inventory files with at least one `deploy_mode: auto` deployment, including dev files. Dev environments with `auto` deployments are kept fresh automatically alongside prod.

## Public repo — private-repo hygiene

This is a **public** GitHub repository. When writing commit messages, PR titles,
PR descriptions, or any other user-visible text, **never name private repos** —
website content, inventory, infra, Lambda, or data repos that are not publicly
listed. Use generic terms instead: "the fleet inventory", "a private consumer",
"internal infra", "private data repo", etc.

## Content source selection

Each inventory YAML may carry a top-level `content_source` field (default: `prod`):

```yaml
content_source: mock   # "prod" (default) or "mock"
```

When `content_source: mock`, the `config` job validates the field is only used on
dev environments (`github_environment` ending in `-dev`) — any other combination is
a fatal error. The build step then swaps the content paths to `mock/` subdirectories:
- posts: `checkout/posts/mock/posts/`
- projects: `checkout/projects/mock/projects.yaml`
- courses: `checkout/courses/mock/courses.yaml`

It also passes `-content-source mock` to the compiler, which enforces the anti-leak
guard at build time: the compiler rejects any `/mock/` path when not in mock mode.

## Build stamp (`/build-info.json`)

Every **dev** deployment gets a `build-info.json` written into `dist/` by
`deploy.yml`'s "Stamp build-info.json" step, just before the build artifact is
synced. It rides the existing sync and ends up at the site root:

```json
{
  "branch": "develop",
  "deployedAt": "2026-09-11T10:04:22Z",
  "deployment": "production",
  "sha": "<40-char revision of the source repo that was compiled>",
  "website": "<site>-dev"
}
```

This is the fleet's single answer to "which build is live" — a site's dev panel
fetches `/build-info.json` and renders it. It is generic: no site opts in, no
site is named, and `sha` is read back from the website checkout (not from the
dispatch payload) so it can never disagree with the tree that was compiled.

Prod deployments deliberately get **no** stamp: the file is served publicly from
the site root, and a `github_environment` ending in `-dev` is the same dev/prod
discriminator the promote job's smoke-test skip and the `content_source` guard
already use. If a prod site ever needs a stamp, turn that condition into an
inventory-declared field rather than dropping the gate.

The key names (`sha`, `branch`, `deployedAt`, `website`, `deployment`) are a
consumer contract — renaming one breaks every site reading the file.
`tests/test_deploy_build_info.py` executes the step's real script body and
asserts the shape; run it with `make test`.

## Compiler embedding flags in inventory YAML

The `compiler` section of each inventory YAML can carry optional fields that control
how the compiler embeds resources into HTML during CI builds:

```yaml
compiler:
  repo: ...
  ref: main
  js_inline_threshold: 32768        # optional; compiler default = 8192 (8 KB); 0 = disable
  js_shared_inline_threshold: 8192  # optional; scripts on >1 page use this lower limit; -1 = off
  raster_inline_threshold: 2147483647  # optional; compiler default = 0 (disabled); large = all
  embed_fonts: false                # optional; default false
  inline_body_css: false            # optional; default false
  allow_blanket_robots_disallow: false  # optional; default false — see below
```

These fields are site-level (not per-deployment). The `deploy.yml` config job
extracts them from `top_compiler` and passes them as matrix outputs:
`compiler_js_inline_threshold`, `compiler_js_shared_inline_threshold`,
`compiler_raster_inline_threshold`, `compiler_embed_fonts`, `compiler_inline_body_css`,
`compiler_allow_blanket_robots_disallow`.
The build step converts them into the matching compiler flags.

`allow_blanket_robots_disallow` is not an embedding flag — the compiler's
`build-static` command hard-errors on a committed `robots.txt` with a blanket
`Disallow: /` under `User-agent: *` unless this is set, which is the correct
default for every production site. Set it `true` only for a dev-only site that
deliberately ships a placeholder blanket-disallow `robots.txt` (no production
domain yet). It maps to the compiler's `-allow-blanket-robots-disallow` flag.

## Two site archetypes: split template+data vs compiler-native

The fleet has two shapes, and the deployer supports both. Which one a site is
determines whether `sources.data` exists.

**Split template+data** (`ffreis-website`, `flemming-website`, `petlook-website`) — page copy lives in a separate `*-data` repo. The deployer
checks it out and copies it into `checkout/website/src/data/`, and the compiler
picks it up from that default location. No `-site-data` flag is involved.

**Compiler-native single-repo** (`ffreis-urbs`, `ffreis-koe-website`) — the site
keeps its own per-language data layer inside the website repo. There is no
`sources.data` block. These sites MUST be built with an explicit
`-site-data "<common>|<i18n>/<lang>"`, exactly as their own Makefiles do:

```yaml
sources:
  website:
    repo: ffreis-org/ffreis-urbs
    ref: main
    site_data:
      common: src/data/common   # shared, language-independent layer
      i18n:   src/data/i18n     # <i18n>/<deployment-name> is the language layer
```

`site_data.i18n` is joined with the **deployment name**, because for these sites
a deployment *is* a language (`deployments: en:` / `pt:`) — the same fan-out the
repos' own `for lang in $(LANGS)` loop performs. `-sibling-base-paths` was
already derived from the other deployments' prefixes and needs no new config.

Both keys are optional and default to empty; omitting them yields the previous
behaviour exactly, so split-archetype sites are unaffected. If `site_data.i18n`
is set but the resolved language directory is absent from the website repo, both
the validate and the build step fail loudly rather than silently compiling an
empty site.

Note the validate step passes the same `-site-data`. Without it, `validate-site-data`
would check the (absent) default `src/data/` tree and pass vacuously.

### The paths handed to the compiler MUST be absolute

The inventory stores `site_data` paths **relative to the website repo root**
(`src/data/common`), but the workflow must prefix each `|`-separated part with
`$GITHUB_WORKSPACE/checkout/website/` before passing it on. This is not
cosmetic:

* Both compiler entrypoints are invoked as `go -C <compiler-dir> run …`, which
  makes the compiler process' working directory the *compiler* checkout — not
  the workspace root, and not `-website-root`.
* `-site-data` is resolved against that working directory, independently of
  `-website-root`: `sitegen.loadLayersFromSource` splits the value on `|` and
  `os.ReadDir`s each part verbatim (`loadMultiDirSource`).

A repo-relative value therefore resolves under `checkout/compiler/` and the job
dies with `loading site data: open src/data/common: no such file or directory`.
`ffreis-urbs`'s own Makefile gets away with relative paths only because it runs
the compiler with `-website-root .` from the repo root.

Keep the existence guards (`[[ -d "${WEBSITE_ROOT}/…" ]]`) and the flag value
rooted at the same `WEBSITE_ROOT`, or the guard will check one path while the
compiler reads another.

`local/deploy-local.sh` carries the same support and the same absolute-path
requirement — it also runs the compiler with `cd "$co/compiler"`.

## Local deploy (`local/deploy-local.sh`) — the sanctioned local-first path

When GitHub Actions is unavailable (e.g. the billing pause), deploy with
`local/deploy-local.sh <website_name>` instead of an ad-hoc `aws s3 sync`. It is a
faithful replica of `deploy.yml`'s build → promote → invalidate: it resolves the
inventory YAML with the **same** `resolve()` logic, exports each source repo at its
pinned ref, injects data/shared-JS, and — critically — passes
`-posts-dir`/`-projects-file`/`-courses-file` exactly like CI.

**Why it exists:** a manual `aws s3 sync` of a local build skips the content
injection, so the compiler renders the `60-pages.yaml` blog **seed** placeholder
(`/blog/production-ml-systems/`, which 404s) instead of the real posts. That is
exactly how the mock post reached ffreis.com prod on 2026-06-29. This script closes
that gap and refuses to publish a build whose blog listing links a card with no
backing post page (the seed-leak signature).

```bash
local/deploy-local.sh ffreis                       # build + guard only (no AWS) — DEFAULT
CF_DISTRIBUTION_ID=<id> local/deploy-local.sh ffreis --deploy --yes-prod   # publish prod
```

- Default action is `--build-only` (no AWS calls). `--deploy` syncs to the live
  bucket + invalidates CloudFront; a prod target additionally requires `--yes-prod`.
- Sync mirrors CI: `--delete` is skipped when sibling deployments share the bucket
  (e.g. ffreis `en`/`pt`), so one language never wipes another's content.
- Run from a normal checkout, or pass `--workspace <root>` / `--inventory <dir>`
  when running from a git worktree.
- This is a `-content-source prod` build; it does not enable the `mock/` content
  system (see "Content source selection"). The two guards are independent.

## Compiler pinning (the `stable` pointer is RETIRED)

Every consumer of the compiler pins an **exact release tag**. There is no
moving pointer any more, and `promote-compiler.yml` has been removed.

### Why `stable` was retired rather than fixed

`stable` was the fleet's "last-known-good" pointer, moved only by
`promote-compiler.yml` after every fleet site built green against a candidate.
The design was sound; the instance never worked:

- the promotion **never once succeeded** — 9 runs, all failed;
- `COMPILER_PROMOTE_TOKEN`, the narrowly-scoped PAT it needs to update a ref in
  another repository, was never provisioned, and a PAT cannot be minted via the
  API — it is a web-UI-only action;
- so `stable` sat frozen at `d1526ab` from the day it was created: 17 commits
  behind v1.0.1 and predating the `css/pages/<page>.css` per-page CSS
  convention entirely.

A site pinned to it therefore **built green and shipped unstyled pages**, and
several did, because `stable` was the default in three separate places — this
repo's inventory, the shared `ffreis-workflows-website` quality workflows, and
individual site Makefiles. Every quality gate those workflows ran was judging an
artefact production never ships.

By 2026-10-17 the pointer had **zero** consumers: every inventory entry pins a
release, no site workflow falls back to it, and the shared workflow library's
`compiler-ref` default is an exact tag. Keeping a promotion mechanism — and
provisioning a high-consequence credential for it — to serve zero consumers was
the wrong trade. An exact tag per consumer gives the same protection with no
moving parts and no secret.

The `stable` BRANCH itself is left in place rather than deleted, so no existing
clone breaks; it is frozen and must not be pinned. Deleting it is a separate,
deliberate step.

### What replaces it

Pin a release tag, and keep the two places that reference it equal:

- `compiler.ref` in the site's `ffreis-website-inventory` entry (drives the
  real deploy), and
- the site's own `WEBSITE_COMPILER_REF` / workflow `compiler-ref` (drives CI).

When those two disagree, CI gates a different artefact than production ships —
which is the same failure `stable` caused, arrived at a different way. A site
Makefile should also normalise an empty-but-set `WEBSITE_COMPILER_REF` and
verify the ref against an existing sibling checkout; see ffreis-forma's
`ensure-compiler` and its `tests/regression/compiler-pin-is-not-inert.test.js`
for the three independent ways a pin can turn out to be inert.

### `verify-compiler.yml` stays, and is the useful half

It builds every non-retired fleet site against any candidate ref, with no AWS
credentials and no deploy step:

```
gh workflow run "Verify Compiler" -R FelipeFuhr/ffreis-website-deployer \
  --field compiler_ref=<branch, tag or SHA>
```

Its remaining job is **measurement**: run it against a candidate and against the
current pin, and diff the per-site table to see a compiler change's real blast
radius before merging. Used exactly that way on 2026-10-17, where it showed a
candidate changed the state of precisely one site out of 24 — and that the one
change was the bug being fixed.

`tests/test_verify_compiler_matches_deploy.py` is what keeps it honest: the
workflow is a replica of `deploy.yml`'s build path, and a replica drifts unless
something compares them.

### Rolling back a compiler change

Re-pin. A rollback is an ordinary pin bump to an older release tag in the two
places listed above — no workflow, no credential, no force-moved ref.

## Keeping this file current

- **If you discover a fact not reflected here:** add it before finishing your task.
- **If something here is wrong or outdated:** correct it in the same commit as the code change.
- **If you rename a file, command, or concept referenced here:** update the reference.

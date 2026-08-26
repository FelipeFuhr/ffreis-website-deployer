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
guard at build time: outside mock mode the compiler rejects content identified as
mock either by a `/mock/` path segment or by a `.mock-content` marker file committed
at the corpus root. The marker is what survives a directory rename, so the isolation
does not rest on a path string alone.

## Guard tests (`make test`)

`tests/test_deploy_guards.py` covers the inventory guards in `deploy.yml`'s
config step — the checks that reject `content_source: mock`, `include_drafts:
true`, and a both-keys section config for a non-dev environment. They are the
last thing between an inventory typo and a production deploy.

The tests **extract the step from the workflow** and run it against fixture
inventories with the same environment variables and a real `$GITHUB_OUTPUT`
file, rather than testing a copy of the logic. A copy drifts, and then the tests
pass while the shipped workflow is wrong. `TestExtractionIsNotVacuous` pins that
the extraction actually found the guards, so an extraction that silently
returned nothing cannot make every other test pass against an empty script.

Both directions are covered: each rejected shape is asserted to fail *with the
right message*, and each permitted shape is asserted to resolve *and produce the
right compiler flags* — otherwise a guard that rejected everything would pass.
Each guard was verified by removing it and confirming the matching test fails.

Run with `make test` (stdlib `unittest`; needs PyYAML). CI runs it as the
**Workflow Guard Tests** job, triggered by changes to `.github/workflows/**`,
`Makefile`, `lefthook.yml`, or `tests/**`.

## Section gating (`enable_sections` / `disable_sections`)

A site can hide whole content sections (`blog`, `courses`, `projects`). Which
direction the inventory expresses that in depends on whether the site declares a
flag registry (`flags/flags.json` at its root):

```yaml
enable_sections: [blog, projects]   # registry site: sections default OFF, list what to turn on
disable_sections: [blog, courses]   # legacy site:   sections default ON,  list what to hide
```

Setting **both** in one inventory is a fatal config error — they answer the same
question in opposite directions and a reader cannot resolve the result at a glance.

Prefer `enable_sections`. With a registry the safe state no longer requires
configuration: a build that receives no section config falls back to the declared
default, so an inventory key lost in a promote or a branch divergence cannot
publish a not-ready section. Without a registry, losing `disable_sections` turns
every section back on, which is the failure mode this exists to remove.

## Draft content (`include_drafts`)

```yaml
include_drafts: true   # dev environments only
```

Passes `-include-drafts` to the compiler, admitting posts, projects and courses
marked `draft: true`. The `config` job validates it exactly the way it validates
`content_source: mock` — set on a `github_environment` that does not end in `-dev`
is a fatal error. The compiler already defaults it off, so this guard exists to
stop an inventory from deliberately turning it on outside dev.

Drafts and mock content are different things and should not be conflated: mock
content is generated filler that is structurally isolated in a `mock/` tree, while
a draft is real, unfinished content sitting in the normal corpus.

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
```

These fields are site-level (not per-deployment). The `deploy.yml` config job
extracts them from `top_compiler` and passes them as matrix outputs:
`compiler_js_inline_threshold`, `compiler_js_shared_inline_threshold`,
`compiler_raster_inline_threshold`, `compiler_embed_fonts`, `compiler_inline_body_css`.
The build step converts them into the matching compiler flags.

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

## Keeping this file current

- **If you discover a fact not reflected here:** add it before finishing your task.
- **If something here is wrong or outdated:** correct it in the same commit as the code change.
- **If you rename a file, command, or concept referenced here:** update the reference.

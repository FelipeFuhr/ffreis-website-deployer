#!/usr/bin/env bash
# local/deploy-local.sh — faithful LOCAL replica of .github/workflows/deploy.yml
# (build → optional promote → optional CloudFront invalidation), for use when
# GitHub Actions is unavailable (e.g. the billing pause) so that local-first
# deploys CANNOT drift from what CI would have produced.
#
# WHY THIS EXISTS
#   The deployer build step injects the content repos into the compiler via
#   -posts-dir / -projects-file / -courses-file. An ad-hoc local `aws s3 sync`
#   that skips that injection publishes the 60-pages.yaml SEED placeholder
#   ("Building Production ML Systems" → /blog/production-ml-systems/, which 404s)
#   instead of the real posts. This script is the single sanctioned local deploy
#   path; it reproduces the deployer's injection exactly and refuses to publish a
#   build whose blog listing still contains a card with no backing post page.
#
# USAGE
#   deploy-local.sh <website_name> [options]
#     --deployment <name>       Build only this deployment (default: all)
#     --inventory <dir>         Path to ffreis-website-inventory checkout
#                               (default: autodetect under the workspace root)
#     --build-only              Build + guard only, no AWS calls (DEFAULT)
#     --deploy                  Also sync to the live bucket + invalidate CF
#     --yes-prod                Required in addition to --deploy for prod targets
#                               (a <website_name> that does not end in -dev)
#     --cf-distribution-id <id> CloudFront distribution id (or env CF_DISTRIBUTION_ID);
#                               required for --deploy (invalidation)
#     --workspace <dir>         Workspace root (default: two levels above this repo)
#     --keep                    Keep the temp build dir for inspection
#
# EXAMPLES
#   # Build ffreis en+pt locally and verify (no AWS):
#   local/deploy-local.sh ffreis
#   # Deploy to prod after review (needs AWS creds + CF id + explicit prod ack):
#   CF_DISTRIBUTION_ID=XXXX local/deploy-local.sh ffreis --deploy --yes-prod
set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────────────
WEBSITE_NAME=""
ONLY_DEPLOYMENT=""
INVENTORY_DIR=""
WORKSPACE_ROOT=""
ACTION="build-only"
YES_PROD=0
KEEP=0
DISABLE_SECTIONS=""
CF_DISTRIBUTION_ID="${CF_DISTRIBUTION_ID:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deployment)          ONLY_DEPLOYMENT="$2"; shift 2 ;;
    --inventory)           INVENTORY_DIR="$2"; shift 2 ;;
    --workspace)           WORKSPACE_ROOT="$2"; shift 2 ;;
    --cf-distribution-id)  CF_DISTRIBUTION_ID="$2"; shift 2 ;;
    --build-only)          ACTION="build-only"; shift ;;
    --deploy)              ACTION="deploy"; shift ;;
    --yes-prod)            YES_PROD=1; shift ;;
    --disable-sections)    DISABLE_SECTIONS="$2"; shift 2 ;;
    --keep)                KEEP=1; shift ;;
    -h|--help)             sed -n '2,40p' "$0"; exit 0 ;;
    -*)                    echo "unknown option: $1" >&2; exit 2 ;;
    *)                     if [[ -z "$WEBSITE_NAME" ]]; then WEBSITE_NAME="$1"; else echo "unexpected arg: $1" >&2; exit 2; fi; shift ;;
  esac
done

[[ -n "$WEBSITE_NAME" ]] || { echo "error: <website_name> is required" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$WORKSPACE_ROOT" ]]; then
  # local/ lives in the deployer repo; the workspace root is normally two levels up,
  # but honour an explicit override for worktrees.
  WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi

# Autodetect the inventory checkout if not supplied.
if [[ -z "$INVENTORY_DIR" ]]; then
  for cand in "$WORKSPACE_ROOT/ffreis-website-inventory" "$SCRIPT_DIR/../../ffreis-website-inventory"; do
    if [[ -d "$cand/inventory" ]]; then INVENTORY_DIR="$(cd "$cand" && pwd)"; break; fi
  done
fi
[[ -d "$INVENTORY_DIR/inventory" ]] || { echo "error: could not find inventory checkout (pass --inventory)" >&2; exit 2; }

# Locate the inventory YAML by recursive glob, exactly like the deployer.
INVENTORY_YAML="$(find "$INVENTORY_DIR/inventory" -name "${WEBSITE_NAME}.yaml" | head -1)"
[[ -n "$INVENTORY_YAML" ]] || { echo "error: no inventory file for '${WEBSITE_NAME}'" >&2; exit 2; }

IS_PROD=1; [[ "$WEBSITE_NAME" == *-dev ]] && IS_PROD=0

echo "▸ website:    $WEBSITE_NAME  ($([[ $IS_PROD == 1 ]] && echo PRODUCTION || echo dev))"
echo "▸ inventory:  $INVENTORY_YAML"
echo "▸ action:     $ACTION"

if [[ "$ACTION" == "deploy" && $IS_PROD == 1 && $YES_PROD != 1 ]]; then
  echo "error: refusing to deploy a PROD target without --yes-prod" >&2
  exit 3
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/deploy-local.${WEBSITE_NAME}.XXXXXX")"
cleanup() { [[ $KEEP == 1 ]] || rm -rf "$BUILD_ROOT"; }
trap cleanup EXIT
echo "▸ build dir:  $BUILD_ROOT  $([[ $KEEP == 1 ]] && echo '(kept)')"

# ── resolve deployments (mirrors deploy.yml config job) ───────────────────────
# Emits one TSV line per deployment with the fields the build/promote steps need.
DEPLOYMENTS_TSV="$(python3 - "$INVENTORY_YAML" "$ONLY_DEPLOYMENT" <<'PY'
import sys, yaml
path, only = sys.argv[1], sys.argv[2]
with open(path) as fh:
    cfg = yaml.safe_load(fh) or {}
top_sources  = cfg.get("sources", {})
top_website  = top_sources.get("website", {})
top_data     = top_sources.get("data", {})
top_posts    = top_sources.get("posts", {})
top_projects = top_sources.get("projects", {})
top_courses  = top_sources.get("courses", {})
top_shared   = top_sources.get("shared_js", {})
top_compiler = cfg.get("compiler", {})
top_builds   = cfg.get("builds", {})
top_publish  = cfg.get("publish", {})
top_cf       = top_publish.get("cloudfront_invalidate_paths", ["/*"])
raw = cfg.get("deployments")
legacy = raw is None

def resolve(name, dep):
    src = dep.get("sources", {})
    d_dat = src.get("data", {})
    pub = dep.get("publish", {})
    cf = pub.get("cloudfront_invalidate_paths", top_cf)
    return {
        "name": name,
        "website_repo": top_website.get("repo", ""),
        "website_ref": src.get("website", {}).get("ref", top_website.get("ref", "main")),
        "data_repo": top_data.get("repo", ""),
        "data_ref": d_dat.get("ref", top_data.get("ref", "main")),
        "data_subpath": d_dat.get("subpath", top_data.get("subpath", "")),
        "posts_repo": src.get("posts", {}).get("repo", top_posts.get("repo", "")),
        "posts_ref": src.get("posts", {}).get("ref", top_posts.get("ref", "main")),
        "projects_repo": src.get("projects", {}).get("repo", top_projects.get("repo", "")),
        "projects_ref": src.get("projects", {}).get("ref", top_projects.get("ref", "main")),
        "courses_repo": src.get("courses", {}).get("repo", top_courses.get("repo", "")),
        "courses_ref": src.get("courses", {}).get("ref", top_courses.get("ref", "main")),
        "shared_js_repo": top_shared.get("repo", ""),
        "shared_js_ref": top_shared.get("ref", "main"),
        # Compiler-native single-repo sites: data lives in the website repo's
        # own layers, so the build needs an explicit -site-data override.
        # Empty for the split template+data archetype. Mirrors deploy.yml.
        "site_data_common": top_website.get("site_data", {}).get("common", ""),
        "site_data_i18n": top_website.get("site_data", {}).get("i18n", ""),
        "compiler_repo": top_compiler.get("repo", ""),
        "compiler_ref": top_compiler.get("ref", "main"),
        "js_inline": str(top_compiler.get("js_inline_threshold", "")),
        "js_shared_inline": str(top_compiler.get("js_shared_inline_threshold", "")),
        "raster_inline": str(top_compiler.get("raster_inline_threshold", "")),
        "embed_fonts": "true" if top_compiler.get("embed_fonts", False) else "",
        "inline_body_css": "true" if top_compiler.get("inline_body_css", False) else "",
        "publish_bucket": pub.get("bucket", top_publish.get("bucket", "")),
        "publish_prefix": pub.get("prefix", ""),
        "publish_region": pub.get("region", top_publish.get("region", top_builds.get("region", "us-east-1"))),
        "cf_paths": " ".join(cf),
    }

deps = [resolve("production", {})] if legacy else [resolve(k, v) for k, v in raw.items()]
# sibling_prefixes: other deployments' non-empty prefixes sharing the same bucket
bp = [(d["publish_bucket"], d["publish_prefix"]) for d in deps]
for d in deps:
    sib = [p for (b, p) in bp if b == d["publish_bucket"] and p and p != d["publish_prefix"]]
    d["sibling_prefixes"] = " ".join(sib)
    # Section visibility (site-level): sections to hide in this environment.
    d["disable_sections"] = ",".join(cfg.get("disable_sections", []))

cols = ["name","website_repo","website_ref","data_repo","data_ref","data_subpath",
        "posts_repo","posts_ref","projects_repo","projects_ref","courses_repo","courses_ref",
        "shared_js_repo","shared_js_ref","compiler_repo","compiler_ref",
        "js_inline","js_shared_inline","raster_inline","embed_fonts","inline_body_css",
        "publish_bucket","publish_prefix","publish_region","cf_paths","sibling_prefixes",
        "disable_sections","site_data_common","site_data_i18n"]
for d in deps:
    if only and d["name"] != only:
        continue
    # \x1f (ASCII Unit Separator) is non-whitespace, so `IFS=$'\x1f' read` preserves
    # empty fields (a plain tab would collapse consecutive empties → arg-count skew).
    print("\x1f".join(d[c] for c in cols))
PY
)"
[[ -n "$DEPLOYMENTS_TSV" ]] || { echo "error: no deployments resolved (bad --deployment?)" >&2; exit 2; }

# ── source export helpers ─────────────────────────────────────────────────────
# Map "Owner/repo" → a local checkout under the workspace, else clone the ref.
find_local_repo() {
  local name="${1##*/}"
  local c
  for c in "$WORKSPACE_ROOT/$name" \
           "$WORKSPACE_ROOT/website/$name" \
           "$WORKSPACE_ROOT/platform/$name" \
           "$WORKSPACE_ROOT/$name/$name"; do
    if [[ -d "$c/.git" ]]; then echo "$c"; return 0; fi
  done
  return 1
}

# export_source <owner/repo> <ref> <dest>  — pristine tree at the pinned ref.
export_source() {
  local repo="$1" ref="$2" dest="$3" local_path srcref
  mkdir -p "$dest"
  if local_path="$(find_local_repo "$repo")"; then
    git -C "$local_path" fetch -q origin "$ref" 2>/dev/null || true
    if git -C "$local_path" rev-parse --verify -q "origin/$ref" >/dev/null; then
      srcref="origin/$ref"
    else
      srcref="$ref"
    fi
    git -C "$local_path" archive --format=tar "$srcref" | tar -x -C "$dest"
    echo "    $repo@$ref  ←  $local_path ($srcref)" >&2
  else
    gh repo clone "$repo" "$dest" -- -q --depth 1 --branch "$ref" 2>/dev/null \
      || git clone -q --depth 1 --branch "$ref" "https://github.com/${repo}.git" "$dest"
    rm -rf "$dest/.git"
    echo "    $repo@$ref  ←  cloned" >&2
  fi
}

# ── broken-card guard ─────────────────────────────────────────────────────────
# Fail if any /blog/<slug>/ link anywhere in the rendered output has no backing
# post page. This catches the 60-pages.yaml seed placeholder (which renders a
# card to /blog/production-ml-systems/ with no page → 404) and any genuinely
# broken card. dist paths are language-prefix-less, so we key only on the segment
# that follows "/blog/" — stripping any leading /en, /pt, … base path.
guard_blog_cards() {
  local dist="$1" name="$2"
  local missing=0 slug
  declare -A seen=()
  while IFS= read -r slug; do
    [[ -n "$slug" && "$slug" != "page" ]] || continue   # "page" is pagination, not a post
    [[ -n "${seen[$slug]:-}" ]] && continue
    seen[$slug]=1
    if [[ ! -f "$dist/blog/$slug/index.html" ]]; then
      echo "  ✗ [$name] blog card '/blog/$slug/' has NO backing page (dist/blog/$slug/index.html missing)" >&2
      missing=1
    fi
  done < <(grep -rhoE '/blog/[a-z0-9-]+/' "$dist" --include='*.html' 2>/dev/null \
             | sed -E 's#^.*/blog/([a-z0-9-]+)/#\1#' | sort -u)
  return $missing
}

# ── known-bad reCAPTCHA site-key guard ────────────────────────────────────────
# The malformed prod reCAPTCHA v3 site key 6LfLH-cs… (Google rejects it) broke
# ffreis.com chat + forms on 2026-06-29. It was hot-fixed live but lingered in
# git; a rebuild from a stale ref would silently regress it. Refuse to publish any
# build whose HTML still embeds a known-bad key. Extend BAD_RECAPTCHA_KEYS if more
# broken keys are discovered.
BAD_RECAPTCHA_KEYS="6LfLH-csAAAABtl79WbpOl436ehu5N_bXEm9NvM"
guard_recaptcha_key() {
  local dist="$1" name="$2" bad hit=0
  for bad in $BAD_RECAPTCHA_KEYS; do
    if grep -rqF "$bad" "$dist" --include='*.html' 2>/dev/null; then
      echo "  ✗ [$name] build embeds a KNOWN-BAD reCAPTCHA site key ($bad) — would break chat/forms" >&2
      hit=1
    fi
  done
  return $hit
}

# ── per-deployment build ──────────────────────────────────────────────────────
declare -a SYNC_PLAN=()   # "dist_dir\tbucket\tprefix\tregion\tcf_paths\tsibling"

build_one() {
  # shellcheck disable=SC2034
  local name="$1" website_repo="$2" website_ref="$3" data_repo="$4" data_ref="$5" \
        data_subpath="$6" posts_repo="$7" posts_ref="$8" projects_repo="$9" projects_ref="${10}" \
        courses_repo="${11}" courses_ref="${12}" shared_js_repo="${13}" shared_js_ref="${14}" \
        compiler_repo="${15}" compiler_ref="${16}" js_inline="${17}" js_shared_inline="${18}" \
        raster_inline="${19}" embed_fonts="${20}" inline_body_css="${21}" \
        publish_bucket="${22}" publish_prefix="${23}" publish_region="${24}" \
        cf_paths="${25}" sibling_prefixes="${26}" disable_sections="${27:-}" \
        site_data_common="${28:-}" site_data_i18n="${29:-}"

  echo "── build deployment: $name ──────────────────────────────────────────────"
  local work="$BUILD_ROOT/$name"
  local co="$work/checkout" dist="$work/dist"
  mkdir -p "$co" "$dist"

  export_source "$website_repo" "$website_ref" "$co/website"
  export_source "$compiler_repo" "$compiler_ref" "$co/compiler"
  [[ -n "$data_repo" ]]     && export_source "$data_repo" "$data_ref" "$co/data"
  [[ -n "$posts_repo" ]]    && export_source "$posts_repo" "$posts_ref" "$co/posts"
  [[ -n "$projects_repo" ]] && export_source "$projects_repo" "$projects_ref" "$co/projects"
  [[ -n "$courses_repo" ]]  && export_source "$courses_repo" "$courses_ref" "$co/courses"
  [[ -n "$shared_js_repo" ]] && export_source "$shared_js_repo" "$shared_js_ref" "$co/shared-js"

  # Inject data (shared/ base layer, then language overlay + site.yaml).
  if [[ -n "$data_repo" ]]; then
    local subpath="${data_subpath%/}"
    local data_root="$co/data${subpath:+/$subpath}"
    local data_parent; data_parent="$([[ -n "$subpath" ]] && dirname "$data_root" || echo "$co/data")"
    mkdir -p "$co/website/src/data/site.d"
    [[ -d "$data_parent/shared/site.d" ]] && cp -r "$data_parent/shared/site.d/." "$co/website/src/data/site.d/"
    cp -r "$data_root/site.d/." "$co/website/src/data/site.d/"
    cp "$data_root/site.yaml" "$co/website/src/data/"
  fi

  # Inject shared JS.
  if [[ -n "$shared_js_repo" && -d "$co/shared-js" ]]; then
    mkdir -p "$co/website/src/assets/js"
    cp "$co/shared-js"/*.js "$co/website/src/assets/js/" 2>/dev/null || true
  fi

  # Assemble compiler flags exactly like deploy.yml.
  local -a args=(-website-root "$co/website" -out "$dist" -clean-urls)
  # Compiler-native single-repo sites. Paths must be absolute: build-static is
  # invoked below with CWD=$co/compiler, and the compiler resolves -site-data
  # parts against its own CWD, not against -website-root.
  if [[ -n "$site_data_i18n" ]]; then
    local sd_lang_dir="${site_data_i18n%/}/$name"
    if [[ ! -d "$co/website/$sd_lang_dir" ]]; then
      echo "  ✗ site_data.i18n is set but $sd_lang_dir is absent from the website repo" >&2
      return 1
    fi
    if [[ -n "$site_data_common" ]]; then
      if [[ ! -d "$co/website/${site_data_common%/}" ]]; then
        echo "  ✗ site_data.common is set but ${site_data_common%/} is absent from the website repo" >&2
        return 1
      fi
      args+=(-site-data "$co/website/${site_data_common%/}|$co/website/$sd_lang_dir")
    else
      args+=(-site-data "$co/website/$sd_lang_dir")
    fi
  fi
  [[ -n "$posts_repo"    && -d "$co/posts/posts" ]]          && args+=(-posts-dir "$co/posts/posts")
  [[ -n "$projects_repo" && -f "$co/projects/projects.yaml" ]] && args+=(-projects-file "$co/projects/projects.yaml")
  [[ -n "$courses_repo"  && -f "$co/courses/courses.yaml" ]]   && args+=(-courses-file "$co/courses/courses.yaml")
  [[ -n "$sibling_prefixes" ]] && args+=(-sibling-base-paths "${sibling_prefixes// /,}")
  local disable="${disable_sections:-}"
  [[ -n "$DISABLE_SECTIONS" ]] && disable="$DISABLE_SECTIONS"
  [[ -n "$disable" ]] && args+=(-disable-sections "$disable")
  [[ -n "$js_inline" ]]        && args+=(-js-inline-threshold "$js_inline")
  [[ -n "$js_shared_inline" ]] && args+=(-js-shared-inline-threshold "$js_shared_inline")
  [[ -n "$raster_inline" ]]    && args+=(-raster-inline-threshold "$raster_inline")
  [[ "$embed_fonts" == "true" ]]     && args+=(-embed-fonts)
  [[ "$inline_body_css" == "true" ]] && args+=(-inline-body-css)

  echo "  building (posts-dir=$([[ " ${args[*]} " == *" -posts-dir "* ]] && echo yes || echo NO))…"
  ( cd "$co/compiler" && go run ./cmd/build-static "${args[@]}" )

  # Guard: no blog card may point at a missing post page.
  if ! guard_blog_cards "$dist" "$name"; then
    echo "  ✗ GUARD FAILED for '$name': blog listing contains a card with no backing post page." >&2
    echo "    This is the seed-leak signature. Refusing to continue." >&2
    exit 4
  fi
  # Guard: no known-broken reCAPTCHA site key may ship.
  if ! guard_recaptcha_key "$dist" "$name"; then
    echo "  ✗ GUARD FAILED for '$name': build embeds a known-bad reCAPTCHA site key." >&2
    echo "    Fix the key in the source site.yaml before deploying. Refusing to continue." >&2
    exit 4
  fi
  local npages=0
  [[ -d "$dist/blog" ]] && npages=$(find "$dist/blog" -mindepth 2 -name index.html 2>/dev/null | wc -l | tr -d ' ')
  echo "  ✓ [$name] built OK — $npages blog post page(s), all listing cards backed, reCAPTCHA key OK."

  SYNC_PLAN+=("$dist"$'\x1f'"$publish_bucket"$'\x1f'"$publish_prefix"$'\x1f'"$publish_region"$'\x1f'"$cf_paths"$'\x1f'"$sibling_prefixes")
}

while IFS=$'\x1f' read -r -a F; do
  [[ ${#F[@]} -ge 26 ]] || continue   # skip any blank line
  build_one "${F[@]}"
done <<< "$DEPLOYMENTS_TSV"

# ── deploy (optional) ─────────────────────────────────────────────────────────
if [[ "$ACTION" != "deploy" ]]; then
  echo
  echo "✓ build-only complete. Review $BUILD_ROOT then re-run with --deploy to publish."
  exit 0
fi

command -v aws >/dev/null || { echo "error: aws CLI not found" >&2; exit 5; }
[[ -n "$CF_DISTRIBUTION_ID" ]] || { echo "error: --cf-distribution-id / CF_DISTRIBUTION_ID required for --deploy" >&2; exit 5; }

declare -A CF_INVALIDATE_PATHS=()
for row in "${SYNC_PLAN[@]}"; do
  IFS=$'\x1f' read -r dist bucket prefix region cf_paths sibling <<< "$row"
  if [[ -n "$prefix" ]]; then dest="s3://${bucket}/${prefix%/}/"; else dest="s3://${bucket}/"; fi
  echo "── sync: $dist  →  $dest ──"
  # Match the deployer: skip --delete when sibling deployments share the bucket.
  if [[ -n "$sibling" ]]; then
    aws s3 sync "$dist/" "$dest" --region "$region"
  else
    aws s3 sync "$dist/" "$dest" --region "$region" --delete
  fi
  for p in $cf_paths; do CF_INVALIDATE_PATHS["$p"]=1; done
done

if [[ ${#CF_INVALIDATE_PATHS[@]} -gt 0 ]]; then
  echo "── invalidate CloudFront $CF_DISTRIBUTION_ID: ${!CF_INVALIDATE_PATHS[*]} ──"
  # shellcheck disable=SC2086 # each path must be a separate argument
  aws cloudfront create-invalidation --distribution-id "$CF_DISTRIBUTION_ID" \
    --paths ${!CF_INVALIDATE_PATHS[*]}
fi

echo "✓ deploy complete for $WEBSITE_NAME."

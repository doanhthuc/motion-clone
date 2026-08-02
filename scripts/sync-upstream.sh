#!/usr/bin/env bash
#
# Pull upstream (ALD-Project) into this monorepo WITHOUT re-leaking its secrets.
#
#   make sync-upstream              # rsync + scrub + gate, then show you the diff
#   make sync-upstream PULL=1       # also `git pull` the tracking clones first
#   make sync-upstream COMMIT=1     # commit if the gate passes
#
# WHY THIS EXISTS
#   rsync OVERWRITES the files scrub-secrets.sh cleaned, so every sync drags the
#   upstream credentials back in: DEFAULT_API_KEY (a key SHARED by every deploy of
#   this source), the Motion Task Cloud admin key, setup/templates.json, and a few
#   personal emails. Doing this by hand is the kind of step you will forget exactly
#   once. So: rsync, re-scrub, then refuse to continue if anything survived.
#
# The tracking clones are pristine copies of upstream with push DISABLED. They live
# OUTSIDE this repo on purpose — inside it, `git add -A` would hoover up
# setup/templates.json and publish 9 customers' Cloudflare tokens.
#
set -uo pipefail
cd "$(dirname "$0")/.."; ROOT="$(pwd)"

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗ \033[0m%s\n' "$*" >&2; exit 1; }

UPSTREAM_DIR="${UPSTREAM_DIR:-$(cd "$ROOT/.." && pwd)/motion-upstream-tracking}"
DO_PULL="${PULL:-0}"
DO_COMMIT="${COMMIT:-0}"
FORCE="${FORCE:-0}"

# Which tracking clone maps onto which subdirectory here.
PAIRS="motions-studio:motions-studio motions:motions"

[ -d "$UPSTREAM_DIR" ] || die "tracking clones not found at $UPSTREAM_DIR
    Create them once (they must live OUTSIDE this repo):
      mkdir -p $UPSTREAM_DIR
      git clone git@github-doanhthuc3005:ALD-Project/motions-studio.git $UPSTREAM_DIR/motions-studio
      git clone git@github-doanhthuc3005:ALD-Project/motions.git        $UPSTREAM_DIR/motions
      # then disable push on both, so you can never shove your fork upstream:
      git -C $UPSTREAM_DIR/motions-studio remote set-url --push origin DISABLED
      git -C $UPSTREAM_DIR/motions        remote set-url --push origin DISABLED
    Or point somewhere else with:  UPSTREAM_DIR=/path make sync-upstream"

# ── Refuse to mix your work-in-progress with an upstream import ────────────────
# Without this you cannot tell, in the resulting diff, which hunks are upstream's
# and which are yours — and that diff is the only review this import ever gets.
if [ "$FORCE" != 1 ] && [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  git -C "$ROOT" status --short | sed 's/^/    /'
  die "working tree is dirty. Commit or stash first, so the upstream diff is reviewable.
    Override with:  FORCE=1 make sync-upstream"
fi

# ── 1. Refresh the tracking clones ────────────────────────────────────────────
SHAS=""
for pair in $PAIRS; do
  src="$UPSTREAM_DIR/${pair%%:*}"
  [ -d "$src/.git" ] || die "$src is not a git clone"

  pushurl="$(git -C "$src" remote get-url --push origin 2>/dev/null || true)"
  case "$pushurl" in
    *DISABLED*) ;;
    *) warn "$src still has a real push URL ($pushurl)."
       warn "    One stray 'git push' there would publish your fork to ALD-Project. Disable it:"
       warn "    git -C $src remote set-url --push origin DISABLED" ;;
  esac

  if [ "$DO_PULL" = 1 ]; then
    log "pulling $(basename "$src")…"
    git -C "$src" pull --ff-only || die "git pull failed in $src (diverged? pull by hand)"
  fi
  sha="$(git -C "$src" rev-parse --short HEAD)"
  branch="$(git -C "$src" branch --show-current)"
  ok "$(basename "$src"): $branch @ $sha"
  SHAS="${SHAS}${pair%%:*}=$sha ($branch)
"
done

# ── 2. rsync upstream → here ──────────────────────────────────────────────────
# NO --delete, on purpose: it would wipe every file this fork adds (pod-volume.sh,
# scrub-secrets.sh, the specs...). Trade-off: files DELETED upstream linger here.
# That's the safer direction to be wrong in.
for pair in $PAIRS; do
  src="$UPSTREAM_DIR/${pair%%:*}"; dst="$ROOT/${pair##*:}"
  [ -d "$dst" ] || die "$dst missing from this monorepo"
  log "rsync $(basename "$src")/ → ${pair##*:}/"
  rsync -a \
    --exclude='.git' --exclude='.git/**' \
    --exclude='.env' --exclude='.env.*' \
    --exclude='setup/templates.json' \
    --exclude='node_modules' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='venv' --exclude='.venv' \
    --exclude='.nuxt' --exclude='.output' --exclude='.nitro' --exclude='.data' \
    "$src/" "$dst/" || die "rsync failed for $pair"
done
ok "rsync done"

# ── 3. Re-scrub — the whole reason this script exists ─────────────────────────
SCRUB="$ROOT/motions-studio/setup/scrub-secrets.sh"
[ -x "$SCRUB" ] || die "$SCRUB missing or not executable — did rsync clobber it?"
log "re-scrubbing upstream secrets…"
bash "$SCRUB" || die "scrub-secrets.sh failed — NOT committing"

log "gate: scrub-secrets.sh --check"
bash "$SCRUB" --check >/dev/null 2>&1 \
  || { bash "$SCRUB" --check; die "gate FAILED — secrets still present, NOT committing"; }
ok "gate passed"

# ── 3b. Gate: các bản sửa CỤC BỘ trên file upstream còn nguyên không ───────────
# rsync ở bước 2 không dùng --delete (xem lý do ở đó), nhưng nó VẪN ghi đè mọi file upstream mà
# fork này đã sửa — âm thầm. Trước cổng này, giữ được delta hay không phụ thuộc vào việc con người
# có nhớ ra hay không, và mỗi lần quên thì hỏng chỉ lộ ra ở lần build hoặc lần dựng pod sau, dưới
# một triệu chứng không liên quan gì tới chữ "sync".
DELTA_CHECK="$ROOT/scripts/check-local-deltas.sh"
if [ -f "$DELTA_CHECK" ]; then
  log "gate: check-local-deltas.sh"
  bash "$DELTA_CHECK" || die "delta cục bộ bị sync ghi đè — áp dụng lại rồi chạy lại, NOT committing"
else
  warn "không thấy scripts/check-local-deltas.sh — không kiểm được delta cục bộ"
fi

# ── 4. Record what we synced ──────────────────────────────────────────────────
{ printf '# Upstream commits this monorepo was last synced from.\n'
  printf '# Written by scripts/sync-upstream.sh — do not edit by hand.\n'
  printf '%s' "$SHAS"
} > "$ROOT/UPSTREAM_SHA"
ok "UPSTREAM_SHA updated"

# ── 5. Report / commit ────────────────────────────────────────────────────────
if [ -z "$(git -C "$ROOT" status --porcelain)" ]; then
  ok "nothing changed — already up to date with upstream"
  exit 0
fi

log "changes from upstream:"
git -C "$ROOT" add -A
git -C "$ROOT" diff --cached --stat | tail -25 | sed 's/^/    /'

if [ "$DO_COMMIT" != 1 ]; then
  printf '\n'
  log "staged but NOT committed — review the diff, then:"
  printf '      git diff --cached\n'
  printf '      git commit -m "Sync upstream: %s"\n' "$(printf '%s' "$SHAS" | tr '\n' ' ')"
  printf '   or re-run with:  make sync-upstream COMMIT=1\n'
  exit 0
fi

git -C "$ROOT" commit -q -m "Sync upstream

$SHAS
Scrubbed again by setup/scrub-secrets.sh — rsync had restored the upstream
credentials (shared DEFAULT_API_KEY, Motion Task Cloud admin key, templates.json,
personal emails). Gate --check passed before this commit."
ok "committed: $(git -C "$ROOT" rev-parse --short HEAD)"
log "push when you're happy:  git push"

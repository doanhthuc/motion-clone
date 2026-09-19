# Vast Bot Provider Picker Implementation Plan (Plan 4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pick Vast.ai per batch from the Telegram bot: a `[RunPod] [Vast]` row on the Choose GPU screen, a Vast tab that prices this batch and hides its spend button with concrete reasons, the chosen provider carried in the spend button's callback data through to `drain.py --provider`, a "Rent on Vast" button on the RunPod stock-out card, and progress / `/kill` text that never prices a Vast pod at RunPod's flat $0.99.

**Architecture:** The provider is a property of one *button*, not of `.env` or bot state (`run:go:<token>:vast`). A read-only quote (`VAST_QUOTE=1 bash scripts/pod-provision.sh` → `vast_rent.py --quote`, one JSON line, never a rental) feeds a pure view-model (`tgbot/vast_panel.py`) that decides what the tab says and whether it may show a spend button. The spend handlers (`_do_confirm`, `_do_resume`) re-check the same conditions at tap time, before the manifest is rewritten. RunPod's path is byte-for-byte unchanged: no suffix means "whatever `.env` says", exactly as today.

**Tech Stack:** Python 3 `unittest` (stdlib only), Telegram inline-keyboard callback data (64-byte cap), bash, the `vastai` CLI.

**Spec:** `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` — §3.5 (bot panel and callbacks), §3.1's `start_drain` forwarding, §8 item 5. The `/kill`-passes-the-lease-provider half of §3.1 was already done in Plan 1 (`_do_kill`, `kill_env`), so nothing here touches it. §8 item 6 (the two paid sessions) is **not** in this plan: it spends real money and needs the user's go-ahead.

## Verification status of this plan (read before executing)

Every code block below is the literal diff or file produced by running these seven tasks **in order in a scratch git worktree from `67cf59e`**, with each task's tests passing at its own commit (Task 4: 652 bot tests, Task 5: 664, Task 6: 670, all green; the rest of the suite is green at the end). The protective behaviours were also **mutation-checked**: 12 deliberate breakages (migration guard applied to Vast, no refusal in `_do_resume`, refusal moved after the manifest write, Vast gate applied to a queued job, provider not forwarded to `start_drain`, spend button always shown, Vast priced at the RunPod rate, latch dropped on refusal, unknown suffix accepted, `/kill` quoting the RunPod rate, credit never checked, static blockers emptied) were each caught by at least one test, and the unmodified baseline passed. **No live Telegram or Vast rental was involved**: the bot's UI was exercised only through the repo's `FakeTg`, and the only real network calls made while planning were read-only (`vastai show user --raw` and dry-run searches; nothing was rented — `vastai show instances-v1` listed zero instances afterwards).

> **Superseded by review fix rounds (as built):** Task 4's fix commit `08dede0` made `spend_blockers` refuse when there is no price quote in the bot process (the diffs embedded in Tasks 3 and 4 predate it: the panel suite is 24 tests, and the bot suite is 654 / 666 / 672 tests after Tasks 4 / 5 / 6, not 652 / 664 / 670). Task 7's fix commit `7f6dfb0` reworded the post-restart recovery paragraph in `docs/gpu-pod.md` and the refusal string in `scripts/tgbot/vast_panel.py`, because the Vast tab cannot be re-opened from an old panel for a try-on batch. For those points read the code, not the blocks embedded below.

## Global Constraints

Every task's requirements implicitly include this section.

1. **RunPod is unchanged.** A call with no provider stays exactly `start_drain(path, dry_run=…, …)` (existing tests pin the exact kwargs), every RunPod message keeps its text (`Started. N job(s) on one pod at $0.99/hour.`, the `$0.99` progress line, `/kill`'s `($…)` figure), and the RunPod screen changes only by gaining the `[RunPod] [Vast]` row. `None` means "use `.env`'s `GPU_PROVIDER`", as today.
2. **The provider travels in callback data, never in `.env` or bot state.** Only the suffix `:vast` exists (`run:go:<token>:vast`, `pa:spend:<token>:vast`, `pa:reuse:<token>:vast`, `pa:rerun:<token>:vast`). Any other suffix is refused like a stale token. Every callback string must fit Telegram's **64-byte** cap (`Tg.keyboard` asserts it): the longest, `pa:reuse:` + a 19-digit token + `:vast`, is 33.
3. **A Vast spend is gated twice.** The panel hides the spend button, with reasons written out; the handlers re-check at tap time (buttons outlive the state they were drawn for). A refusal spends nothing, keeps the draft, and **does not rewrite the manifest** — its mtime is the run token every button in the chat is checked against.
4. **`CONFIRM=yes` appears in exactly one executable string literal under `scripts/tgbot/`** (`run.start_drain`; `test_batch_tgrun` asserts it with an AST walk). A price quote can never rent: `vast_rent.py --quote` refuses `--confirm`, `pod-provision.sh` lets `VAST_QUOTE=1` outrank `CONFIRM=yes`, and `vast_quote.fetch_quote` removes `CONFIRM` from the child's environment.
5. **Vast's spend button exists only for pipelines named in `VAST_ENABLED_PIPELINES`** (empty by default). Spec §1: one measured Vast session per pipeline family before its button is enabled; as of 2026-09-19 only the `motion` stage has ever run on Vast.
6. **Costs shown for Vast are estimates and say so** (`≈`, `quoted`). Never assert cost from anything but a measurement or the invoice (CLAUDE.md), and never price a Vast pod at RunPod's flat $0.99.
7. **Telegram HTML:** only `<b> <i> <code> <pre> <blockquote>` and the repo's `<tg-emoji>`; `FakeTg` rejects anything else. Escape every interpolated reason (it can carry a command's stderr).
8. **Conventions (CLAUDE.md):** English comments/docs/commits; no `# #region ALD` markers; a comment explains *why* with the measured number and its date; no new secrets (`motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit — the repo is public).
9. **Free gates before every commit, no pod:** the task's own tests, then at the end `make batch-test`, `make check-job-types`, `make check-comfy-nodes`, `make check-batch-params`, `make check-vast-models`.

## What planning found that Plans 1–3 could not have seen

- **The GPU name.** The bot's `.env` holds the RunPod spelling (`GPU=NVIDIA GeForce RTX 5090`), and `pod-provision.sh`'s Vast branch passed `$GPU` straight into the marketplace query. Measured 2026-09-19 with a dry run (nothing rented): `gpu_name=NVIDIA GeForce RTX 5090` fails with `vastai search returned invalid JSON`; `gpu_name=RTX_5090` returned 4 qualifying offers. So every bot-driven Vast run would have failed at search. Task 1 fixes it.
- **`vastai show user --raw`** returns the prepaid balance as `credit` (10.70 on the live account) next to a `balance` of 0. `credit` is what Task 2 reads.
- **A quote takes ~4 s** (`pod-provision.sh` dry run, measured 2026-09-19), so it runs synchronously in the bot's poll loop behind an interstitial ("Asking Vast for offers…"), bounded at 120 s.
- **`.env`'s `GPU_PROVIDER` may be `vast`** on a machine set up for the manual sessions. The RunPod tab does not override it (spec: RunPod has no suffix). Keep `.env` at `runpod` on the bot's host, as the spec already says.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/pod-provision.sh` | modify | Vast branch: RunPod→Vast GPU name; `VAST_QUOTE=1` |
| `scripts/vast_rent.py` | modify | `--quote`: one JSON line, mutually exclusive with `--confirm` |
| `scripts/batchlib_ext/vast_account.py` | create | `account_credit()` from `vastai show user --raw` |
| `scripts/batchlib_ext/vast_quote.py` | create | `fetch_quote()` (runs the script in quote mode, cached), `last_quote()` |
| `scripts/tgbot/vast_panel.py` | create | Pure view-model: blockers, GPU-seconds, session estimate, the tab's text |
| `scripts/tgbot/run.py` | modify | `start_drain(gpu_provider=)`; `progress_text(provider=, usd_per_hr=)` |
| `scripts/drain.py` | modify | `vast_download_gb(manifest)` — one definition for the rent command and the quote |
| `scripts/tgbot/bot.py` | modify | Callback suffix, the two spend gates, the picker, the stock-out button, billing text |
| `docs/gpu-pod.md`, `.env.example`, the spec | modify | Document the tab, `VAST_ENABLED_PIPELINES`, `VAST_GPU`, and what is still unverified |
| `scripts/tests/…` | modify / create | One test module per unit; bot tests appended to `test_batch_bot.py` |

---

### Task 1: A Vast search that works from the bot's `.env`, and a quote mode that cannot rent

**Files:**
- Modify: `scripts/pod-provision.sh` (the Vast branch, from `VAST_ARGS=` down)
- Modify: `scripts/vast_rent.py` (`_parse`, `main`, a new `quote_json`)
- Test: `scripts/tests/test_batch_vast_rent.py` (append to `TestMain`), `scripts/tests/test_batch_provider_wiring.py` (append a class)

**Interfaces:**
- Produces: `VAST_QUOTE=1 GPU_PROVIDER=vast POD_VOLUME= bash scripts/pod-provision.sh` prints, as its **last stdout line**, one JSON object with keys `offer_id, machine_id, dph, gpu, location, ready_s, known, bandwidth_usd, gb, qualifying`, and rents nothing. When no offer qualifies it exits 1 with the reason on stderr (`✗ 0 of N offers qualify (…)`). `--quote` and `--confirm` together exit 2 before any search. `VAST_GPU` (env or `.env`) overrides the GPU name; otherwise `NVIDIA GeForce RTX 5090` → `RTX_5090`, `NVIDIA GeForce RTX 4090` → `RTX_4090`, an already-Vast name passes through, and any other name containing a space is refused by name before any search.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing tests**

The wiring tests run the **real** `pod-provision.sh` with a fake `vastai` first on `PATH` (it answers searches and exits 9 on anything else, so a rent attempt from a path that must never rent shows up as a failure). No network, no rental.

`scripts/tests/test_batch_vast_rent.py`:

```diff
--- a/scripts/tests/test_batch_vast_rent.py
+++ b/scripts/tests/test_batch_vast_rent.py
@@ -711,6 +711,30 @@ class TestMain(unittest.TestCase):
         self.assertEqual(api.destroyed, ["i1"])
         self.assertNotIn("GPU_INSTANCE_ID=i1", (self.tmp / ".env").read_text(encoding="utf-8"))
 
+    def test_quote_prints_one_json_line_and_rents_nothing(self):
+        api = FakeVast([offer(7, 70)])
+        rc, out, _ = self._main(api, "--quote")
+        self.assertEqual(rc, 0)
+        quote = json.loads(out)
+        self.assertEqual(quote["offer_id"], 7)
+        self.assertEqual(quote["machine_id"], 70)
+        self.assertAlmostEqual(quote["dph"], 0.40)
+        self.assertFalse(quote["known"])
+        self.assertEqual(quote["qualifying"], 1)
+        self.assertEqual(api.create_attempts, [])
+
+    def test_quote_and_confirm_together_are_refused_before_any_search(self):
+        api = FakeVast([offer(7, 70)])
+        rc, out, _ = self._main(api, "--quote", "--confirm")
+        self.assertEqual((rc, out), (2, ""))
+        self.assertEqual(api.queries, [])
+        self.assertEqual(api.create_attempts, [])
+
+    def test_quote_with_no_qualifying_offer_exits_1_with_the_reason(self):
+        rc, out, err = self._main(FakeVast([offer(7, 70, dph=0.99)]), "--quote")
+        self.assertEqual((rc, out), (1, ""))
+        self.assertIn("over price cap", err)
+
     def test_ssh_target_prints_host_and_port(self):
         rc, out, _ = self._main(FakeVast([]), "--ssh-target", "51518664")
         self.assertEqual((rc, out.strip()), (0, "1.2.3.4 40022"))
```

`scripts/tests/test_batch_provider_wiring.py`:

```diff
--- a/scripts/tests/test_batch_provider_wiring.py
+++ b/scripts/tests/test_batch_provider_wiring.py
@@ -446,3 +446,85 @@ class TestPodWaitDirectAddress(unittest.TestCase):
 
 if __name__ == "__main__":
     unittest.main()
+
+
+# One qualifying offer, shaped like `vastai search offers --raw` rows (same fields the ranker reads).
+_FAKE_OFFER = {"id": 4401, "machine_id": 55, "gpu_name": "RTX 5090", "dph_total": 0.45,
+               "inet_down": 1800.0, "internet_down_cost_per_tb": 2.0, "disk_bw": 3800.0,
+               "cpu_ghz": 3.0, "direct_port_count": 12, "rentable": True,
+               "geolocation": "Bulgaria, BG"}
+
+# A `vastai` that answers searches and fails loudly on anything else, so a rent attempt from a path
+# that must never rent shows up as exit 9 plus a "create" line in the log.
+_FAKE_VASTAI = f"""#!/usr/bin/env python3
+import json, os, sys
+with open(os.environ["FAKE_VASTAI_LOG"], "a", encoding="utf-8") as log:
+    log.write(" ".join(sys.argv[1:]) + "\\n")
+if sys.argv[1:3] == ["search", "offers"]:
+    print(json.dumps([{_FAKE_OFFER!r}]).replace("True", "true"))
+    sys.exit(0)
+sys.exit(9)
+"""
+
+
+class TestProvisionVastBranch(unittest.TestCase):
+    """The real pod-provision.sh against a fake `vastai` first on PATH: no network, no rental."""
+
+    def setUp(self):
+        self.tmp = Path(tempfile.mkdtemp())
+        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
+        (self.tmp / ".env").write_text("", encoding="utf-8")
+        self.bin = self.tmp / "bin"
+        self.bin.mkdir()
+        fake = self.bin / "vastai"
+        fake.write_text(_FAKE_VASTAI, encoding="utf-8")
+        fake.chmod(0o755)
+        self.log = self.tmp / "vastai.log"
+
+    def _run(self, **env):
+        base = {k: v for k, v in os.environ.items()
+                if k not in ("GPU", "GPU_PROVIDER", "POD_VOLUME", "CONFIRM", "VAST_QUOTE",
+                             "VAST_GPU", "VAST_GB", "OFFER", "SKIP", "MAX_DPH")}
+        base.update({"PATH": f"{self.bin}{os.pathsep}{base['PATH']}", "GPU_PROVIDER": "vast",
+                     "POD_VOLUME": "", "FAKE_VASTAI_LOG": str(self.log)})
+        base.update(env)
+        return subprocess.run(["bash", str(ROOT / "scripts" / "pod-provision.sh")], cwd=self.tmp,
+                              env=base, capture_output=True, text=True, timeout=60)
+
+    def _calls(self) -> list[str]:
+        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
+
+    def test_a_runpod_gpu_name_is_translated_to_the_vast_spelling(self):
+        out = self._run(GPU="NVIDIA GeForce RTX 5090", VAST_QUOTE="1")
+        self.assertEqual(out.returncode, 0, out.stderr)
+        self.assertIn("gpu_name=RTX_5090", self._calls()[0])
+        self.assertNotIn("GeForce", self._calls()[0])
+
+    def test_vast_gpu_overrides_the_translation(self):
+        out = self._run(GPU="NVIDIA GeForce RTX 5090", VAST_GPU="RTX_4090", VAST_QUOTE="1")
+        self.assertEqual(out.returncode, 0, out.stderr)
+        self.assertIn("gpu_name=RTX_4090", self._calls()[0])
+
+    def test_an_already_vast_spelled_gpu_passes_through_untouched(self):
+        out = self._run(GPU="RTX_5090", VAST_QUOTE="1")
+        self.assertEqual(out.returncode, 0, out.stderr)
+        self.assertIn("gpu_name=RTX_5090", self._calls()[0])
+
+    def test_an_unknown_runpod_name_is_refused_by_name_before_any_search(self):
+        out = self._run(GPU="NVIDIA RTX PRO 4500 Blackwell", VAST_QUOTE="1")
+        self.assertNotEqual(out.returncode, 0)
+        self.assertIn("VAST_GPU", out.stderr)
+        self.assertEqual(self._calls(), [])
+
+    def test_a_quote_prints_one_json_line_and_never_rents_even_with_confirm_set(self):
+        out = self._run(GPU="RTX_5090", VAST_QUOTE="1", CONFIRM="yes")
+        self.assertEqual(out.returncode, 0, out.stderr)
+        quote = json.loads(out.stdout.strip().splitlines()[-1])
+        self.assertEqual(quote["offer_id"], 4401)
+        self.assertAlmostEqual(quote["dph"], 0.45)
+        self.assertFalse([c for c in self._calls() if c.startswith("create")], self._calls())
+
+    def test_without_a_quote_and_without_confirm_it_is_still_the_plain_dry_run(self):
+        out = self._run(GPU="RTX_5090")
+        self.assertEqual(out.returncode, 0, out.stderr)
+        self.assertEqual(out.stdout.strip().splitlines()[-1], "4401")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_vast_rent scripts.tests.test_batch_provider_wiring`
Expected: FAIL — the three new `TestMain` quote tests (argparse rejects `--quote`) and the `TestProvisionVastBranch` tests (no translation, no quote mode).

- [ ] **Step 3: Implement**

The comment in `pod-provision.sh` deliberately avoids the literal words `vastai search offers`: an existing test (`test_pod_provision_hands_the_vast_branch_to_the_rent_function`) forbids that string anywhere in the script.

`scripts/vast_rent.py`:

```diff
--- a/scripts/vast_rent.py
+++ b/scripts/vast_rent.py
@@ -394,6 +394,19 @@ def rent(api: VastApi, cfg: RentConfig, board: Scoreboard, *, confirm: bool,
                     f"after {pulls} pull attempt(s) and {create_failures} failed create(s){tail}")
 
 
+def quote_json(result: RentResult, gb: float) -> str:
+    """The best offer and the terms that priced it, as one JSON line (`--quote`). The bot's
+    provider panel reads this instead of scraping the human-readable shortlist."""
+    best = result.chosen
+    offer = best.offer
+    return json.dumps({
+        "offer_id": offer.get("id"), "machine_id": best.machine_id,
+        "dph": float(offer["dph_total"]), "gpu": offer.get("gpu_name"),
+        "location": offer.get("geolocation"), "ready_s": best.ready_s, "known": best.known,
+        "bandwidth_usd": best.bandwidth_usd, "gb": gb, "qualifying": len(result.ranked)},
+        sort_keys=True)
+
+
 _IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
 _HOSTNAME_CHARS_RE = re.compile(r"^[A-Za-z0-9.-]+$")
 
@@ -451,6 +464,9 @@ def _parse(argv: list[str] | None) -> argparse.Namespace:
     ap.add_argument("--skip", default="")
     ap.add_argument("--offer", default="")
     ap.add_argument("--confirm", action="store_true")
+    ap.add_argument("--quote", action="store_true",
+                    help="dry run that prints ONE JSON line (the best offer and its cost terms); "
+                         "never rents, and cannot be combined with --confirm")
     ap.add_argument("--ssh-target", metavar="INSTANCE_ID")
     return ap.parse_args(argv)
 
@@ -493,6 +509,10 @@ def main(argv: list[str] | None = None) -> int:
             _stderr(f"missing --{required.replace('_', '-')}")
             return 2
 
+    if args.quote and args.confirm:
+        _stderr("--quote and --confirm are mutually exclusive: a quote never rents")
+        return 2
+
     # F7/I5: the deadline must stay under the watchdog's grace (tier 3 reaps an unleased
     # labelled instance GRACE_MIN minutes after it first sees it) with a minute of slack for
     # bookkeeping — see test_the_pull_deadline_leaves_room_inside_the_watchdog_grace.
@@ -527,6 +547,9 @@ def main(argv: list[str] | None = None) -> int:
         print(f"\033[31m ✗ \033[0m{exc}", file=sys.stderr)
         return 1
 
+    if args.quote:
+        print(quote_json(result, args.gb))
+        return 0
     if not args.confirm:
         _stderr(f"\n{len(result.ranked)} qualifying offer(s), best first:\n"
                 f"{format_table(result.ranked)}\n\n"
```

`scripts/pod-provision.sh`:

```diff
--- a/scripts/pod-provision.sh
+++ b/scripts/pod-provision.sh
@@ -481,10 +481,33 @@ fi
 command -v vastai >/dev/null || die "vastai CLI not found:  pip install vastai  &&  vastai set api-key <key>"
 command -v python3 >/dev/null || die "python3 needed to run scripts/vast_rent.py"
 
-VAST_ARGS=(--gpu "$GPU" --disk "$DISK" --image "$IMAGE" --max-dph "$MAX_DPH"
+# The Telegram bot's .env carries the RunPod spelling of the GPU ("NVIDIA GeForce RTX 5090"); the
+# Vast marketplace spells it RTX_5090, and a name with spaces is not even one search token. Measured
+# 2026-09-19: a marketplace search filtered on gpu_name=NVIDIA GeForce RTX 5090 fails with "invalid
+# JSON", while gpu_name=RTX_5090 returned 4 qualifying offers. VAST_GPU wins when set; otherwise the
+# two RunPod names this repo uses are translated, and any other name with a space is refused by name
+# rather than sent to a search that can only fail.
+VAST_GPU="${VAST_GPU:-$(env_get VAST_GPU)}"
+if [ -z "$VAST_GPU" ]; then
+  case "$GPU" in
+    "NVIDIA GeForce RTX 5090") VAST_GPU=RTX_5090 ;;
+    "NVIDIA GeForce RTX 4090") VAST_GPU=RTX_4090 ;;
+    *" "*) die "GPU='$GPU' is a RunPod name, and Vast spells GPUs differently (e.g. RTX_5090).
+    Set VAST_GPU=RTX_5090 (or the Vast name you want) in .env or the environment." ;;
+    *) VAST_GPU="$GPU" ;;
+  esac
+fi
+
+VAST_ARGS=(--gpu "$VAST_GPU" --disk "$DISK" --image "$IMAGE" --max-dph "$MAX_DPH"
            --reliability "$RELIABILITY" --min-disk-bw "$MIN_DISK_BW" --min-cpu-ghz "$MIN_CPU_GHZ")
 [ -n "$OFFER" ] && VAST_ARGS+=(--offer "$OFFER")
 [ -n "$SKIP" ] && VAST_ARGS+=(--skip "$SKIP")
-[ "${CONFIRM:-}" = "yes" ] && VAST_ARGS+=(--confirm)
+# VAST_QUOTE=1 is the bot's price check: vast_rent.py prints one JSON line and rents nothing. It
+# outranks CONFIRM=yes on purpose, so a quote can never turn into a rental whatever else is set.
+if [ "${VAST_QUOTE:-}" = "1" ]; then
+  VAST_ARGS+=(--quote)
+elif [ "${CONFIRM:-}" = "yes" ]; then
+  VAST_ARGS+=(--confirm)
+fi
 
 exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vast_rent.py" "${VAST_ARGS[@]}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_vast_rent scripts.tests.test_batch_provider_wiring`
Expected: OK (119 tests at the time of writing).

- [ ] **Step 5: Gates and commit**

```bash
bash -n scripts/pod-provision.sh && motions-studio/setup/scrub-secrets.sh --check
git add scripts/pod-provision.sh scripts/vast_rent.py scripts/tests/test_batch_vast_rent.py scripts/tests/test_batch_provider_wiring.py
git commit -m "vast: translate the RunPod GPU name for Vast, add a read-only --quote mode"
```

---

### Task 2: The account and the quote, as two small read-only modules

**Files:**
- Create: `scripts/batchlib_ext/vast_account.py`, `scripts/batchlib_ext/vast_quote.py`
- Test: `scripts/tests/test_batch_vast_account.py`, `scripts/tests/test_batch_vast_quote.py` (both new)

**Interfaces:**
- Consumes: Task 1's quote mode (through the subprocess boundary only; the tests fake `subprocess.run`).
- Produces:
  - `vast_account.account_credit() -> float` — the account's prepaid credit in USD; `RuntimeError` on any failure (never `0.0` for "could not read").
  - `vast_quote.VastQuote(offer_id: int, machine_id: int | None, dph: float, gpu: str, location: str, ready_s: float, known: bool, bandwidth_usd: float, gb: float, qualifying: int, fetched_at: float)` (frozen dataclass).
  - `vast_quote.fetch_quote(gb: float, *, force: bool = False, run=subprocess.run, now=time.time, repo_root: Path = ROOT) -> VastQuote` — cached `TTL_SEC` (60 s) per download size rounded to 0.1 GB; `RuntimeError(reason)` on failure, with the ANSI colour and the "Knobs" paragraph stripped.
  - `vast_quote.last_quote() -> VastQuote | None` — the newest quote that succeeded, however old.
  - `vast_quote.BOOT_AFTER_RUNNING_S = 232.0` (17 s SSH + 200 s bootstrap + 15 s ComfyUI restart, measured 2026-09-19 on one warm host), `TTL_SEC = 60.0`, `TIMEOUT_SEC = 120`.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_batch_vast_account.py` (new file):

```python
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.vast_account import account_credit

# The fields that matter from `vastai show user --raw`, as captured 2026-09-19 (other keys omitted).
_USER_JSON = json.dumps({"balance": 0, "credit": 10.701384150700019, "can_pay": True,
                         "username": "x"})


class TestAccountCredit(unittest.TestCase):
    @patch("subprocess.run")
    def test_reads_credit_not_balance(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_USER_JSON, stderr="")
        self.assertAlmostEqual(account_credit(), 10.701384150700019)
        self.assertEqual(mock_run.call_args[0][0], ["vastai", "show", "user", "--raw"])

    @patch("subprocess.run")
    def test_non_zero_exit_raises_with_the_cli_message(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no api key")
        with self.assertRaisesRegex(RuntimeError, "no api key"):
            account_credit()

    @patch("subprocess.run")
    def test_malformed_json_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with self.assertRaises(RuntimeError):
            account_credit()

    @patch("subprocess.run")
    def test_missing_credit_raises_rather_than_reporting_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"balance": 0}),
                                          stderr="")
        with self.assertRaisesRegex(RuntimeError, "no credit"):
            account_credit()

    @patch("subprocess.run")
    def test_a_boolean_credit_is_not_a_number(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"credit": True}),
                                          stderr="")
        with self.assertRaises(RuntimeError):
            account_credit()

    @patch("subprocess.run", side_effect=FileNotFoundError("vastai"))
    def test_a_missing_binary_raises_runtimeerror(self, _run):
        with self.assertRaisesRegex(RuntimeError, "could not run vastai"):
            account_credit()

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("vastai", 30))
    def test_a_hang_raises_runtimeerror(self, _run):
        with self.assertRaisesRegex(RuntimeError, "could not run vastai"):
            account_credit()


if __name__ == "__main__":
    unittest.main()
```

`scripts/tests/test_batch_vast_quote.py` (new file):

```python
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext import vast_quote
from batchlib_ext.vast_quote import fetch_quote, last_quote

_JSON = json.dumps({"offer_id": 4401, "machine_id": 55, "dph": 0.45, "gpu": "RTX 5090",
                    "location": "Bulgaria, BG", "ready_s": 556.0, "known": False,
                    "bandwidth_usd": 0.07, "gb": 51.8, "qualifying": 6})


def _proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRun:
    """Stands in for subprocess.run: records every call, answers from a queue (last one repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class TestFetchQuote(unittest.TestCase):
    def setUp(self):
        vast_quote._CACHE.clear()
        vast_quote._last = None
        self.clock = [1000.0]

    def _fetch(self, run, gb=51.8, **kw):
        return fetch_quote(gb, run=run, now=lambda: self.clock[0], repo_root=Path("/repo"), **kw)

    def test_parses_the_last_json_line_amid_log_noise(self):
        run = FakeRun(_proc(stdout="\x1b[36m==>\x1b[0m something\nnoise\n" + _JSON + "\n"))
        q = self._fetch(run)
        self.assertEqual((q.offer_id, q.machine_id, q.dph, q.location), (4401, 55, 0.45, "Bulgaria, BG"))
        self.assertFalse(q.known)
        self.assertEqual(q.ready_s, 556.0)

    def test_runs_the_provision_script_in_quote_mode_on_vast_with_no_volume(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run)
        argv, kwargs = run.calls[0]
        self.assertEqual(argv, ["bash", "/repo/scripts/pod-provision.sh"])
        self.assertEqual(kwargs["cwd"], Path("/repo"))
        env = kwargs["env"]
        self.assertEqual((env["GPU_PROVIDER"], env["POD_VOLUME"], env["VAST_QUOTE"]),
                         ("vast", "", "1"))
        self.assertEqual(env["VAST_GB"], "51.8")

    def test_confirm_never_reaches_the_child_even_when_the_bot_process_has_it(self):
        run = FakeRun(_proc(stdout=_JSON))
        with mock.patch.dict(os.environ, {"CONFIRM": "yes"}):
            self._fetch(run)
        self.assertNotIn("CONFIRM", run.calls[0][1]["env"])

    def test_a_second_call_inside_the_ttl_does_not_search_again(self):
        run = FakeRun(_proc(stdout=_JSON))
        first = self._fetch(run)
        self.clock[0] += vast_quote.TTL_SEC - 1
        self.assertIs(self._fetch(run), first)
        self.assertEqual(len(run.calls), 1)

    def test_force_and_an_expired_ttl_both_search_again(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run)
        self._fetch(run, force=True)
        self.clock[0] += vast_quote.TTL_SEC + 1
        self._fetch(run)
        self.assertEqual(len(run.calls), 3)

    def test_a_different_download_size_is_a_different_cache_entry(self):
        run = FakeRun(_proc(stdout=_JSON))
        self._fetch(run, gb=51.8)
        self._fetch(run, gb=17.4)
        self.assertEqual(len(run.calls), 2)

    def test_a_failure_raises_the_reason_without_ansi_or_the_knobs_paragraph(self):
        err = ("searching: gpu_name=RTX_5090 ...\n\x1b[31m ✗ \x1b[0m0 of 40 offers qualify "
               "(40 over price cap).\n  Knobs (env or .env): MAX_DPH, ...\n")
        run = FakeRun(_proc(returncode=1, stderr=err))
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch(run)
        self.assertEqual(str(ctx.exception), "0 of 40 offers qualify (40 over price cap).")

    def test_a_failure_without_a_marker_reports_the_last_line(self):
        run = FakeRun(_proc(returncode=1, stderr="Traceback (most recent call last):\nValueError: boom\n"))
        with self.assertRaisesRegex(RuntimeError, "ValueError: boom"):
            self._fetch(run)

    def test_no_json_at_all_raises(self):
        with self.assertRaisesRegex(RuntimeError, "no JSON"):
            self._fetch(FakeRun(_proc(stdout="just log lines\n")))

    def test_unreadable_json_raises(self):
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            self._fetch(FakeRun(_proc(stdout='{"offer_id": "x"}')))

    def test_a_hang_or_a_missing_shell_raises_runtimeerror(self):
        for exc in (subprocess.TimeoutExpired("bash", 120), FileNotFoundError("bash")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaisesRegex(RuntimeError, "could not run"):
                    self._fetch(FakeRun(exc))

    def test_last_quote_survives_a_later_failure(self):
        self.assertIsNone(last_quote())
        q = self._fetch(FakeRun(_proc(stdout=_JSON)))
        self.assertIs(last_quote(), q)
        with self.assertRaises(RuntimeError):
            self._fetch(FakeRun(_proc(returncode=1, stderr="x")), gb=99.0)
        self.assertIs(last_quote(), q)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_vast_account scripts.tests.test_batch_vast_quote`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib_ext.vast_account'` (and `vast_quote`).

- [ ] **Step 3: Implement**

`scripts/batchlib_ext/vast_account.py` (new file):

```python
"""Vast account credit via `vastai show user --raw`. Read-only — no renting.

Only `credit` is read: it is the prepaid balance in USD (a live account showed credit 10.70 next to
`balance` 0 on 2026-09-19, and docs/gpu-pod.md quotes the same field). Same contract as
runpod_account.account_balance: any failure raises RuntimeError and the caller decides what to tell
the user — an unreadable account must read as "cannot check", never as "$0".
"""
from __future__ import annotations

import json
import subprocess

_TIMEOUT_SEC = 30   # one HTTPS round trip behind the CLI, the same bound as runpod_account.


def account_credit() -> float:
    try:
        out = subprocess.run(["vastai", "show", "user", "--raw"],
                             capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not run vastai: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"vastai show user failed: {(out.stderr or out.stdout).strip()[:200]}")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"vastai returned invalid JSON: {exc}") from exc
    credit = data.get("credit") if isinstance(data, dict) else None
    if isinstance(credit, bool) or not isinstance(credit, (int, float)):
        raise RuntimeError("vastai show user returned no credit — is the API key set?")
    return float(credit)
```

`scripts/batchlib_ext/vast_quote.py` (new file):

```python
"""A read-only price check for one Vast rental, for the bot's provider panel.

Runs the REAL scripts/pod-provision.sh in quote mode (VAST_QUOTE=1) so the search filters, image,
disk and GPU spelling come from the one place that already derives them — a second copy of that
configuration in Python would drift the first time a knob changed. vast_rent.py --quote prints one
JSON line and never rents; this module additionally strips CONFIRM from the child's environment, so
even a bot process that somehow carried it could not turn a price check into a rental.

Cached for TTL_SEC per download size: the panel redraws on every tap, and a search is a marketplace
round trip (about 4 s measured 2026-09-19). Refresh passes force=True.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SEC = 120
TTL_SEC = 60.0
# What still has to happen AFTER the instance reports `running`, measured 2026-09-19 on a warm host
# (docs/gpu-pod.md#vast-e2e): SSH answering ~17 s later, bootstrap 200 s (the model download runs
# inside it), ComfyUI restart ~15 s. The per-machine create -> running time is separate: it comes
# from the scoreboard, or is the slowest ever seen when the machine is unmeasured.
BOOT_AFTER_RUNNING_S = 232.0

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class VastQuote:
    offer_id: int
    machine_id: int | None
    dph: float
    gpu: str
    location: str
    ready_s: float
    known: bool
    bandwidth_usd: float
    gb: float
    qualifying: int
    fetched_at: float


_CACHE: dict[float, VastQuote] = {}
_last: VastQuote | None = None


def last_quote() -> VastQuote | None:
    """The most recent quote that succeeded, however old — what the progress message prices a
    running rental with (labelled an estimate; the real rate is on Vast's own invoice)."""
    return _last


def _reason(stderr: str) -> str:
    text = _ANSI.sub("", stderr or "").strip()
    if "✗" in text:
        text = text.rsplit("✗", 1)[1].strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return (lines[0] if lines else "no reason given")[:300]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (lines[-1] if lines else "no reason given")[:300]


def _parse(stdout: str, *, gb: float, fetched_at: float) -> VastQuote:
    # pod-provision.sh prints its own log/warn lines to stdout before it hands over, so the JSON is
    # the last line that looks like an object, not the whole of stdout.
    objects = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip().startswith("{")]
    if not objects:
        raise RuntimeError("the quote command printed no JSON")
    try:
        d = json.loads(objects[-1])
        machine = d.get("machine_id")
        return VastQuote(
            offer_id=int(d["offer_id"]), machine_id=None if machine is None else int(machine),
            dph=float(d["dph"]), gpu=str(d.get("gpu") or ""), location=str(d.get("location") or ""),
            ready_s=float(d["ready_s"]), known=bool(d["known"]),
            bandwidth_usd=float(d["bandwidth_usd"]), gb=float(d.get("gb", gb)),
            qualifying=int(d.get("qualifying", 0)), fetched_at=fetched_at)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"the quote command printed unreadable JSON: {exc}") from exc


def fetch_quote(gb: float, *, force: bool = False, run=subprocess.run, now=time.time,
                repo_root: Path = ROOT) -> VastQuote:
    """The best qualifying offer for a rental that will download `gb` GB. Raises RuntimeError with
    the reason (no offer qualifies, vastai missing or logged out, unreadable answer)."""
    global _last
    key = round(float(gb), 1)
    t = now()
    cached = _CACHE.get(key)
    if cached is not None and not force and t - cached.fetched_at < TTL_SEC:
        return cached
    env = {k: v for k, v in os.environ.items() if k != "CONFIRM"}
    env.update({"GPU_PROVIDER": "vast", "POD_VOLUME": "", "VAST_QUOTE": "1",
                "VAST_GB": f"{key:.1f}"})
    try:
        out = run(["bash", str(repo_root / "scripts" / "pod-provision.sh")], cwd=repo_root,
                  env=env, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not run pod-provision.sh: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(_reason(out.stderr))
    quote = _parse(out.stdout, gb=key, fetched_at=t)
    _CACHE[key] = quote
    _last = quote
    return quote
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_vast_account scripts.tests.test_batch_vast_quote`
Expected: OK (19 tests).

- [ ] **Step 5: Gates and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib_ext/vast_account.py scripts/batchlib_ext/vast_quote.py scripts/tests/test_batch_vast_account.py scripts/tests/test_batch_vast_quote.py
git commit -m "vast: read the account credit and price a rental without renting"
```

---

### Task 3: What the Vast tab says, decided without Telegram

**Files:**
- Create: `scripts/tgbot/vast_panel.py`
- Test: `scripts/tests/test_batch_vast_panel.py` (new)

**Interfaces:**
- Consumes: `VastQuote`, `BOOT_AFTER_RUNNING_S` (Task 2); `batchlib.vast_models.STAGE_MODEL_IDS` (Plan 3); `tgbot.run.MEASURED_STAGE_SEC`; `batchlib.runner._local_tryon_stage`.
- Produces:
  - `parse_enabled(raw: str | None) -> frozenset[str]`
  - `static_blockers(manifest, enabled) -> list[str]` — pipelines not enabled, stages missing from the registry. No network.
  - `gpu_seconds(manifest) -> float` — measured stage seconds, a local (Phase A) try-on costing none, unmeasured stages at their `timeout_min` ceiling.
  - `session_usd(quote, run_s) -> float` = `dph × (ready_s + BOOT_AFTER_RUNNING_S + run_s) / 3600 + bandwidth_usd`.
  - `spend_blockers(manifest, enabled, *, credit_fn, quote) -> list[str]` — the tap-time subset: static reasons plus credit, the estimate coming from the last quote when there is one.
  - `VastView(lines, blockers, quote, session_usd)` with `.can_spend`, and `build_view(manifest, *, gb, enabled, quote_fn, credit_fn) -> VastView` — `quote_fn`/`credit_fn` are zero-argument callables that raise `RuntimeError`.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_batch_vast_panel.py` (new file):

```python
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import Manifest, Run
from batchlib_ext.vast_quote import BOOT_AFTER_RUNNING_S, VastQuote
from tgbot import vast_panel
from tgbot.vast_panel import (build_view, gpu_seconds, parse_enabled, session_usd,
                              spend_blockers, static_blockers)


def _manifest(*runs):
    return Manifest(path=Path("batch/m.yaml"), runs=list(runs))


MOTION = Run(id="r1", pipeline="motion-enhance", stage_params={"enhance": {"engine": "lanczos"}})


def _quote(**over):
    base = dict(offer_id=1, machine_id=7, dph=0.90, gpu="RTX 5090", location="Bulgaria, BG",
                ready_s=556.0, known=False, bandwidth_usd=0.10, gb=51.8, qualifying=4,
                fetched_at=0.0)
    base.update(over)
    return VastQuote(**base)


class TestParseEnabled(unittest.TestCase):
    def test_splits_trims_and_ignores_blanks(self):
        self.assertEqual(parse_enabled(" motion-enhance , ,character-swap "),
                         frozenset({"motion-enhance", "character-swap"}))

    def test_none_and_empty_are_the_empty_set(self):
        self.assertEqual(parse_enabled(None), frozenset())
        self.assertEqual(parse_enabled(""), frozenset())


class TestStaticBlockers(unittest.TestCase):
    def test_an_unenabled_pipeline_is_named(self):
        reasons = static_blockers(_manifest(MOTION), frozenset())
        self.assertEqual(len(reasons), 1)
        self.assertIn("motion-enhance", reasons[0])
        self.assertIn("VAST_ENABLED_PIPELINES", reasons[0])

    def test_an_enabled_pipeline_with_registered_stages_has_no_blockers(self):
        self.assertEqual(static_blockers(_manifest(MOTION), frozenset({"motion-enhance"})), [])

    def test_a_stage_missing_from_the_registry_is_named(self):
        registry = {k: v for k, v in vast_panel.STAGE_MODEL_IDS.items() if k != "enhance"}
        with mock.patch.object(vast_panel, "STAGE_MODEL_IDS", registry):
            reasons = static_blockers(_manifest(MOTION), frozenset({"motion-enhance"}))
        self.assertEqual(reasons, ["stage enhance has no Vast model registry entry"])

    def test_two_runs_of_one_pipeline_are_reported_once(self):
        other = Run(id="r2", pipeline="motion-enhance")
        self.assertEqual(len(static_blockers(_manifest(MOTION, other), frozenset())), 1)

    def test_an_unknown_pipeline_is_blocked_not_a_crash(self):
        reasons = static_blockers(_manifest(Run(id="x", pipeline="nope")), frozenset())
        self.assertEqual(len(reasons), 1)


class TestGpuSeconds(unittest.TestCase):
    def test_sums_the_measured_stage_times(self):
        self.assertEqual(gpu_seconds(_manifest(MOTION)), 247 + 114)

    def test_a_local_tryon_costs_no_gpu_time(self):
        run = Run(id="t", pipeline="tryon-motion-enhance",
                  stage_params={"tryon": {"provider": "gemini"}})
        self.assertEqual(gpu_seconds(_manifest(run)), 247 + 114)

    def test_a_self_hosted_tryon_does_cost_gpu_time(self):
        run = Run(id="t", pipeline="tryon-motion-enhance",
                  stage_params={"tryon": {"provider": "qwen"}})
        self.assertEqual(gpu_seconds(_manifest(run)), 351 + 247 + 114)

    def test_an_unmeasured_stage_falls_back_to_its_timeout_ceiling(self):
        run = Run(id="c", pipeline="character-swap")
        self.assertEqual(gpu_seconds(_manifest(run)),
                         vast_panel.STAGES["character-swap"].timeout_min * 60)


class TestSessionUsd(unittest.TestCase):
    def test_is_gpu_time_from_create_plus_bandwidth(self):
        q = _quote(dph=0.90, ready_s=556.0, bandwidth_usd=0.10)
        expected = 0.90 * (556.0 + BOOT_AFTER_RUNNING_S + 361.0) / 3600 + 0.10
        self.assertAlmostEqual(session_usd(q, 361.0), expected)


class TestSpendBlockers(unittest.TestCase):
    ENABLED = frozenset({"motion-enhance"})

    def _blockers(self, *, credit=10.0, quote=None, enabled=None):
        def credit_fn():
            if isinstance(credit, Exception):
                raise credit
            return credit
        return spend_blockers(_manifest(MOTION),
                              self.ENABLED if enabled is None else enabled,
                              credit_fn=credit_fn, quote=quote)

    def test_nothing_blocks_an_enabled_pipeline_with_enough_credit(self):
        self.assertEqual(self._blockers(quote=_quote()), [])

    def test_the_static_reasons_apply_at_tap_time_too(self):
        self.assertEqual(len(self._blockers(enabled=frozenset())), 1)

    def test_credit_below_the_last_quotes_estimate_blocks(self):
        reasons = self._blockers(credit=0.05, quote=_quote())
        self.assertEqual(len(reasons), 1)
        self.assertIn("below this session's estimate", reasons[0])

    def test_with_no_quote_yet_only_a_readable_account_is_required(self):
        self.assertEqual(self._blockers(credit=0.05, quote=None), [])
        reasons = self._blockers(credit=RuntimeError("no api key"), quote=None)
        self.assertEqual(len(reasons), 1)
        self.assertIn("no api key", reasons[0])


class TestBuildView(unittest.TestCase):
    ENABLED = frozenset({"motion-enhance"})

    def _view(self, *, quote=None, credit=10.0, manifest=None, enabled=None):
        def quote_fn():
            if isinstance(quote, Exception):
                raise quote
            return quote or _quote()

        def credit_fn():
            if isinstance(credit, Exception):
                raise credit
            return credit
        return build_view(manifest or _manifest(MOTION), gb=51.8,
                          enabled=self.ENABLED if enabled is None else enabled,
                          quote_fn=quote_fn, credit_fn=credit_fn)

    def test_the_happy_path_may_spend_and_shows_the_offer_and_the_estimate(self):
        view = self._view()
        self.assertTrue(view.can_spend)
        text = "\n".join(view.lines)
        self.assertIn("$0.90/h", text)
        self.assertIn("Bulgaria, BG", text)
        self.assertIn("52 GB", text)
        self.assertIn("Vast credit: $10.00", text)
        self.assertNotIn("No spend button", text)
        self.assertAlmostEqual(view.session_usd, session_usd(_quote(), 361.0))

    def test_an_unmeasured_machine_says_so_and_a_measured_one_gives_its_own_time(self):
        self.assertIn("not measured", "\n".join(self._view().lines))
        measured = "\n".join(self._view(quote=_quote(known=True, ready_s=35.0)).lines)
        self.assertIn("35 s", measured)
        self.assertNotIn("not measured", measured)

    def test_no_qualifying_machine_hides_the_button_with_the_reason(self):
        view = self._view(quote=RuntimeError("0 of 40 offers qualify (40 over price cap)."))
        self.assertFalse(view.can_spend)
        self.assertIn("40 over price cap", "\n".join(view.lines))

    def test_an_unreadable_account_hides_the_button_and_says_why(self):
        view = self._view(credit=RuntimeError("no api key"))
        self.assertFalse(view.can_spend)
        self.assertIn("no api key", "\n".join(view.lines))

    def test_credit_below_the_estimate_hides_the_button(self):
        view = self._view(credit=0.05)
        self.assertFalse(view.can_spend)
        self.assertIn("below this session's estimate", "\n".join(view.lines))

    def test_an_unenabled_pipeline_hides_the_button_even_when_everything_else_is_fine(self):
        view = self._view(enabled=frozenset())
        self.assertFalse(view.can_spend)
        self.assertIn("no measured Vast session", "\n".join(view.lines))
        self.assertIn("$0.90/h", "\n".join(view.lines))   # the tab still shows the offer

    def test_reasons_are_html_escaped(self):
        view = self._view(quote=RuntimeError("bad <b>tag</b> & more"))
        text = "\n".join(view.lines)
        self.assertIn("bad &lt;b&gt;tag&lt;/b&gt; &amp; more", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_vast_panel`
Expected: FAIL — `ImportError: cannot import name 'vast_panel' from 'tgbot'`.

- [ ] **Step 3: Implement**

`scripts/tgbot/vast_panel.py` (new file):

```python
"""What the bot's Vast tab says and whether it may offer a spend button — decided here, with no
Telegram and no network of its own (the quote and the account are passed in).

The tab always renders. Its spend button is hidden, with the concrete reasons written out, when any
of these holds (spec §3.5):
  - a pipeline in the manifest has no measured Vast session yet (VAST_ENABLED_PIPELINES, spec §1:
    one measured session per pipeline family before its button is enabled — as of 2026-09-19 only
    the `motion` stage has ever run on Vast),
  - a stage in the manifest has no model-registry entry (the runtime twin of `make
    check-vast-models`, so a job cannot be sent to a box that never downloaded its models),
  - no Vast machine passes the filters, or the CLI is missing / not logged in,
  - the account credit is below this batch's estimated session cost.

Everything returned as text is HTML for Telegram, with reasons escaped: a reason can carry a
command's stderr.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, Iterable

from batchlib.manifest import Manifest
from batchlib.pipelines import PIPELINES, STAGES
from batchlib.runner import _local_tryon_stage
from batchlib.vast_models import STAGE_MODEL_IDS
from batchlib_ext.vast_quote import BOOT_AFTER_RUNNING_S, VastQuote

from .run import MEASURED_STAGE_SEC


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _unique(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def parse_enabled(raw: str | None) -> frozenset[str]:
    """`VAST_ENABLED_PIPELINES=motion-enhance,character-swap` -> the set of pipeline names."""
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def static_blockers(manifest: Manifest, enabled: frozenset[str]) -> list[str]:
    """Reasons a Vast rental for this manifest is not allowed — no network involved, so the spend
    handler re-runs it at tap time instead of trusting the panel that was drawn minutes earlier."""
    reasons: list[str] = []
    for pipeline in _unique(run.pipeline for run in manifest.runs):
        if pipeline not in enabled:
            reasons.append(
                f"no measured Vast session for {pipeline} yet — add it to "
                "VAST_ENABLED_PIPELINES in .env after the paid check (docs/gpu-pod.md#vast-provider)")
    stages = _unique(stage for run in manifest.runs for stage in PIPELINES.get(run.pipeline, []))
    for stage in stages:
        if stage not in STAGE_MODEL_IDS:
            reasons.append(f"stage {stage} has no Vast model registry entry")
    return reasons


def gpu_seconds(manifest: Manifest) -> float:
    """Seconds of GPU work the manifest will do once the box is up. A try-on that runs locally over
    an API (Phase A) costs no GPU time, so it is left out. Stages with no measurement fall back to
    their timeout ceiling — the same pessimistic fallback tgbot.run.estimate_minutes uses."""
    total = 0.0
    for run in manifest.runs:
        local = _local_tryon_stage(run)
        for stage in PIPELINES.get(run.pipeline, []):
            if stage == local:
                continue
            total += MEASURED_STAGE_SEC.get(stage, STAGES[stage].timeout_min * 60)
    return total


def session_usd(quote: VastQuote, run_s: float) -> float:
    """Estimated total for one session: GPU time from create to teardown, plus bandwidth."""
    billed_s = quote.ready_s + BOOT_AFTER_RUNNING_S + run_s
    return quote.dph * billed_s / 3600.0 + quote.bandwidth_usd


def _credit_check(credit_fn: Callable[[], float],
                  session: float | None) -> tuple[float | None, list[str]]:
    """(credit, reasons). An unreadable account is a reason, never "$0" and never a pass."""
    try:
        credit = credit_fn()
    except RuntimeError as exc:
        return None, [f"could not read your Vast account (is vastai logged in?) — {exc}"]
    if session is not None and credit < session:
        return credit, [f"Vast credit ${credit:.2f} is below this session's estimate "
                        f"${session:.2f}"]
    return credit, []


def spend_blockers(manifest: Manifest, enabled: frozenset[str], *,
                   credit_fn: Callable[[], float], quote: VastQuote | None) -> list[str]:
    """The reasons to refuse a Vast spend at the moment of the tap. A subset of what the panel
    shows: no marketplace search here (a stale button must not stall the bot for a search), so the
    estimate the credit is compared with comes from the last quote, when there is one."""
    reasons = static_blockers(manifest, enabled)
    session = None if quote is None else session_usd(quote, gpu_seconds(manifest))
    return reasons + _credit_check(credit_fn, session)[1]


@dataclass(frozen=True)
class VastView:
    lines: list[str]              # HTML, one per line, for the caller to join
    blockers: list[str]           # plain text; empty means the spend button may be shown
    quote: VastQuote | None
    session_usd: float | None

    @property
    def can_spend(self) -> bool:
        return not self.blockers and self.quote is not None


def build_view(manifest: Manifest, *, gb: float, enabled: frozenset[str],
               quote_fn: Callable[[], VastQuote], credit_fn: Callable[[], float]) -> VastView:
    blockers = static_blockers(manifest, enabled)
    lines = ["<b>Vast.ai</b> — one RTX 5090, rented for this batch only"]
    quote: VastQuote | None = None
    session: float | None = None
    try:
        quote = quote_fn()
    except RuntimeError as exc:
        blockers.append(f"no Vast machine qualifies right now — {exc}")
    if quote is not None:
        run_s = gpu_seconds(manifest)
        session = session_usd(quote, run_s)
        total_min = (quote.ready_s + BOOT_AFTER_RUNNING_S) / 60
        lines += [
            f"{_esc(quote.gpu)} · ${quote.dph:.2f}/h · {_esc(quote.location)}",
            f"Download for this batch ≈ {gb:.0f} GB → bandwidth ${quote.bandwidth_usd:.2f}",
            f"Estimated session ≈ <b>${session:.2f}</b> "
            f"(cold start + {run_s / 60:.0f} min of GPU work, plus bandwidth)",
        ]
        if quote.known:
            lines.append(f"Cold start ≈ {total_min:.0f} min — this machine took "
                         f"{quote.ready_s:.0f} s to start last time")
        else:
            lines.append("Cold start: not measured — typically 5–9 min (2 data points); "
                         f"planned for up to {total_min:.0f} min")
    credit, credit_reasons = _credit_check(credit_fn, session)
    blockers += credit_reasons
    if credit is not None:
        lines.append(f"Vast credit: ${credit:.2f}")
    if blockers:
        lines += ["", "<b>No spend button</b> — why:"] + [f"• {_esc(reason)}" for reason in blockers]
    return VastView(lines=lines, blockers=blockers, quote=quote, session_usd=session)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_vast_panel`
Expected: OK (23 tests).

- [ ] **Step 5: Gates and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/vast_panel.py scripts/tests/test_batch_vast_panel.py
git commit -m "bot: the Vast tab's view-model — blockers, estimate, text"
```

---

### Task 4: The provider travels with the spend button, and Vast spends are gated at the tap

**Files:**
- Modify: `scripts/tgbot/run.py` (`start_drain`, `progress_text`)
- Modify: `scripts/tgbot/bot.py` (imports, a suffix constant and `_split_provider`, the `run:go` / `pa:reuse|rerun` / `pa:spend` callback branches, `_start_progress`, `_billing_kwargs`, `_job_has_local_tryon` → `_draft_manifest`, `_vast_enabled`, `_vast_refusal`, `_do_resume`, `_do_confirm`)
- Test: `scripts/tests/test_batch_tgrun.py`, `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: Task 2 (`account_credit`, `last_quote`), Task 3 (`parse_enabled`, `spend_blockers`), `progress_text`'s existing signature.
- Produces:
  - `run.start_drain(manifest_path, *, dry_run, resume=False, force_local=False, gpu_provider: str | None = None)` — appends `PROVIDER=<gpu_provider>` **before** the `dry_run` gate; `ValueError` for anything but `"runpod"`/`"vast"` (nothing started).
  - `run.progress_text(..., provider: str | None = None, usd_per_hr: float | None = None)` — a Vast lease (or the argument, with no lease) renders `on Vast.ai` and, with a rate, `≈$X so far (quoted $Y/h)`; no rate means time only; RunPod renders exactly as before.
  - `bot._split_provider(rest) -> tuple[str, str | None] | None`; `bot._VAST_SUFFIX = ":vast"`.
  - `bot._draft_manifest(chat_id) -> Manifest | None` (what the drafted jobs *would* write, without touching the live file); `_job_has_local_tryon` now uses it.
  - `bot._do_confirm(..., gpu_provider: str | None = None)` and `bot._do_resume(..., gpu_provider: str | None = None) -> bool` (True only when a drain was started). On `"vast"` both skip the migration guard, and a **new** rental (not a job queued onto a running drain) must pass `_vast_refusal` — in `_do_confirm` *before* the manifest is rewritten.
  - `bot._start_progress(..., gpu_provider=None)` records `gpu_provider` and `usd_per_hr` (from `vast_last_quote()`) in the progress file for Vast only; `bot._billing_kwargs(payload)` reads them back.
- Later tasks rely on: `_draft_manifest`, `_vast_enabled`, `_vast_refusal`, `_billing_kwargs`, `_split_provider`, `_VAST_SUFFIX`, and `_do_resume`'s boolean return.

**Review focus (each is a place a plausible implementation is wrong):**
- `start_drain` must be called with **no** `gpu_provider` kwarg for RunPod — three existing tests use `assert_called_once_with(manifest, dry_run=False, resume=True)`.
- `_PHASE_A_OFFERED` is dropped only when `_do_resume` returns True; a refused Vast spend must leave the rent panel re-renderable.
- The Vast gate in `_do_confirm` runs before `write_manifest` and only when `not running`.

- [ ] **Step 1: Write the failing tests**

`test_batch_bot.py` gains a `_FlowFixture` (TestFlow's `setUp` and helpers moved into a base class with no tests of their own, so `TestVastConfirm` can share them without re-running every `TestFlow` test) and a `_VastBase` fixture that puts a motion-enhance manifest on disk with the Vast network calls patched out. `TestFlow` itself is otherwise untouched.

`scripts/tests/test_batch_tgrun.py`:

```diff
--- a/scripts/tests/test_batch_tgrun.py
+++ b/scripts/tests/test_batch_tgrun.py
@@ -93,6 +93,52 @@ class TestProgressText(unittest.TestCase):
         self.assertIn("running the try-on", text.lower())
 
 
+class TestProgressTextProvider(unittest.TestCase):
+    """A Vast run must never be priced at the flat RunPod $0.99/h (spec §3.5)."""
+
+    def setUp(self):
+        self.manifest = Path(tempfile.mkdtemp()) / "m.yaml"
+        self.manifest.write_text("runs: []", encoding="utf-8")
+        state_path_for(self.manifest).write_text(json.dumps(STATE), encoding="utf-8")
+        self.now = 1_000_000.0
+
+    def _text(self, provider_of_lease=None, **kwargs):
+        lease = None
+        if provider_of_lease is not None:
+            lease = Lease(pod_id="p1", provisioned_at=self.now - 3600.0,
+                          manifest=str(self.manifest), abs_max_min=240,
+                          provider=provider_of_lease)
+        with mock.patch("time.time", return_value=self.now):
+            return progress_text(self.manifest, lease=lease, **kwargs)
+
+    def test_a_runpod_lease_keeps_the_flat_rate_line(self):
+        text = self._text("runpod")
+        self.assertIn("60m00s on the pod · 💸 $0.99 so far", text)
+        self.assertNotIn("Vast", text)
+
+    def test_a_vast_lease_with_a_quoted_rate_shows_an_estimate_at_that_rate(self):
+        text = self._text("vast", usd_per_hr=0.90)
+        self.assertIn("60m00s on Vast.ai · 💸 ≈$0.90 so far (quoted $0.90/h)", text)
+        self.assertNotIn("$0.99", text)
+
+    def test_a_vast_lease_without_a_rate_shows_time_only_not_the_runpod_figure(self):
+        text = self._text("vast")
+        self.assertIn("60m00s on Vast.ai", text)
+        self.assertNotIn("💸", text)
+
+    def test_the_lease_provider_outranks_the_argument(self):
+        text = self._text("vast", provider="runpod", usd_per_hr=0.90)
+        self.assertIn("on Vast.ai", text)
+
+    def test_with_no_lease_the_argument_names_who_we_are_waiting_for(self):
+        empty = Path(tempfile.mkdtemp()) / "none.yaml"
+        empty.write_text("runs: []", encoding="utf-8")
+        state_path_for(empty).write_text(
+            json.dumps({"batch": "2026-09-16-0900", "runs": {}}), encoding="utf-8")
+        self.assertIn("waiting for Vast.ai", progress_text(empty, lease=None, provider="vast"))
+        self.assertIn("waiting for the pod", progress_text(empty, lease=None))
+
+
 class _FakeProc:
     """Stands in for subprocess.Popen: only .poll() is ever read by drain_running."""
     def __init__(self, poll_return):
@@ -209,6 +255,25 @@ class TestStartDrain(unittest.TestCase):
         argv = self._argv_for(dry_run=False)
         self.assertEqual(argv, ["make", "drain", f"FILE={self.manifest}", "CONFIRM=yes"])
 
+    def test_a_vast_provider_is_forwarded_before_the_confirm_gate(self):
+        with mock.patch("tgbot.run.subprocess.Popen") as popen:
+            start_drain(self.manifest, dry_run=False, gpu_provider="vast")
+        self.assertEqual(popen.call_args[0][0],
+                         ["make", "drain", f"FILE={self.manifest}", "PROVIDER=vast",
+                          "CONFIRM=yes"])
+
+    def test_a_vast_dry_run_forwards_the_provider_and_still_never_confirms(self):
+        with mock.patch("tgbot.run.subprocess.Popen") as popen:
+            start_drain(self.manifest, dry_run=True, gpu_provider="vast")
+        self.assertEqual(popen.call_args[0][0],
+                         ["make", "drain", f"FILE={self.manifest}", "PROVIDER=vast"])
+
+    def test_an_unknown_provider_is_refused_before_anything_starts(self):
+        with mock.patch("tgbot.run.subprocess.Popen") as popen:
+            with self.assertRaises(ValueError):
+                start_drain(self.manifest, dry_run=False, gpu_provider="aws")
+        popen.assert_not_called()
+
     def test_output_goes_to_a_log_file_beside_the_manifest_not_a_pipe(self):
         # A drain runs for the lifetime of a rented pod. A Popen pipe nobody
         # reads fills its OS buffer and deadlocks the child mid-batch.
```

`scripts/tests/test_batch_bot.py`:

```diff
--- a/scripts/tests/test_batch_bot.py
+++ b/scripts/tests/test_batch_bot.py
@@ -10,6 +10,7 @@ from batchlib.runner import stage_dest
 from batchlib_ext.gpu_stock import Stock
 from batchlib_ext.handoff import Handoff, handoff_path, mailbox_path, write_handoff
 from batchlib_ext.lease import Lease
+from batchlib_ext.vast_quote import VastQuote
 from batchlib_ext.migrate_lease import MigrateLease, write_migrate_lease
 from batchlib_ext.provision_failure import (ProvisionFailure,
                                             provision_failure_path,
@@ -1348,17 +1349,9 @@ class TestMigrateSyncDetail(unittest.TestCase):
         self.assertIn("(100%)", detail)
 
 
-class TestFlow(unittest.TestCase):
-    """The state machine Task 7 adds: files in, an ambiguous image asked
-    about (never guessed), the manifest shown once every required slot is
-    filled, and /confirm as the only reachable path to start_drain.
-
-    Real files on disk, real `make batch-validate`: only `probe()` is faked
-    (no real ffprobe/media needed) — everything downstream of it, including
-    the manifest text and the free validation gate, runs for real, against
-    the real repo (`bot._REPO_ROOT`), so a passing test here is evidence the
-    generated manifest actually validates, not just that a string was built.
-    """
+class _FlowFixture(unittest.TestCase):
+    """TestFlow's setUp/tearDown and job-building helpers, with no tests of their own, so a
+    class that needs a real drafted job can share them without re-running every TestFlow test."""
 
     def setUp(self):
         self._orig_root = bot.ROOT
@@ -1436,6 +1429,19 @@ class TestFlow(unittest.TestCase):
             bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
             bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
 
+
+class TestFlow(_FlowFixture):
+    """The state machine Task 7 adds: files in, an ambiguous image asked
+    about (never guessed), the manifest shown once every required slot is
+    filled, and /confirm as the only reachable path to start_drain.
+
+    Real files on disk, real `make batch-validate`: only `probe()` is faked
+    (no real ffprobe/media needed) — everything downstream of it, including
+    the manifest text and the free validation gate, runs for real, against
+    the real repo (`bot._REPO_ROOT`), so a passing test here is evidence the
+    generated manifest actually validates, not just that a string was built.
+    """
+
     # ---- the control panel (2026-09-01) ------------------------------------
     #
     # One message per chat, edited in place. Everything below guards a
@@ -7420,3 +7426,290 @@ class TestTryonFailureRetry(unittest.TestCase):
         self._notice()
         self.assertFalse(any("Regenerating" in m and "failed" in m
                              for m in self.tg.messages))
+
+
+
+# ---- Vast as a second GPU provider (spec §3.5, Plan 4) --------------------------------------------
+
+_MOTION_MANIFEST = ("runs:\n  - id: runA\n    pipeline: motion-enhance\n"
+                    "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n")
+
+
+def _vast_quote(**over):
+    base = dict(offer_id=4401, machine_id=55, dph=0.90, gpu="RTX 5090", location="Bulgaria, BG",
+                ready_s=556.0, known=False, bandwidth_usd=0.10, gb=51.8, qualifying=4,
+                fetched_at=0.0)
+    base.update(over)
+    return VastQuote(**base)
+
+
+class _VastBase(unittest.TestCase):
+    """A chat whose motion-enhance manifest is on disk and whose rent panel has been offered
+    (the state tick_phase_a leaves), with the Vast network calls patched out."""
+
+    ENV = ("GPU=NVIDIA GeForce RTX 5090\nPOD_VOLUME_ID=vol-1\n"
+           "VAST_ENABLED_PIPELINES=motion-enhance\n")
+
+    def setUp(self):
+        self._orig_root = bot.ROOT
+        self.root = Path(tempfile.mkdtemp())
+        (self.root / "batch").mkdir()
+        (self.root / "out").mkdir()
+        bot.ROOT = self.root
+        (self.root / ".env").write_text(self.ENV, encoding="utf-8")
+        reset_bot_state()
+        run_mod._PHASE_A.clear()
+        run_mod._PHASE_A_RC.clear()
+        self.manifest = bot._job_manifest_path(ME)
+        self.manifest.write_text(_MOTION_MANIFEST, encoding="utf-8")
+        state_path_for(self.manifest).write_text(
+            json.dumps({"batch": "2026-09-19-1200", "runs": {}}), encoding="utf-8")
+        self.tg = FakeTg()
+        self.token = bot._run_token(ME)
+        self.assertNotEqual(self.token, "0", "the manifest must exist or every token is 0")
+        # os.environ outranks .env for the enabled list; a developer's shell must not leak in.
+        env = mock.patch.dict("os.environ", {}, clear=False)
+        env.start()
+        self.addCleanup(env.stop)
+        import os
+        os.environ.pop("VAST_ENABLED_PIPELINES", None)
+        for name, value in (("lease_for", None),):
+            patcher = mock.patch(f"tgbot.bot.{name}", return_value=value)
+            patcher.start()
+            self.addCleanup(patcher.stop)
+        self.credit = mock.patch("tgbot.bot.vast_credit", return_value=25.0)
+        self.last = mock.patch("tgbot.bot.vast_last_quote", return_value=_vast_quote())
+        self.credit_mock = self.credit.start()
+        self.last.start()
+        for patcher in (self.credit, self.last):
+            self.addCleanup(patcher.stop)
+
+    def tearDown(self):
+        bot.ROOT = self._orig_root
+        reset_bot_state()
+
+    def _latch(self):
+        bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
+
+
+class TestVastCallbackSuffix(_VastBase):
+    def test_split_provider(self):
+        self.assertEqual(bot._split_provider("123"), ("123", None))
+        self.assertEqual(bot._split_provider("123:vast"), ("123", "vast"))
+        self.assertIsNone(bot._split_provider("123:aws"))
+        self.assertIsNone(bot._split_provider("123:vast:x"))
+
+    def test_the_longest_vast_spend_button_fits_the_64_byte_cap(self):
+        token = "9" * 19       # mtime_ns is 19 digits today
+        for prefix in (bot._CB_RUN_GO, bot._CB_PHASE_A_SPEND, bot._CB_PHASE_A_REUSE,
+                       bot._CB_PHASE_A_RERUN):
+            self.assertLessEqual(len(f"{prefix}{token}{bot._VAST_SUFFIX}".encode()), 64, prefix)
+
+    def _press(self, data):
+        bot.handle(self.tg, cb_from(ME, data), allowed_user_id=ME)
+
+    def test_run_go_without_a_suffix_is_a_default_provider_spend(self):
+        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
+             mock.patch("tgbot.bot._do_confirm") as confirm:
+            self._press(bot._CB_RUN_GO + self.token)
+        confirm.assert_called_once_with(self.tg, ME, dry_run=False, gpu_provider=None)
+
+    def test_run_go_with_the_vast_suffix_spends_on_vast(self):
+        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
+             mock.patch("tgbot.bot._do_confirm") as confirm:
+            self._press(bot._CB_RUN_GO + self.token + bot._VAST_SUFFIX)
+        confirm.assert_called_once_with(self.tg, ME, dry_run=False, gpu_provider="vast")
+
+    def test_an_unknown_suffix_or_a_stale_token_spends_nothing(self):
+        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
+             mock.patch("tgbot.bot._do_confirm") as confirm:
+            self._press(bot._CB_RUN_GO + self.token + ":aws")
+            self._press(bot._CB_RUN_GO + "1" + bot._VAST_SUFFIX)
+        confirm.assert_not_called()
+        self.assertEqual(sum("the job changed" in m for m in self.tg.messages), 2)
+
+    def test_the_reuse_and_rerun_choosers_carry_the_provider_through(self):
+        with mock.patch("tgbot.bot._do_confirm") as confirm:
+            self._press(bot._CB_PHASE_A_REUSE + self.token + bot._VAST_SUFFIX)
+            self._press(bot._CB_PHASE_A_RERUN + self.token)
+        self.assertEqual(confirm.call_args_list, [
+            mock.call(self.tg, ME, dry_run=False, phase_a_choice="reuse", gpu_provider="vast"),
+            mock.call(self.tg, ME, dry_run=False, phase_a_choice="rerun", gpu_provider=None)])
+
+    def test_the_post_phase_a_spend_resumes_on_the_chosen_provider(self):
+        self._latch()
+        with mock.patch("tgbot.bot._do_resume", return_value=True) as resume:
+            self._press(bot._CB_PHASE_A_SPEND + self.token + bot._VAST_SUFFIX)
+        resume.assert_called_once_with(self.tg, ME, self.manifest, dry_run=False,
+                                       gpu_provider="vast")
+        self.assertNotIn(ME, bot._PHASE_A_OFFERED)
+
+    def test_a_refused_spend_leaves_the_rent_panel_re_renderable(self):
+        self._latch()
+        with mock.patch("tgbot.bot._do_resume", return_value=False):
+            self._press(bot._CB_PHASE_A_SPEND + self.token + bot._VAST_SUFFIX)
+        self.assertIn(ME, bot._PHASE_A_OFFERED)
+
+
+class TestVastResume(_VastBase):
+    def _resume(self, **kwargs):
+        with mock.patch("tgbot.bot.busy", return_value=False), \
+             mock.patch("tgbot.bot.migration_running", return_value=kwargs.pop("migrating", False)), \
+             mock.patch("tgbot.bot._start_progress") as progress, \
+             mock.patch("tgbot.bot.start_drain") as start_drain:
+            started = bot._do_resume(self.tg, ME, self.manifest, dry_run=False, **kwargs)
+        return started, start_drain, progress
+
+    def test_a_vast_resume_starts_the_drain_on_vast_and_records_it_in_progress(self):
+        started, start_drain, progress = self._resume(gpu_provider="vast")
+        self.assertTrue(started)
+        start_drain.assert_called_once_with(self.manifest, dry_run=False, resume=True,
+                                            gpu_provider="vast")
+        self.assertEqual(progress.call_args.kwargs["gpu_provider"], "vast")
+        self.assertIn("on Vast.ai", self.tg.messages[-1])
+
+    def test_a_migration_in_flight_does_not_block_a_vast_resume(self):
+        started, start_drain, _ = self._resume(gpu_provider="vast", migrating=True)
+        self.assertTrue(started)
+        start_drain.assert_called_once()
+
+    def test_a_migration_in_flight_still_blocks_the_default_resume(self):
+        started, start_drain, _ = self._resume(migrating=True)
+        self.assertFalse(started)
+        start_drain.assert_not_called()
+
+    def test_the_default_resume_is_unchanged_and_says_nothing_about_vast(self):
+        started, start_drain, progress = self._resume()
+        self.assertTrue(started)
+        start_drain.assert_called_once_with(self.manifest, dry_run=False, resume=True)
+        self.assertNotIn("gpu_provider", progress.call_args.kwargs)
+        self.assertNotIn("Vast", self.tg.messages[-1])
+
+    def test_a_vast_resume_for_an_unmeasured_pipeline_is_refused_and_spends_nothing(self):
+        (self.root / ".env").write_text("GPU=x\n", encoding="utf-8")
+        started, start_drain, progress = self._resume(gpu_provider="vast")
+        self.assertFalse(started)
+        start_drain.assert_not_called()
+        progress.assert_not_called()
+        self.assertIn("Not renting on Vast", self.tg.messages[-1])
+        self.assertIn("nothing was spent", self.tg.messages[-1])
+
+    def test_a_vast_resume_with_an_unreadable_account_is_refused(self):
+        self.credit_mock.side_effect = RuntimeError("no api key")
+        started, start_drain, _ = self._resume(gpu_provider="vast")
+        self.assertFalse(started)
+        start_drain.assert_not_called()
+        self.assertIn("no api key", self.tg.messages[-1])
+
+    def test_a_vast_resume_with_too_little_credit_is_refused(self):
+        self.credit_mock.return_value = 0.05
+        started, start_drain, _ = self._resume(gpu_provider="vast")
+        self.assertFalse(started)
+        start_drain.assert_not_called()
+        self.assertIn("below this session's estimate", self.tg.messages[-1])
+
+
+class TestVastConfirm(_FlowFixture):
+    """_do_confirm, the fresh-spend gate, with the provider the Vast tab minted."""
+
+    def setUp(self):
+        super().setUp()
+        (self.root / ".env").write_text(
+            "VAST_ENABLED_PIPELINES=tryon-motion-enhance\n", encoding="utf-8")
+        import os
+        env = mock.patch.dict("os.environ", {}, clear=False)
+        env.start()
+        self.addCleanup(env.stop)
+        os.environ.pop("VAST_ENABLED_PIPELINES", None)
+        self.credit = mock.patch("tgbot.bot.vast_credit", return_value=25.0)
+        self.last = mock.patch("tgbot.bot.vast_last_quote", return_value=_vast_quote())
+        self.credit_mock = self.credit.start()
+        self.last.start()
+        self.addCleanup(self.credit.stop)
+        self.addCleanup(self.last.stop)
+
+    def _confirm(self, **kwargs):
+        with mock.patch("tgbot.bot.start_drain") as start_drain, \
+             mock.patch("tgbot.bot._start_progress") as progress, \
+             mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
+             mock.patch("tgbot.bot.drain_running",
+                        return_value=kwargs.pop("running", False)), \
+             mock.patch("tgbot.bot.migration_running",
+                        return_value=kwargs.pop("migrating", False)):
+            self._fill_required_slots()
+            bot._do_confirm(self.tg, ME, dry_run=False, **kwargs)
+        return start_drain, progress
+
+    def test_a_vast_confirm_starts_the_drain_on_vast_and_names_the_rate(self):
+        start_drain, progress = self._confirm(gpu_provider="vast")
+        start_drain.assert_called_once()
+        self.assertEqual(start_drain.call_args.kwargs["gpu_provider"], "vast")
+        self.assertEqual(progress.call_args.kwargs["gpu_provider"], "vast")
+        started = next(m for m in self.tg.messages if "Started" in m)
+        self.assertIn("Vast.ai at about $0.90/hour", started)
+        self.assertNotIn("$0.99", started)
+
+    def test_a_migration_in_flight_does_not_block_a_vast_confirm(self):
+        start_drain, _ = self._confirm(gpu_provider="vast", migrating=True)
+        start_drain.assert_called_once()
+
+    def test_the_default_confirm_is_unchanged(self):
+        start_drain, progress = self._confirm()
+        start_drain.assert_called_once()
+        self.assertNotIn("gpu_provider", start_drain.call_args.kwargs)
+        self.assertNotIn("gpu_provider", progress.call_args.kwargs)
+        self.assertIn("on one pod at $0.99/hour",
+                      next(m for m in self.tg.messages if "Started" in m))
+
+    def test_a_refused_vast_confirm_spends_nothing_and_keeps_the_draft_and_its_token(self):
+        (self.root / ".env").write_text("GPU=x\n", encoding="utf-8")
+        with mock.patch("tgbot.bot._job_has_local_tryon", return_value=False), \
+             mock.patch("tgbot.bot.drain_running", return_value=False), \
+             mock.patch("tgbot.bot.migration_running", return_value=False), \
+             mock.patch("tgbot.bot.start_drain") as start_drain, \
+             mock.patch("tgbot.bot._start_progress"):
+            self._fill_required_slots()
+            before = bot._run_token(ME)
+            bot._do_confirm(self.tg, ME, dry_run=False, gpu_provider="vast")
+            after = bot._run_token(ME)
+        start_drain.assert_not_called()
+        self.assertNotEqual(before, "0")
+        self.assertEqual(before, after, "the refusal rewrote the manifest and killed the panel")
+        self.assertIn(ME, bot._STATE)          # the draft survives, so the user can go back
+        self.assertIn("Not renting on Vast", self.tg.messages[-1])
+
+    def test_a_job_queued_onto_a_running_drain_is_not_a_new_vast_rental(self):
+        (self.root / ".env").write_text("GPU=x\n", encoding="utf-8")   # nothing enabled
+        start_drain, _ = self._confirm(gpu_provider="vast", running=True)
+        start_drain.assert_not_called()          # queued, not launched
+        self.assertTrue(any("Queued" in m for m in self.tg.messages))
+        self.assertFalse(any("Not renting on Vast" in m for m in self.tg.messages))
+
+
+class TestVastProgressBilling(_VastBase):
+    def test_billing_kwargs_reads_provider_and_rate_and_ignores_junk(self):
+        self.assertEqual(bot._billing_kwargs({"gpu_provider": "vast", "usd_per_hr": 0.9}),
+                         {"provider": "vast", "usd_per_hr": 0.9})
+        self.assertEqual(bot._billing_kwargs({}), {})
+        self.assertEqual(bot._billing_kwargs(None), {})
+        self.assertEqual(bot._billing_kwargs({"usd_per_hr": True}), {})
+
+    def test_a_vast_progress_file_records_the_provider_and_the_quoted_rate(self):
+        bot._start_progress(self.tg, ME, self.manifest, ["motion"], gpu_provider="vast")
+        payload = json.loads(bot._progress_path(ME).read_text(encoding="utf-8"))
+        self.assertEqual((payload["gpu_provider"], payload["usd_per_hr"]), ("vast", 0.90))
+
+    def test_a_runpod_progress_file_is_unchanged(self):
+        bot._start_progress(self.tg, ME, self.manifest, ["motion"])
+        payload = json.loads(bot._progress_path(ME).read_text(encoding="utf-8"))
+        self.assertNotIn("gpu_provider", payload)
+        self.assertNotIn("usd_per_hr", payload)
+
+    def test_the_message_is_priced_at_the_quoted_rate_not_the_runpod_one(self):
+        lease = Lease(pod_id="i1", provisioned_at=time.time() - 3600, manifest=str(self.manifest),
+                      abs_max_min=240, provider="vast")
+        with mock.patch("tgbot.bot.lease_for", return_value=lease):
+            bot._start_progress(self.tg, ME, self.manifest, ["motion"], gpu_provider="vast")
+        self.assertIn("on Vast.ai", self.tg.messages[-1])
+        self.assertIn("quoted $0.90/h", self.tg.messages[-1])
+        self.assertNotIn("$0.99", self.tg.messages[-1])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun scripts.tests.test_batch_bot.TestVastCallbackSuffix scripts.tests.test_batch_bot.TestVastResume scripts.tests.test_batch_bot.TestVastConfirm scripts.tests.test_batch_bot.TestVastProgressBilling`
Expected: FAIL / ERROR — `start_drain() got an unexpected keyword argument 'gpu_provider'`, `AttributeError: module 'tgbot.bot' has no attribute '_split_provider'`, and the progress-text tests.

- [ ] **Step 3: Implement**

`scripts/tgbot/run.py`:

```diff
--- a/scripts/tgbot/run.py
+++ b/scripts/tgbot/run.py
@@ -157,7 +157,9 @@ def _elapsed(lease) -> str:
 
 def progress_text(manifest_path: Path, *, lease,
                   stages: list[str] | None = None,
-                  phase: str | None = None) -> str:
+                  phase: str | None = None,
+                  provider: str | None = None,
+                  usd_per_hr: float | None = None) -> str:
     """Render one progress message from the journal alone. Returns HTML.
 
     A bar of `done/len(planned)` cells is discrete because the journal is
@@ -191,10 +193,17 @@ def progress_text(manifest_path: Path, *, lease,
     from the absence of a lease is how the message came to say "waiting for
     the pod" about a step that deliberately runs before any pod exists.
 
+    `provider` and `usd_per_hr` exist for Vast, whose price is not the flat $0.99 a RunPod 5090
+    costs: the lease's own provider wins when there is one, and the dollar figure is the rate
+    QUOTED when the user tapped spend, printed as an estimate ("≈") because the offer actually
+    rented can differ from the quote. With no rate known a Vast message shows time only — never
+    the RunPod figure. RunPod (or nothing) renders exactly as it always has.
+
     HTML (2026-08-31) because this is re-rendered into the same message every
     poll — the caller must send it with parse_mode="HTML", and every
     interpolated value here is escaped for that reason.
     """
+    on_vast = ((lease.provider if lease is not None else provider) or "runpod") == "vast"
     state = load_state(state_path_for(manifest_path))
     batch = state.get("batch") or "(not started yet)"
     try:
@@ -227,7 +236,8 @@ def progress_text(manifest_path: Path, *, lease,
             # phase where the only real question is whether anything is
             # happening at all, which `elapsed` answers and the journal cannot.
             tail = f" ({elapsed})" if elapsed else ""
-            lines.append(f"{_ICON_EYES_CE} waiting for the pod — "
+            where = "Vast.ai" if on_vast else "the pod"
+            lines.append(f"{_ICON_EYES_CE} waiting for {where} — "
                          f"nothing recorded yet{tail}")
     for run_id in sorted(runs):
         run = runs[run_id]
@@ -266,13 +276,19 @@ def progress_text(manifest_path: Path, *, lease,
         mins = (time.time() - lease.provisioned_at) / 60
         # Elapsed, not a prediction: the pod bills from provisioned_at whether
         # or not a stage is moving, so this is the number that costs money.
-        lines.append(f"\n⏱ {elapsed} on the pod · 💸 ${mins / 60 * 0.99:.2f} so far")
+        if on_vast:
+            cost = (f" · 💸 ≈${mins / 60 * usd_per_hr:.2f} so far (quoted ${usd_per_hr:.2f}/h)"
+                    if usd_per_hr else "")
+            lines.append(f"\n⏱ {elapsed} on Vast.ai{cost}")
+        else:
+            lines.append(f"\n⏱ {elapsed} on the pod · 💸 ${mins / 60 * 0.99:.2f} so far")
 
     return "\n".join(lines)
 
 
 def start_drain(manifest_path: Path, *, dry_run: bool,
-                resume: bool = False, force_local: bool = False) -> subprocess.Popen:
+                resume: bool = False, force_local: bool = False,
+                gpu_provider: str | None = None) -> subprocess.Popen:
     """Launch `make drain FILE=...`, appending CONFIRM=yes only when dry_run is False.
 
     This is the ONLY line in this module (in this repo) that may write the
@@ -293,12 +309,22 @@ def start_drain(manifest_path: Path, *, dry_run: bool,
     dry_run gate deliberately — the gate must stay the last thing appended so
     that "CONFIRM=yes appears iff dry_run is False" remains readable as a
     single trailing condition.
+
+    `gpu_provider` forwards PROVIDER=vast|runpod (Makefile:119, drain.py's --provider) so the cloud
+    follows THIS run and never .env: the bot must not rewrite .env for a per-batch choice, because a
+    bot that dies mid-run would leave the other provider's value behind. None appends nothing, and
+    the drain then uses .env's GPU_PROVIDER exactly as before. It sits before the dry_run gate for
+    the same reason force_local does.
     """
+    if gpu_provider is not None and gpu_provider not in ("runpod", "vast"):
+        raise ValueError(f"unknown gpu_provider {gpu_provider!r}")
     argv = ["make", "drain", f"FILE={manifest_path}"]
     if resume:
         argv.append("RESUME=1")
     if force_local:
         argv.append("FORCE_LOCAL=1")
+    if gpu_provider is not None:
+        argv.append(f"PROVIDER={gpu_provider}")
     if not dry_run:
         argv.append("CONFIRM=yes")
 
```

`scripts/tgbot/bot.py`:

```diff
--- a/scripts/tgbot/bot.py
+++ b/scripts/tgbot/bot.py
@@ -30,7 +30,7 @@ from pathlib import Path
 sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
 from batchlib.config import env_get, env_set
 from batchlib.local_tryon import is_local_provider, qwen_max_configured
-from batchlib.manifest import (ManifestError, load_manifest, load_state,
+from batchlib.manifest import (Manifest, ManifestError, load_manifest, load_state,
                                save_state, state_path_for)
 from batchlib.pipelines import (PIPELINES, effective_stage_params,
                                optional_roles, required_roles)
@@ -56,6 +56,7 @@ from tgbot.ingest import (Probe, describe, probe, quality_warning,
 from tgbot.job import (DEFAULT_PROVIDER, Job, _tryon_stage, _unique_ids, missing_slots,
                        render_manifest, run_id_for, slot_for, write_manifest)
 from tgbot.preview import sheet, slot_preview
+from tgbot.vast_panel import parse_enabled, spend_blockers
 # `run as run_mod` alongside the from-imports, for exactly one caller:
 # _busy_reason, which has to resolve drain_running through tgbot.run's OWN
 # globals so it cannot disagree with the busy() that just returned True. See
@@ -68,6 +69,8 @@ from tgbot.run import (LEASE_PATH, _RUNNING, busy, drain_running,
                        stop_phase_a, summary_text)
 from batchlib_ext.gpu_stock import stock_at, stock_at_cached, volume_datacenter
 from batchlib_ext.runpod_account import account_balance
+from batchlib_ext.vast_account import account_credit as vast_credit
+from batchlib_ext.vast_quote import last_quote as vast_last_quote
 from batchlib_ext.handoff import handoff_path, mailbox_path, read_handoff
 from batchlib_ext.lease import clear_lease, read_lease
 from batchlib_ext.migrate_lease import read_migrate_lease
@@ -1440,6 +1443,11 @@ _CB_RUN_MIGRATE_MENU = "run:mgmenu"
 # message), and only Back should ever pass its own message_id in to be
 # edited — passing the panel's id there would overwrite the manifest.
 _CB_RUN_BACK = "run:back"
+# The provider a spend button was minted for rides IN its callback data, after the run token:
+# "run:go:<token>:vast". Not in .env (a bot that dies mid-run would leave it behind) and not in
+# bot state (lost on restart, and shared between two panels). RunPod has no suffix, so every button
+# already sitting in a chat keeps meaning exactly what it meant.
+_VAST_SUFFIX = ":vast"
 # + "m"/"s"/"g" — which screen to redraw (main / switch menu / migrate menu).
 _CB_RUN_REFRESH = "run:refresh:"
 # The /gpu report's own Refresh button — separate from _CB_RUN_REFRESH
@@ -1558,6 +1566,19 @@ _CB_PROVIDER = "prov:"
 _CB_PROVIDER_ASK = "prov-ask"
 
 
+def _split_provider(rest: str) -> tuple[str, str | None] | None:
+    """`<token>` -> (token, None); `<token>:vast` -> (token, "vast"); anything else -> None.
+
+    None means a suffix this version does not know — a button from a newer or older bot — and the
+    callers treat it like a stale token: refuse, spend nothing."""
+    token, sep, suffix = rest.partition(":")
+    if not sep:
+        return token, None
+    if suffix == _VAST_SUFFIX[1:]:
+        return token, "vast"
+    return None
+
+
 def _run_token(chat_id: int) -> str:
     """A stamp identifying the exact manifest a Run button was offered for.
 
@@ -1709,7 +1730,8 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
                 _remove_gpu_sub(tg, chat_id, msg_id, short, dc)
 
         elif data.startswith(_CB_RUN_GO):
-            if data[len(_CB_RUN_GO):] != _run_token(chat_id):
+            parsed = _split_provider(data[len(_CB_RUN_GO):])
+            if parsed is None or parsed[0] != _run_token(chat_id):
                 tg.send_message(chat_id,
                                 "the job changed since that button was sent, so "
                                 "nothing ran. Check the manifest above and "
@@ -1722,22 +1744,25 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
                 # produces. Nothing has been spent yet at this point.
                 _do_phase_a(tg, chat_id, dry_run=dry_run)
             else:
-                _do_confirm(tg, chat_id, dry_run=dry_run)
+                _do_confirm(tg, chat_id, dry_run=dry_run, gpu_provider=parsed[1])
 
         elif data.startswith(_CB_PHASE_A_REUSE) or data.startswith(_CB_PHASE_A_RERUN):
             reuse = data.startswith(_CB_PHASE_A_REUSE)
             prefix = _CB_PHASE_A_REUSE if reuse else _CB_PHASE_A_RERUN
-            if data[len(prefix):] != _run_token(chat_id):
+            parsed = _split_provider(data[len(prefix):])
+            if parsed is None or parsed[0] != _run_token(chat_id):
                 tg.send_message(chat_id,
                                 "the job changed since that button was sent, so "
                                 "nothing ran. Check the manifest above and "
                                 "confirm again.")
             else:
                 _do_confirm(tg, chat_id, dry_run=dry_run,
-                            phase_a_choice="reuse" if reuse else "rerun")
+                            phase_a_choice="reuse" if reuse else "rerun",
+                            gpu_provider=parsed[1])
 
         elif data.startswith(_CB_PHASE_A_SPEND):
-            if data[len(_CB_PHASE_A_SPEND):] != _run_token(chat_id):
+            parsed = _split_provider(data[len(_CB_PHASE_A_SPEND):])
+            if parsed is None or parsed[0] != _run_token(chat_id):
                 tg.send_message(chat_id,
                                 "the job changed since that button was sent, so "
                                 "nothing ran. Check the manifest above and "
@@ -1747,9 +1772,14 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
                 # comment for why _STATE cannot be relied on here. The path
                 # comes from chat_id, the way _CB_RUN_GO's branch reaches
                 # _do_confirm; the token proved which manifest was reviewed.
-                _PHASE_A_OFFERED.pop(chat_id, None)
-                _do_resume(tg, chat_id, _job_manifest_path(chat_id),
-                           dry_run=dry_run)
+                #
+                # The latch is dropped only once the rental really started: a refusal
+                # (a Vast spend the gate turned down, a migration in flight) leaves the
+                # panel it came from re-renderable instead of falling back to the
+                # "run try-on first" screen.
+                if _do_resume(tg, chat_id, _job_manifest_path(chat_id),
+                              dry_run=dry_run, gpu_provider=parsed[1]):
+                    _PHASE_A_OFFERED.pop(chat_id, None)
 
         elif data.startswith(_CB_TRYON_REGEN):
             index, _, token = data[len(_CB_TRYON_REGEN):].partition(":")
@@ -2921,7 +2951,8 @@ def _progress_path(chat_id: int) -> Path:
 def _start_progress(tg: Tg, chat_id: int, manifest_path: Path,
                     stages: list[str], *, phase: str | None = None,
                     sent_tryon: list[str] | None = None,
-                    regen: dict | None = None) -> None:
+                    regen: dict | None = None,
+                    gpu_provider: str | None = None) -> None:
     """Send the first progress message and record it for later edits.
 
     `phase` names which tick owns the resulting message: "local" while Phase A
@@ -2936,15 +2967,40 @@ def _start_progress(tg: Tg, chat_id: int, manifest_path: Path,
     records a single-image regeneration — both only from _regen_tryon: a
     regeneration re-runs Phase A on the same manifest, and without the seed
     every OTHER image would be sent a second time.
+
+    `gpu_provider` is "vast" for a Vast rental and records, in the same file, the provider and the
+    hourly rate QUOTED on the panel the user tapped (vast_last_quote — an estimate: the offer
+    actually rented can differ). tick_progress and /status read both back, so every later render
+    prices the run at its own rate instead of RunPod's flat $0.99. Absent for RunPod, so that
+    file and its message are exactly what they were.
     """
+    billing: dict = {}
+    if gpu_provider == "vast":
+        quote = vast_last_quote()
+        billing = {"gpu_provider": "vast",
+                   **({"usd_per_hr": quote.dph} if quote is not None else {})}
     text = progress_text(manifest_path, lease=lease_for(manifest_path),
-                         stages=stages, phase=phase)
+                         stages=stages, phase=phase, **_billing_kwargs(billing))
     message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
     _progress_path(chat_id).write_text(json.dumps({
         "manifest": str(manifest_path), "message_id": message_id,
         "stages": stages, "sent_tryon": sorted(sent_tryon or []),
         **({"phase": phase} if phase else {}),
-        **({"regen": regen} if regen else {})}, indent=2), encoding="utf-8")
+        **({"regen": regen} if regen else {}),
+        **billing}, indent=2), encoding="utf-8")
+
+
+def _billing_kwargs(payload: dict | None) -> dict:
+    """progress_text's provider/usd_per_hr, read back from a progress file's payload. Empty for a
+    RunPod run (and for a file written before Vast existed), which keeps its old rendering."""
+    payload = payload or {}
+    out: dict = {}
+    if payload.get("gpu_provider"):
+        out["provider"] = str(payload["gpu_provider"])
+    rate = payload.get("usd_per_hr")
+    if isinstance(rate, (int, float)) and not isinstance(rate, bool):
+        out["usd_per_hr"] = float(rate)
+    return out
 
 
 def _deliver_tryon_previews(tg: Tg, chat_id: int, manifest_path: Path,
@@ -5253,7 +5309,8 @@ def _again(tg: Tg, chat_id: int) -> None:
                          note=f"reusing the last batch — {len(jobs)} job(s)")
 
 
-def _do_resume(tg: Tg, chat_id: int, manifest_path: Path, *, dry_run: bool) -> None:
+def _do_resume(tg: Tg, chat_id: int, manifest_path: Path, *, dry_run: bool,
+               gpu_provider: str | None = None) -> bool:
     """Continue a batch whose pod rental already failed once — reached only
     from the recovery buttons _deliver_provision_failure offers, after the
     user picked a different GPU (_CB_RECOVER_SWITCH) or asked to retry the
@@ -5272,12 +5329,18 @@ def _do_resume(tg: Tg, chat_id: int, manifest_path: Path, *, dry_run: bool) -> N
     reached for a manifest that already has a recorded batch id (proof it
     already passed the money gate once), so it is not a second way to spend
     money the user has not already agreed to.
+
+    `gpu_provider` is "vast" when the user picked Vast on the panel, None for everything else
+    (drain then uses .env's provider, as it always did). A Vast rental has no Network Volume, so
+    the migration guard does not apply to it, and it must pass _vast_refusal — the same checks the
+    panel's hidden spend button shows — before anything is started. Returns True only when a drain
+    was started, so a caller can tell a refusal from a launch.
     """
-    if migration_running():
+    if gpu_provider != "vast" and migration_running():
         tg.send_message(chat_id, "a volume migration is in progress for this "
                                  "pod's datacenter — wait for it to finish "
                                  "before retrying")
-        return
+        return False
     # busy(), not drain_running(): this is about to hand the manifest to
     # drain.py, which READS it — the predicate run.busy's own docstring names
     # for exactly that. A live Phase A holds no lease and registers no _RUNNING
@@ -5289,28 +5352,38 @@ def _do_resume(tg: Tg, chat_id: int, manifest_path: Path, *, dry_run: bool) -> N
     # the thing running was a drain.
     if busy(manifest_path):
         tg.send_message(chat_id, "already running — nothing to resume")
-        return
+        return False
     state = load_state(state_path_for(manifest_path))
     if not state.get("batch"):
         tg.send_message(chat_id, f"nothing to resume for {_esc(manifest_path.stem)} "
                                  "— that batch never started")
-        return
+        return False
     try:
         manifest = load_manifest(manifest_path)
     except ManifestError as exc:
         tg.send_message(chat_id, f"could not resume — {_esc(str(exc))}")
-        return
+        return False
+    if gpu_provider == "vast":
+        refusal = _vast_refusal(manifest)
+        if refusal is not None:
+            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
+            return False
     clear_provision_failure(provision_failure_path(manifest_path))
     stages: list[str] = []
     for run in manifest.runs:
         for stage in PIPELINES[run.pipeline]:
             if stage not in stages:
                 stages.append(stage)
-    tg.send_message(chat_id, f"{ICON_ROCKET_CE} <b>Retrying</b> — renting a pod "
+    where = " on Vast.ai" if gpu_provider == "vast" else ""
+    tg.send_message(chat_id, f"{ICON_ROCKET_CE} <b>Retrying</b> — renting a pod{where} "
                              f"again for {_esc(manifest_path.stem)}.",
                     parse_mode=PARSE_HTML)
-    start_drain(manifest_path, dry_run=dry_run, resume=True)
-    _start_progress(tg, chat_id, manifest_path, stages)
+    # Only Vast passes a provider: a RunPod call stays exactly `start_drain(path, dry_run=..,
+    # resume=True)`, the call the RunPod-unchanged tests pin.
+    provider_kwargs = {} if gpu_provider is None else {"gpu_provider": gpu_provider}
+    start_drain(manifest_path, dry_run=dry_run, resume=True, **provider_kwargs)
+    _start_progress(tg, chat_id, manifest_path, stages, **provider_kwargs)
+    return True
 
 
 def _manifest_write_ok(chat_id: int) -> bool:
@@ -5378,17 +5451,51 @@ def _job_has_local_tryon(chat_id: int) -> bool:
     either, and _do_confirm reports that properly. Silently doing nothing would
     be worse than falling through.
     """
+    manifest = _draft_manifest(chat_id)
+    return manifest is not None and has_local_tryon(manifest)
+
+
+def _draft_manifest(chat_id: int) -> Manifest | None:
+    """The manifest this chat's drafted jobs WOULD write, loaded back from a throwaway file — or
+    None when there is no job or it will not render. The one place that answers "what is about to
+    be submitted" without touching the live manifest file, whose mtime is the run token that every
+    button in the chat is checked against."""
     queued = _jobs_for(chat_id)
     if not queued:
-        return False
+        return None
     try:
         text = render_manifest(queued, now=time.strftime("%Y-%m-%d %H:%M:%S"))
         with tempfile.TemporaryDirectory() as d:
             probe = Path(d) / "probe.yaml"
             probe.write_text(text, encoding="utf-8")
-            return has_local_tryon(load_manifest(probe))
+            return load_manifest(probe)
     except (ManifestError, OSError):
-        return False
+        return None
+
+
+def _vast_enabled() -> frozenset[str]:
+    """Pipelines allowed to rent on Vast: VAST_ENABLED_PIPELINES from the environment, then .env.
+    Empty by default — spec §1 wants one measured Vast session per pipeline family before its
+    spend button exists, and as of 2026-09-19 none has been run through the bot."""
+    return parse_enabled(os.environ.get("VAST_ENABLED_PIPELINES")
+                         or env_get(ROOT / ".env", "VAST_ENABLED_PIPELINES"))
+
+
+def _vast_refusal(manifest: Manifest | None) -> str | None:
+    """Why a Vast spend must NOT start now, as HTML for the chat; None means it may.
+
+    The money-gate twin of the panel's hidden spend button. Telegram keeps buttons tappable
+    forever, so a button drawn when the account was funded and the pipeline enabled can be tapped
+    after either changed — the panel alone is not a gate. No marketplace search here: the credit is
+    compared with the estimate from the last quote, so a stale tap cannot stall the bot."""
+    if manifest is None:
+        return f"{ICON_WARN} <b>Not renting on Vast</b> — no manifest to check. Nothing was spent."
+    reasons = spend_blockers(manifest, _vast_enabled(), credit_fn=vast_credit,
+                             quote=vast_last_quote())
+    if not reasons:
+        return None
+    return (f"{ICON_WARN} <b>Not renting on Vast</b> — nothing was spent:\n"
+            + "\n".join(f"• {_esc(reason)}" for reason in reasons))
 
 
 def _journal_is_resumable(manifest_path: Path) -> bool:
@@ -5533,7 +5640,8 @@ def _do_phase_a(tg: Tg, chat_id: int, *, dry_run: bool) -> None:
 
 
 def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
-                phase_a_choice: str | None = None) -> None:
+                phase_a_choice: str | None = None,
+                gpu_provider: str | None = None) -> None:
     """THE money gate for a FRESH spend decision. The only OTHER function
     that may call start_drain is _do_resume, which continues a manifest
     already confirmed here once — see its own docstring for why that is not
@@ -5557,11 +5665,17 @@ def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
 
     `grep -rn "start_drain" scripts/tgbot/bot.py` must show exactly two call
     sites: this one, and _do_resume's.
+
+    `gpu_provider` is "vast" when the panel's Vast tab minted the button, None otherwise. Vast has
+    no Network Volume, so the migration guard below does not apply to it; and a NEW Vast rental
+    (not a job queued onto a pod already running) must pass _vast_refusal, evaluated BEFORE the
+    manifest is rewritten — that rewrite changes its mtime, which is the run token, and would kill
+    the very panel the user is about to tap again after a refusal.
     """
     # Checked before anything else, including completeness — a migration mid-
     # copy is moving the Network Volume this pod would mount, so renting must
     # not be allowed to race it regardless of how complete the job is.
-    if migration_running():
+    if gpu_provider != "vast" and migration_running():
         tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                  "datacenter — wait for it to finish before renting")
         return
@@ -5665,6 +5779,11 @@ def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
     # here is what it used to be, and re-deriving a path a guard already acted
     # on is how the two can quietly stop being the same file.
     running = drain_running(live_path)
+    if gpu_provider == "vast" and not running:
+        refusal = _vast_refusal(_draft_manifest(chat_id))
+        if refusal is not None:
+            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
+            return
     manifest_path = mailbox_path(live_path) if running else live_path
     # Re-written on the FIRST entry, even when `validated` was cached True: the
     # cache only remembers that the JOB CONTENT was valid, not which file it
@@ -5744,9 +5863,12 @@ def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
         # same duplication already flagged at _preserved_tryon — plus a new
         # user-visible message that would need its own test. Revisit together
         # with that finding, not separately.
+        # Only Vast passes a provider — see _do_resume's identical line.
+        provider_kwargs = {} if gpu_provider is None else {"gpu_provider": gpu_provider}
         start_drain(manifest_path, dry_run=dry_run,
                     resume=phase_a_choice is not None,
-                    force_local=phase_a_choice == "rerun")
+                    force_local=phase_a_choice == "rerun",
+                    **provider_kwargs)
     # Clear in-memory state so the next file starts a fresh job rather
     # than mutating one already handed to a running drain. The manifest
     # itself, and the drain's own journal, stay on disk regardless.
@@ -5827,12 +5949,19 @@ def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
                 "rental, and no second Gemini call.",
                 parse_mode=PARSE_HTML)
     else:
+        if gpu_provider == "vast":
+            quote = vast_last_quote()
+            rate = f"about ${quote.dph:.2f}/hour (quoted)" if quote is not None else "Vast's hourly rate"
+            where = f"on Vast.ai at {rate}"
+        else:
+            where = "on one pod at $0.99/hour"
         tg.send_message(chat_id,
-                        f"{ICON_ROCKET_CE} <b>Started.</b> {submitted_count} job(s) on one pod at "
-                        "$0.99/hour.\nI will keep the message below updated and "
+                        f"{ICON_ROCKET_CE} <b>Started.</b> {submitted_count} job(s) {where}."
+                        "\nI will keep the message below updated and "
                         "send the results when it finishes — no need to ask.",
                         parse_mode=PARSE_HTML)
-        _start_progress(tg, chat_id, manifest_path, stages)
+        _start_progress(tg, chat_id, manifest_path, stages,
+                        **({} if gpu_provider is None else {"gpu_provider": gpu_provider}))
     return
 
 
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_tgrun scripts.tests.test_batch_bot`
Expected: OK — 652 bot tests (628 existing + 24 new). The bot suite takes about 100 s.

- [ ] **Step 5: Gates and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/run.py scripts/tgbot/bot.py scripts/tests/test_batch_tgrun.py scripts/tests/test_batch_bot.py
git commit -m "bot: carry the GPU provider in spend callbacks and gate Vast spends at the tap"
```

---

### Task 5: The picker — a provider row and the Vast tab

**Files:**
- Modify: `scripts/drain.py` (a `vast_download_gb` helper; `provision()` uses it)
- Modify: `scripts/tgbot/bot.py` (imports, tab constants, `_provider_row`, `_panel_manifest`, `_offer_vast_panel`, `gpu_provider` on `_offer_run_confirm` / `_offer_run_for_chat` / `_offer_rent_after_phase_a`, the `run:vast` / `run:rp` callback, `run:refresh:v`)
- Test: `scripts/tests/test_batch_drain.py`, `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: Task 3's `build_view`, Task 2's `fetch_quote`, Task 4's `_vast_enabled`, `_draft_manifest`, `_VAST_SUFFIX`.
- Produces:
  - `drain.vast_download_gb(manifest) -> float` = `total_download_gb(manifest) + VAST_IMAGE_GB` — the *same* number `provision()` exports as `VAST_GB`, so the rent command and the quote cannot disagree.
  - `bot._CB_RUN_RUNPOD = "run:rp"`, `bot._CB_RUN_VAST = "run:vast"`; `bot._provider_row(active) -> list`.
  - `_offer_run_confirm(..., gpu_provider=None)`: `"vast"` draws the Vast tab, `None` the RunPod screen with the provider row on top. The Phase A try-on confirm has **no** provider row (that tap rents nothing).
  - The Vast spend button is `spend_cb + ":vast"` (`run:go:<token>:vast` before Phase A's rent panel, `pa:spend:<token>:vast` after it); Refresh on the tab is `run:refresh:v` and forces a new quote.

**Review focus:**
- The RunPod tab is its own callback, **not** `run:back`: Back refuses when no draft job is in memory, which the rent panel drawn after Phase A may not have after a bot restart, while `run:vast` accepts the panel's run token. Two tabs with different guards would behave differently after a restart.
- The Vast tab must not call `stock_at` / `stock_at_cached` (no datacenter or stock lines) — a test asserts neither is called.
- The quote is sized with `drain.vast_download_gb`, and Refresh passes `force=True`.

- [ ] **Step 1: Write the failing tests**

`TestVastPicker` patches `vast_fetch_quote` in its own `setUp` (the function does not exist in `bot` until this task).

`scripts/tests/test_batch_drain.py`:

```diff
--- a/scripts/tests/test_batch_drain.py
+++ b/scripts/tests/test_batch_drain.py
@@ -80,6 +80,16 @@ def _manifest_with_a_motion_run() -> Manifest:
     ])
 
 
+class TestVastDownloadGb(unittest.TestCase):
+    def test_is_the_manifests_models_plus_the_image_floor(self):
+        m = _manifest_with_a_motion_run()
+        self.assertAlmostEqual(drain.vast_download_gb(m),
+                               drain.total_download_gb(m) + drain.VAST_IMAGE_GB)
+
+    def test_an_empty_manifest_is_just_the_image(self):
+        self.assertAlmostEqual(drain.vast_download_gb(_empty_manifest()), drain.VAST_IMAGE_GB)
+
+
 class TestProvision(unittest.TestCase):
     def _manifest_path(self) -> Path:
         return Path(tempfile.mkdtemp()) / "tg-1.yaml"
```

`scripts/tests/test_batch_bot.py`:

```diff
--- a/scripts/tests/test_batch_bot.py
+++ b/scripts/tests/test_batch_bot.py
@@ -7491,6 +7491,13 @@ class _VastBase(unittest.TestCase):
     def _latch(self):
         bot._PHASE_A_OFFERED[ME] = bot._run_token(ME)
 
+    def _last_buttons(self):
+        return [d for row in (self.tg.screen_buttons[-1] or []) for _, d, *_ in row]
+
+    def _spend_buttons(self):
+        return [d for d in self._last_buttons()
+                if d.startswith((bot._CB_RUN_GO, bot._CB_PHASE_A_SPEND))]
+
 
 class TestVastCallbackSuffix(_VastBase):
     def test_split_provider(self):
@@ -7713,3 +7720,121 @@ class TestVastProgressBilling(_VastBase):
         self.assertIn("on Vast.ai", self.tg.messages[-1])
         self.assertIn("quoted $0.90/h", self.tg.messages[-1])
         self.assertNotIn("$0.99", self.tg.messages[-1])
+
+
+class TestVastPicker(_VastBase):
+    def setUp(self):
+        super().setUp()
+        patcher = mock.patch("tgbot.bot.vast_fetch_quote", return_value=_vast_quote())
+        self.fetch_mock = patcher.start()
+        self.addCleanup(patcher.stop)
+
+    def _stock(self):
+        return {"NVIDIA GeForce RTX 5090": [
+            Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
+                  datacenter_id="EU-RO-1", stock_status="available", price_per_hr=0.99)]}
+
+    def _runpod_panel(self):
+        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
+             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
+            bot._offer_run_confirm(self.tg, ME)
+
+    def _vast_panel(self, **kwargs):
+        with mock.patch("tgbot.bot.stock_at_cached") as stock, \
+             mock.patch("tgbot.bot.stock_at") as live:
+            bot._offer_run_confirm(self.tg, ME, gpu_provider="vast", **kwargs)
+        stock.assert_not_called()        # no datacenter or stock lines on the Vast tab
+        live.assert_not_called()
+
+    def test_the_runpod_screen_gains_a_provider_row_and_keeps_its_own_buttons(self):
+        self._runpod_panel()
+        first = self.tg.screen_buttons[-1][0]
+        self.assertEqual([(label, data) for label, data, *_ in first],
+                         [("RunPod ✓", bot._CB_RUN_RUNPOD), ("Vast", bot._CB_RUN_VAST)])
+        self.assertTrue(any(d.startswith(bot._CB_RUN_GO) and not d.endswith(":vast")
+                            for d in self._last_buttons()))
+
+    def test_the_fail_open_runpod_screen_has_the_row_too(self):
+        with mock.patch("tgbot.bot.volume_datacenter", return_value=None):
+            bot._offer_run_confirm(self.tg, ME)
+        self.assertIn(bot._CB_RUN_VAST, self._last_buttons())
+
+    def test_the_phase_a_try_on_confirm_has_no_provider_row(self):
+        bot._offer_run_confirm(self.tg, ME, phase_a=True)
+        self.assertNotIn(bot._CB_RUN_VAST, self._last_buttons())
+
+    def test_the_vast_tab_shows_the_offer_and_a_vast_spend_button(self):
+        self._latch()
+        self._vast_panel()
+        text = self.tg.screen[-1]
+        for expected in ("$0.90/h", "Bulgaria, BG", "Vast credit: $25.00", "Estimated session"):
+            self.assertIn(expected, text)
+        self.assertNotIn("Switch GPU", text)
+        self.assertNotIn("Other regions", text)
+        buttons = self._last_buttons()
+        self.assertIn(bot._CB_RUN_GO + self.token + ":vast", buttons)
+        self.assertIn(bot._CB_RUN_REFRESH + "v", buttons)
+        first = self.tg.screen_buttons[-1][0]
+        self.assertEqual([label for label, *_ in first], ["RunPod", "Vast ✓"])
+
+    def test_the_quote_is_sized_to_this_manifests_download(self):
+        self._latch()
+        self._vast_panel()
+        import drain
+        expected = drain.vast_download_gb(load_manifest(self.manifest))
+        self.assertAlmostEqual(self.fetch_mock.call_args.args[0], expected)
+        self.assertIs(self.fetch_mock.call_args.kwargs["force"], False)
+
+    def test_the_post_phase_a_vast_spend_button_resumes_and_carries_the_provider(self):
+        self._latch()
+        bot._offer_rent_after_phase_a(self.tg, ME, gpu_provider="vast")
+        self.assertIn(bot._CB_PHASE_A_SPEND + self.token + ":vast", self._last_buttons())
+        self.assertIn("Try-on finished", self.tg.screen[-1])
+
+    def test_a_pipeline_nobody_has_measured_gets_no_spend_button_and_says_why(self):
+        (self.root / ".env").write_text("GPU=x\n", encoding="utf-8")
+        self._latch()
+        self._vast_panel()
+        self.assertEqual(self._spend_buttons(), [])
+        self.assertIn("no measured Vast session for motion-enhance", self.tg.screen[-1])
+        self.assertIn("$0.90/h", self.tg.screen[-1])       # the tab still renders the offer
+
+    def test_no_qualifying_machine_gets_no_spend_button(self):
+        self.fetch_mock.side_effect = RuntimeError("0 of 40 offers qualify (40 over price cap).")
+        self._latch()
+        self._vast_panel()
+        self.assertEqual(self._spend_buttons(), [])
+        self.assertIn("40 over price cap", self.tg.screen[-1])
+
+    def test_an_unreadable_account_gets_no_spend_button(self):
+        self.credit_mock.side_effect = RuntimeError("no api key")
+        self._latch()
+        self._vast_panel()
+        self.assertEqual(self._spend_buttons(), [])
+        self.assertIn("no api key", self.tg.screen[-1])
+
+    def test_the_vast_tab_button_draws_the_panel_and_refresh_forces_a_new_quote(self):
+        self._latch()
+        with mock.patch("tgbot.bot.stock_at_cached"):
+            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_VAST), allowed_user_id=ME)
+            self.assertIn("Vast.ai", self.tg.screen[-1])
+            self.assertIs(self.fetch_mock.call_args.kwargs["force"], False)
+            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_REFRESH + "v"), allowed_user_id=ME)
+        self.assertIs(self.fetch_mock.call_args.kwargs["force"], True)
+        self.assertIn(bot._CB_PHASE_A_SPEND + self.token + ":vast", self._last_buttons())
+
+    def test_the_vast_tab_with_no_job_at_all_says_so_instead_of_drawing(self):
+        bot._PHASE_A_OFFERED.clear()
+        bot.handle(self.tg, cb_from(ME, bot._CB_RUN_VAST), allowed_user_id=ME)
+        self.assertIn("no complete job yet", self.tg.messages[-1])
+        self.fetch_mock.assert_not_called()
+
+    def test_the_runpod_tab_goes_back_to_the_stock_screen(self):
+        self._latch()
+        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
+             mock.patch("tgbot.bot.stock_at_cached", return_value=self._stock()):
+            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_VAST), allowed_user_id=ME)
+            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_RUNPOD), allowed_user_id=ME)
+        self.assertIn("Try-on finished", self.tg.screen[-1])
+        self.assertIn("RTX 5090", self.tg.screen[-1])
+        self.assertIn(bot._CB_PHASE_A_SPEND + self.token, self._last_buttons())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_drain scripts.tests.test_batch_bot.TestVastPicker`
Expected: FAIL / ERROR — `module 'drain' has no attribute 'vast_download_gb'`; `TestVastPicker.setUp` cannot patch `tgbot.bot.vast_fetch_quote`.

- [ ] **Step 3: Implement**

`scripts/drain.py`:

```diff
--- a/scripts/drain.py
+++ b/scripts/drain.py
@@ -101,6 +101,12 @@ _STOCK_OUT_MARKER = "không tự xoay sang card khác"
 VAST_IMAGE_GB = 17.38
 
 
+def vast_download_gb(manifest: Manifest) -> float:
+    """GB a Vast rental for this manifest downloads: its models plus the base-image pull. One
+    definition for the rent command below and for the bot's price quote, so the two cannot drift."""
+    return total_download_gb(manifest) + VAST_IMAGE_GB
+
+
 def provision(*, ceiling_min: int, manifest_path: Path, manifest: Manifest) -> str:
     """Rent a pod and return its instance id. Does NOT wait or bootstrap.
 
@@ -137,8 +143,7 @@ def provision(*, ceiling_min: int, manifest_path: Path, manifest: Manifest) -> s
     no_volume = "POD_VOLUME= " if chosen and chosen != "runpod" else ""
     vast_gb = ""
     if chosen and chosen != "runpod":
-        gb = total_download_gb(manifest) + VAST_IMAGE_GB
-        vast_gb = f"VAST_GB={gb:.1f} "
+        vast_gb = f"VAST_GB={vast_download_gb(manifest):.1f} "
     result = subprocess.run(
         f"{no_volume}{vast_gb}POD_MAX_HOURS={hours} CONFIRM=yes bash scripts/pod-provision.sh",
         shell=True, cwd=ROOT, stderr=subprocess.PIPE, text=True)
```

`scripts/tgbot/bot.py`:

```diff
--- a/scripts/tgbot/bot.py
+++ b/scripts/tgbot/bot.py
@@ -41,7 +41,7 @@ from batchlib.runner import (_local_tryon_stage, has_local_tryon,
 # is already on sys.path (the insert above), so this is the plan's own "import
 # it, do not reimplement" for failed_job_ids rather than re-deriving "did this
 # run fail" from state.json by hand a second time.
-from drain import failed_job_ids
+from drain import failed_job_ids, vast_download_gb
 from batch_run import EXIT_NEEDS_POD
 import batch_clean
 # Absolute, NOT `from .tgclient import ...`. This file runs as
@@ -56,7 +56,7 @@ from tgbot.ingest import (Probe, describe, probe, quality_warning,
 from tgbot.job import (DEFAULT_PROVIDER, Job, _tryon_stage, _unique_ids, missing_slots,
                        render_manifest, run_id_for, slot_for, write_manifest)
 from tgbot.preview import sheet, slot_preview
-from tgbot.vast_panel import parse_enabled, spend_blockers
+from tgbot.vast_panel import build_view as vast_build_view, parse_enabled, spend_blockers
 # `run as run_mod` alongside the from-imports, for exactly one caller:
 # _busy_reason, which has to resolve drain_running through tgbot.run's OWN
 # globals so it cannot disagree with the busy() that just returned True. See
@@ -70,7 +70,7 @@ from tgbot.run import (LEASE_PATH, _RUNNING, busy, drain_running,
 from batchlib_ext.gpu_stock import stock_at, stock_at_cached, volume_datacenter
 from batchlib_ext.runpod_account import account_balance
 from batchlib_ext.vast_account import account_credit as vast_credit
-from batchlib_ext.vast_quote import last_quote as vast_last_quote
+from batchlib_ext.vast_quote import fetch_quote as vast_fetch_quote, last_quote as vast_last_quote
 from batchlib_ext.handoff import handoff_path, mailbox_path, read_handoff
 from batchlib_ext.lease import clear_lease, read_lease
 from batchlib_ext.migrate_lease import read_migrate_lease
@@ -1443,6 +1443,13 @@ _CB_RUN_MIGRATE_MENU = "run:mgmenu"
 # message), and only Back should ever pass its own message_id in to be
 # edited — passing the panel's id there would overwrite the manifest.
 _CB_RUN_BACK = "run:back"
+# The two tabs of the Choose GPU screen (spec §3.5). Exact matches, no trailing colon, and neither
+# is a prefix of another key here. Not _CB_RUN_BACK for RunPod: Back insists on a drafted job in
+# memory, which the rent panel drawn after Phase A may not have any more (a bot restart clears it),
+# while these two accept the panel's own run token instead — so the tabs behave the same as each
+# other wherever they are shown.
+_CB_RUN_RUNPOD = "run:rp"
+_CB_RUN_VAST = "run:vast"
 # The provider a spend button was minted for rides IN its callback data, after the run token:
 # "run:go:<token>:vast". Not in .env (a bot that dies mid-run would leave it behind) and not in
 # bot state (lost on restart, and shared between two panels). RunPod has no suffix, so every button
@@ -1674,6 +1681,22 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
             msg_id = (query.get("message") or {}).get("message_id")
             _offer_run_migrate_menu(tg, chat_id, message_id=msg_id)
 
+        elif data == _CB_RUN_VAST or data == _CB_RUN_RUNPOD:
+            msg_id = (query.get("message") or {}).get("message_id")
+            job = _STATE.get(chat_id)
+            # Back's guard, plus one more way in: the panel drawn after Phase A may have no draft
+            # job left in memory, only the manifest its run token names.
+            if (job is None or missing_slots(job)) \
+                    and _PHASE_A_OFFERED.get(chat_id) != _run_token(chat_id):
+                tg.send_message(chat_id, "no complete job yet — send the "
+                                         "required files first")
+            else:
+                on_vast = data == _CB_RUN_VAST
+                if on_vast and msg_id is not None:
+                    tg.edit_message(chat_id, msg_id, "🔄 Asking Vast for offers…")
+                _offer_run_for_chat(tg, chat_id, message_id=msg_id,
+                                    gpu_provider="vast" if on_vast else None)
+
         elif data == _CB_RUN_BACK:
             msg_id = (query.get("message") or {}).get("message_id")
             job = _STATE.get(chat_id)
@@ -1696,6 +1719,9 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
                 _offer_run_switch_menu(tg, chat_id, message_id=msg_id, force=True)
             elif view == "g":
                 _offer_run_migrate_menu(tg, chat_id, message_id=msg_id, force=True)
+            elif view == "v":
+                _offer_run_for_chat(tg, chat_id, message_id=msg_id, force=True,
+                                    gpu_provider="vast")
             else:
                 _offer_run_for_chat(tg, chat_id, message_id=msg_id, force=True)
 
@@ -4740,9 +4766,59 @@ def _edit_or_send(tg: Tg, chat_id: int, message_id: int | None, text: str,
     tg.send_message(chat_id, text, buttons=buttons, parse_mode=parse_mode)
 
 
+def _provider_row(active: str) -> list:
+    """The first row of the Choose GPU screen: [RunPod] [Vast], the active one ticked. Tapping the
+    ticked one just redraws the screen it is already on."""
+    return [("RunPod ✓" if active == "runpod" else "RunPod", _CB_RUN_RUNPOD),
+            ("Vast ✓" if active == "vast" else "Vast", _CB_RUN_VAST)]
+
+
+def _panel_manifest(chat_id: int) -> Manifest | None:
+    """The manifest the Choose GPU screen is deciding a rental for: the one on disk once Phase A
+    has offered its rent panel (its run token matches), otherwise the draft about to be written.
+    Neither read touches the live file's mtime."""
+    if _PHASE_A_OFFERED.get(chat_id) == _run_token(chat_id):
+        try:
+            return load_manifest(_job_manifest_path(chat_id))
+        except (ManifestError, OSError):
+            return None
+    return _draft_manifest(chat_id)
+
+
+def _offer_vast_panel(tg: Tg, chat_id: int, *, message_id: int | None, force: bool,
+                      spend_cb: str, heading: str | None) -> None:
+    """The Vast tab (spec §3.5): the offer, its price, this batch's bandwidth and session cost,
+    the cold start, and a spend button only when nothing blocks it. No datacenter or stock lines
+    and no Switch GPU / Other regions: v1 searches only the configured 5090 and a Vast rental has
+    no volume to migrate. The marketplace search is a blocking call of a few seconds (4 s measured
+    2026-09-19, bounded at 120 s), so the caller shows an interstitial first."""
+    manifest = _panel_manifest(chat_id)
+    if manifest is None:
+        _edit_or_send(tg, chat_id, message_id,
+                      "no complete job yet — send the required files first",
+                      [_provider_row("vast")])
+        return
+    gb = vast_download_gb(manifest)
+    view = vast_build_view(
+        manifest, gb=gb, enabled=_vast_enabled(),
+        quote_fn=lambda: vast_fetch_quote(gb, force=force, repo_root=_REPO_ROOT),
+        credit_fn=vast_credit)
+    lines = [heading or f"{ICON_NVIDIA_CE} <b>Choose GPU</b>", "", *view.lines]
+    buttons = [_provider_row("vast"),
+               [("Refresh", _CB_RUN_REFRESH + "v", _ce_id(ICON_REFRESH_CE))]]
+    if view.can_spend:
+        buttons.append([(f"Yes, spend ≈${view.session_usd:.2f} on Vast", spend_cb + _VAST_SUFFIX,
+                         _ce_id(ICON_ROCKET_CE)),
+                        ("Cancel", _CB_RUN_NO)])
+    else:
+        buttons.append([("Cancel", _CB_RUN_NO)])
+    _edit_or_send(tg, chat_id, message_id, "\n".join(lines), buttons, parse_mode=PARSE_HTML)
+
+
 def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
                        force: bool = False, spend_cb: str | None = None,
-                       heading: str | None = None, phase_a: bool = False) -> None:
+                       heading: str | None = None, phase_a: bool = False,
+                       gpu_provider: str | None = None) -> None:
     """The step between [Run] and spending money: always lists every known
     GPU's live stock/price at the home datacenter and lets [Confirm] switch
     to any of them before renting (2026-09-02, widened from "only offer a
@@ -4789,6 +4865,11 @@ def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
     message: the very first [Run] tap on the job panel, and tick_phase_a's
     post-Phase-A panel. `force` bypasses stock_at_cached's 60s TTL for a
     real live recheck, used only by the 🔄 Refresh button.
+
+    `gpu_provider` "vast" draws the Vast tab (_offer_vast_panel) instead of RunPod's stock screen;
+    None is the RunPod screen, which gains only the [RunPod] [Vast] row on top. The Phase A
+    try-on confirm below has no provider row on purpose: that tap rents nothing, and the GPU is
+    chosen on the panel drawn after the try-on finishes.
     """
     configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
     volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
@@ -4810,6 +4891,10 @@ def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
                _ce_id(ICON_ROCKET_CE)),
               ("Cancel", _CB_RUN_NO)]])
         return
+    if gpu_provider == "vast":
+        _offer_vast_panel(tg, chat_id, message_id=message_id, force=force,
+                          spend_cb=spend_cb, heading=heading)
+        return
     wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
     try:
         stock = ((stock_at(wanted) if force else stock_at_cached(wanted))
@@ -4826,7 +4911,8 @@ def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
             tg, chat_id, message_id,
             f"This rents a GPU pod at ${price:.2f}/hour and starts the job.\n"
             "Confirm?",
-            [[("Refresh", _CB_RUN_REFRESH + "m", _ce_id(ICON_REFRESH_CE))],
+            [_provider_row("runpod"),
+             [("Refresh", _CB_RUN_REFRESH + "m", _ce_id(ICON_REFRESH_CE))],
              [(spend_label, spend_cb, _ce_id(ICON_ROCKET_CE)),
               ("Cancel", _CB_RUN_NO)]])
         return
@@ -4875,7 +4961,7 @@ def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
     # category button opens a submenu (_offer_run_switch_menu /
     # _offer_run_migrate_menu) with the same options one full-width button
     # per row, so nothing there is packed tight enough to truncate.
-    buttons = []
+    buttons = [_provider_row("runpod")]
     if switch_buttons:
         buttons.append([("Switch GPU type ▸", _CB_RUN_SWITCH_MENU, _ce_id(ICON_NVIDIA_CE))])
     if migrate_buttons:
@@ -4894,7 +4980,7 @@ def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
 
 
 def _offer_run_for_chat(tg: Tg, chat_id: int, *, message_id: int | None = None,
-                        force: bool = False) -> None:
+                        force: bool = False, gpu_provider: str | None = None) -> None:
     """[Run]'s first screen, choosing between the two flows by manifest content.
 
     A chat whose draft has local try-on gets the two-step flow (Phase A, then
@@ -4924,24 +5010,30 @@ def _offer_run_for_chat(tg: Tg, chat_id: int, *, message_id: int | None = None,
     came through here, saw a try-on draft, and dropped the user back on the
     "run try-on first" screen — a button that re-ran Phase A instead of
     renting.
+
+    `gpu_provider` is threaded to whichever screen is drawn, so a Refresh or the Vast tab stays on
+    the provider the user chose.
     """
     if _PHASE_A_OFFERED.get(chat_id) == _run_token(chat_id):
-        _offer_rent_after_phase_a(tg, chat_id, message_id=message_id, force=force)
+        _offer_rent_after_phase_a(tg, chat_id, message_id=message_id, force=force,
+                                  gpu_provider=gpu_provider)
         return
     _offer_run_confirm(tg, chat_id, message_id=message_id, force=force,
-                       phase_a=_job_has_local_tryon(chat_id))
+                       phase_a=_job_has_local_tryon(chat_id), gpu_provider=gpu_provider)
 
 
 def _offer_rent_after_phase_a(tg: Tg, chat_id: int, *,
                               message_id: int | None = None,
-                              force: bool = False) -> None:
+                              force: bool = False,
+                              gpu_provider: str | None = None) -> None:
     """The Choose GPU panel for a batch whose try-on is already on disk: its
     spend button resumes into a rental (_CB_PHASE_A_SPEND) rather than
     starting Phase A again."""
     _offer_run_confirm(
         tg, chat_id, message_id=message_id, force=force,
         spend_cb=f"{_CB_PHASE_A_SPEND}{_run_token(chat_id)}",
-        heading=f"{ICON_NVIDIA_CE} <b>Try-on finished</b> — now rent a GPU?")
+        heading=f"{ICON_NVIDIA_CE} <b>Try-on finished</b> — now rent a GPU?",
+        gpu_provider=gpu_provider)
 
 
 def _offer_run_switch_menu(tg: Tg, chat_id: int, *, message_id: int | None = None,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_drain scripts.tests.test_batch_bot`
Expected: OK — 664 bot tests (652 + 12 new).

- [ ] **Step 5: Gates and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/drain.py scripts/tgbot/bot.py scripts/tests/test_batch_drain.py scripts/tests/test_batch_bot.py
git commit -m "bot: a RunPod/Vast row on the Choose GPU screen and the Vast tab behind it"
```

---

### Task 6: The stock-out card's Vast button, and no RunPod prices on a Vast pod

**Files:**
- Modify: `scripts/tgbot/bot.py` (`_CB_RECOVER_VAST`, `_deliver_provision_failure`, the `rec:vast:` callback, `_ask_kill`, and the three `progress_text` render sites — two in `tick_progress`, one in `/status`)
- Test: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: Task 4's `_billing_kwargs`, Task 5's `_offer_run_for_chat(gpu_provider="vast")`.
- Produces: `bot._CB_RECOVER_VAST = "rec:vast:"` (+ manifest stem). Tapping it for **this chat's** batch sets the same `_PHASE_A_OFFERED` latch `tick_phase_a` sets and opens the Vast rent panel; for any other stem it opens nothing. It never spends — the spend button on that panel carries its own run token. `/kill`'s confirm line says `already N min on the pod` with **no** dollar figure for a non-RunPod lease.

**Known cosmetic limitation (deliberate, not a bug to fix here):** the panel opened from the stock-out card keeps the heading "Try-on finished — now rent a GPU?" even for a batch with no local try-on. Making the heading depend on the failure would need a second source of truth for what the panel is about; the buttons and the numbers beneath it are correct.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_batch_bot.py`:

```diff
--- a/scripts/tests/test_batch_bot.py
+++ b/scripts/tests/test_batch_bot.py
@@ -7838,3 +7838,90 @@ class TestVastPicker(_VastBase):
         self.assertIn("Try-on finished", self.tg.screen[-1])
         self.assertIn("RTX 5090", self.tg.screen[-1])
         self.assertIn(bot._CB_PHASE_A_SPEND + self.token, self._last_buttons())
+
+
+class TestVastProgressRender(_VastBase):
+    """tick_progress and /status price a running Vast pod from its progress file."""
+
+    def test_a_later_render_reads_the_rate_back_from_the_file(self):
+        bot._start_progress(self.tg, ME, self.manifest, ["motion"], gpu_provider="vast")
+        payload = json.loads(bot._progress_path(ME).read_text(encoding="utf-8"))
+        lease = Lease(pod_id="i1", provisioned_at=time.time() - 3600, manifest=str(self.manifest),
+                      abs_max_min=240, provider="vast")
+        with mock.patch("tgbot.bot.lease_for", return_value=lease), \
+             mock.patch("tgbot.bot.drain_running", return_value=True):
+            bot.tick_progress(self.tg, ME)
+        self.assertIn("quoted $0.90/h", self.tg.screen[-1])
+
+
+class TestVastRecoveryAndKill(unittest.TestCase):
+    """The stock-out card's Rent on Vast button, and /kill's wording for a Vast pod."""
+
+    def setUp(self):
+        self._orig_root = bot.ROOT
+        self.root = Path(tempfile.mkdtemp())
+        (self.root / "batch").mkdir()
+        (self.root / "out").mkdir()
+        bot.ROOT = self.root
+        (self.root / ".env").write_text(
+            "GPU=NVIDIA GeForce RTX 5090\nPOD_VOLUME_ID=vol-1\n", encoding="utf-8")
+        reset_bot_state()
+        self.manifest = self.root / "batch" / "tg-1.yaml"
+        self.manifest.write_text(_MOTION_MANIFEST, encoding="utf-8")
+        state_path_for(self.manifest).write_text(
+            json.dumps({"batch": "2026-09-14-1421", "runs": {}}), encoding="utf-8")
+        write_provision_failure(provision_failure_path(self.manifest), ProvisionFailure(
+            gpu="NVIDIA GeForce RTX 5090", datacenter="EU-RO-1",
+            stock_out=True, detail="hết máy ..."))
+        self.tg = FakeTg()
+
+    def tearDown(self):
+        bot.ROOT = self._orig_root
+        reset_bot_state()
+
+    def test_the_stock_out_card_offers_rent_on_vast(self):
+        with mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
+             mock.patch("tgbot.bot.stock_at_cached", return_value={}):
+            bot.deliver_result(self.tg, ME, self.manifest)
+        flat = [data for row in self.tg.buttons[-1] for _, data, *_ in row]
+        self.assertIn(f"{bot._CB_RECOVER_VAST}tg-1", flat)
+
+    def test_a_non_stock_out_failure_card_has_no_vast_button(self):
+        write_provision_failure(provision_failure_path(self.manifest), ProvisionFailure(
+            gpu="NVIDIA GeForce RTX 5090", datacenter="EU-RO-1", stock_out=False,
+            detail="some API error"))
+        bot.deliver_result(self.tg, ME, self.manifest)
+        flat = [data for rows in self.tg.buttons if rows for row in rows for _, data, *_ in row]
+        self.assertFalse([d for d in flat if d.startswith(bot._CB_RECOVER_VAST)])
+
+    def test_tapping_it_for_this_chats_batch_latches_the_rent_panel_on_vast(self):
+        stem = bot._job_manifest_path(ME).stem
+        with mock.patch("tgbot.bot._offer_run_for_chat") as offer:
+            bot.handle(self.tg, cb_from(ME, f"{bot._CB_RECOVER_VAST}{stem}"),
+                       allowed_user_id=ME)
+        self.assertIn(ME, bot._PHASE_A_OFFERED)
+        self.assertEqual(offer.call_args.kwargs["gpu_provider"], "vast")
+
+    def test_tapping_it_for_another_batchs_card_opens_nothing(self):
+        with mock.patch("tgbot.bot._offer_run_for_chat") as offer:
+            bot.handle(self.tg, cb_from(ME, f"{bot._CB_RECOVER_VAST}tg-999"),
+                       allowed_user_id=ME)
+            bot.handle(self.tg, cb_from(ME, bot._CB_RECOVER_VAST), allowed_user_id=ME)
+        offer.assert_not_called()
+        self.assertNotIn(ME, bot._PHASE_A_OFFERED)
+
+    def _kill_ask(self, provider):
+        lease = Lease(pod_id="p1", provisioned_at=time.time() - 3600, manifest=str(self.manifest),
+                      abs_max_min=240, provider=provider)
+        with mock.patch("tgbot.bot.drain_running", return_value=True), \
+             mock.patch("tgbot.bot.phase_a_running", return_value=False), \
+             mock.patch("tgbot.bot.lease_for", return_value=lease):
+            bot._ask_kill(self.tg, ME)
+        return self.tg.messages[-1]
+
+    def test_kill_quotes_the_runpod_rate_only_for_a_runpod_pod(self):
+        self.assertIn("($0.99) on the pod", self._kill_ask("runpod"))
+        vast = self._kill_ask("vast")
+        self.assertIn("already 60 min on the pod", vast)
+        self.assertNotIn("$0.99", vast)
+
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest scripts.tests.test_batch_bot.TestVastProgressRender scripts.tests.test_batch_bot.TestVastRecoveryAndKill`
Expected: FAIL — no `rec:vast:` button on the card, `_CB_RECOVER_VAST` missing, `tick_progress` ignoring the recorded rate, `/kill` quoting `$0.99` for a Vast lease.

- [ ] **Step 3: Implement**

`scripts/tgbot/bot.py`:

```diff
--- a/scripts/tgbot/bot.py
+++ b/scripts/tgbot/bot.py
@@ -717,6 +717,8 @@ def _deliver_provision_failure(tg: Tg, chat_id: int, manifest_path: Path,
                         f"${e.price_per_hr:.2f}/h",
                         f"{_CB_RECOVER_MIGRATE}{e.datacenter_id}:{stem}")])
 
+    buttons.append([("☁ Rent on Vast instead", f"{_CB_RECOVER_VAST}{stem}")])
+
     short = _GPU_SHORT.get(failure.gpu)
     if short is not None:
         # ICON_CRITICAL_CE, same as _offer_gpu_sub_datacenters' own "none"
@@ -1503,6 +1505,10 @@ _CB_RECOVER_MIGRATE = "rec:mig:"   # + "<to_dc>:<manifest stem>"
 # just failed on. A user whose card had scrolled away had no way to resume
 # without /confirm, which minted a new batch id and re-ran every try-on.
 _CB_RECOVER_RETRY = "rec:retry:"   # + "<manifest stem>"
+# Opens the Vast tab of the rent panel for the batch whose RunPod rental just failed (spec §3.5).
+# Carries the stem like the other recovery buttons, and is only honoured for the manifest THIS chat
+# is on: it opens a panel, it never spends — the spend button on that panel carries its own token.
+_CB_RECOVER_VAST = "rec:vast:"     # + "<manifest stem>"
 
 # The reuse-or-rerun chooser _do_confirm sends when the journal already holds
 # a matching try-on. Both carry _run_token for the same reason the spend
@@ -1906,6 +1912,21 @@ def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> Non
                 _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                            dry_run=dry_run)
 
+        elif data.startswith(_CB_RECOVER_VAST):
+            stem = data[len(_CB_RECOVER_VAST):]
+            if not stem or stem != _job_manifest_path(chat_id).stem:
+                tg.send_message(chat_id, "that button is from an earlier batch; "
+                                         "check /status")
+            else:
+                # The same latch tick_phase_a sets, so every re-render of this panel (Refresh,
+                # the [RunPod] tab, Back) stays on the rent panel and its spend button resumes
+                # into a rental instead of dropping to the "run try-on first" screen.
+                _PHASE_A_OFFERED[chat_id] = _run_token(chat_id)
+                # The interstitial becomes the panel: the search takes a few seconds and an
+                # unchanged chat for that long reads as a dead button.
+                wait_id = tg.send_message(chat_id, "🔄 Asking Vast for offers…")
+                _offer_run_for_chat(tg, chat_id, message_id=wait_id, gpu_provider="vast")
+
         elif data.startswith(_CB_RECOVER_SWITCH):
             short, _, stem = data[len(_CB_RECOVER_SWITCH):].partition(":")
             gpu_id = _GPU_BY_SHORT.get(short)
@@ -3467,7 +3488,7 @@ def tick_progress(tg: Tg, chat_id: int) -> None:
         # a brand new job that had nothing to do with the old handoff.
         hpath.unlink(missing_ok=True)
         final_text = progress_text(manifest_path, lease=lease_for(manifest_path),
-                                   stages=stages)
+                                   stages=stages, **_billing_kwargs(payload))
         tg.edit_message(chat_id, message_id, final_text, parse_mode=PARSE_HTML)
         path.unlink(missing_ok=True)
         _ANIM_PAUSE.pop(chat_id, None)
@@ -3502,7 +3523,7 @@ def tick_progress(tg: Tg, chat_id: int) -> None:
         return
 
     text = progress_text(manifest_path, lease=lease_for(manifest_path),
-                         stages=stages)
+                         stages=stages, **_billing_kwargs(payload))
     if running:
         try:
             if not tg.edit_message(chat_id, message_id, text,
@@ -5172,7 +5193,10 @@ def _ask_kill(tg: Tg, chat_id: int) -> None:
     spent = ""
     if lease is not None:
         mins = int((time.time() - lease.provisioned_at) / 60)
-        spent = f" — already {mins} min (${mins / 60 * 0.99:.2f}) on the pod"
+        # RunPod's flat rate is only true of RunPod; a Vast pod is priced by its own offer, which
+        # the lease does not carry, so it gets the time and no invented dollar figure.
+        spent = (f" — already {mins} min (${mins / 60 * 0.99:.2f}) on the pod"
+                 if lease.provider == "runpod" else f" — already {mins} min on the pod")
     tg.send_message(
         chat_id,
         f"{ICON_WARN} This destroys the pod right now{spent}. Whatever is mid-render "
@@ -6370,6 +6394,7 @@ def _handle(tg: Tg, update: dict, *, allowed_user_id: int,
         # never disagree with what is already on screen.
         stages = None
         phase = None
+        payload: dict = {}
         prog = _progress_path(chat_id)
         if prog.exists():
             try:
@@ -6387,7 +6412,8 @@ def _handle(tg: Tg, update: dict, *, allowed_user_id: int,
         tg.send_message(chat_id,
                         progress_text(manifest_path,
                                       lease=lease_for(manifest_path),
-                                      stages=stages, phase=phase),
+                                      stages=stages, phase=phase,
+                                      **_billing_kwargs(payload)),
                         parse_mode=PARSE_HTML)
         return
 
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest scripts.tests.test_batch_bot`
Expected: OK — 670 tests.

- [ ] **Step 5: Gates and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -m "bot: offer Vast from the RunPod stock-out card; price a Vast pod at its own rate"
```

---

### Task 7: Documentation, configuration, and the full free gates

**Files:**
- Modify: `.env.example`, `docs/gpu-pod.md` (a `#vast-bot` section and the "NOT verified yet" list), `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` (an "As built" note under §3.5)

**Interfaces:** none (documentation). The unverified-assumption bullets are part of the deliverable: the bot on the VPS needs `vastai` installed and logged in (it is not installed there yet), the cost and cold-start figures are estimates from two hosts, a quote blocks the poll loop while it runs, and the bot has never been run against a real Vast rental.

- [ ] **Step 1: Apply the edits**

`.env.example`:

```diff
--- a/.env.example
+++ b/.env.example
@@ -22,6 +22,16 @@ DISK=120
 MAX_DPH=0.60
 RELIABILITY=0.95
 
+# --- Vast as a per-batch choice in the Telegram bot (docs/gpu-pod.md#vast-bot) ----------------------
+# The Vast tab of the bot's Choose GPU screen shows a spend button only for pipelines listed here
+# (comma-separated, names from scripts/batchlib/pipelines.py, e.g. motion-enhance). Empty means
+# never: the spec wants one measured Vast session per pipeline family before its button exists,
+# and as of 2026-09-19 only the `motion` stage has ever run on Vast. Add a name after its paid check.
+VAST_ENABLED_PIPELINES=
+# The GPU name in Vast's spelling. Empty derives it from GPU above: "NVIDIA GeForce RTX 5090" and
+# "...4090" are translated, and any other RunPod-style name is refused by pod-provision.sh.
+VAST_GPU=
+
 # Lưới an toàn chống quên tắt pod: sau bao nhiêu giờ pod TỰ DỪNG (runpodctl --stop-after).
 # Pod GPU là $0,99/giờ, nên một lần quên tắt để chạy cả tháng là ~$713. Với POD_MAX_HOURS=8 thì
 # một lần quên tốn ~$8.
```

`docs/gpu-pod.md`:

```diff
--- a/docs/gpu-pod.md
+++ b/docs/gpu-pod.md
@@ -591,6 +591,43 @@ Not implemented yet, so the first paid session is not surprised by it: `-o Ident
 <key>` for ssh when the agent holds several keys (measured need 2026-09-19 — no failure seen yet,
 but nothing here picks a specific key if the ssh-agent offers more than one).
 
+<a id="vast-bot"></a>
+### Choosing Vast from the Telegram bot (2026-09-19)
+
+The Choose GPU screen opens with a `[RunPod] [Vast]` row. RunPod is the default and its screen is
+unchanged apart from that row. The Vast tab shows the best qualifying 5090 offer with its $/h and
+location, this batch's bandwidth and estimated session cost, the cold start, and your Vast credit —
+and no datacenter, stock, Switch-GPU or migrate lines, because a Vast rental has no volume. Its spend
+button is hidden, with the reasons written out, unless all of these hold:
+
+- the manifest's pipeline is listed in `VAST_ENABLED_PIPELINES` (`.env` or the environment,
+  comma-separated). Empty by default: the spec wants one measured Vast session per pipeline family
+  before its button exists, and as of 2026-09-19 only the `motion` stage has ever run on Vast. Add a
+  name after its paid session;
+- every stage has a model-registry entry (`make check-vast-models` keeps the repo side honest);
+- a machine passes the filters. The quote is `VAST_QUOTE=1 bash scripts/pod-provision.sh`, i.e.
+  `vast_rent.py --quote`: one JSON line, never a rental (about 4 s measured, bounded at 120 s);
+- `vastai show user --raw` answers and its `credit` covers the estimated session.
+
+The provider rides in the spend button's callback data (`run:go:<token>:vast`,
+`pa:spend:<token>:vast`, `pa:reuse|rerun:<token>:vast`) and reaches `drain.py --provider vast` for
+that run only; the bot never writes it to `.env`. The spend handlers re-check the same conditions when
+the button is tapped, because buttons outlive the state they were drawn for; a refusal spends nothing,
+keeps the draft, and leaves the panel usable. A RunPod stock-out card gains **Rent on Vast instead**,
+which opens the same panel for that batch.
+
+The tap-time check needs a price quote from this bot process (the panel fetches it): after a bot
+restart, an old Vast spend button refuses ("no current Vast price quote") until you open the Vast
+tab or press Refresh.
+
+`GPU=` in `.env` holds the RunPod spelling; `pod-provision.sh` translates it for Vast (`VAST_GPU`
+overrides). Before 2026-09-19 a Vast search with the RunPod name failed outright ("invalid JSON"),
+which only shows once the bot drives a Vast run.
+
+The progress message prices a Vast pod at the rate quoted on the panel (`≈$… so far`, an estimate —
+the offer actually rented can differ and the invoice is the truth), never at RunPod's flat $0.99;
+`/kill` gives the minutes and no dollar figure for a Vast pod.
+
 **First paid session — assumptions that are NOT verified yet:**
 - The exact `vastai ssh-url` output text (parsed tolerantly) and the `create --raw` reply keys.
   When `vastai create` prints non-JSON with exit 0 after an offer vanishes, it is **unverified**
@@ -623,6 +656,16 @@ but nothing here picks a specific key if the ssh-agent offers more than one).
   bootstrap continues has been read through and shellchecked, but never run on a live pod. If it
   hangs or the `wait` never returns, the pod is still billing — check `/tmp/preload-models.log`
   over SSH before assuming a stuck bootstrap is something else.
+- The bot on the VPS needs `vastai` installed and logged in: it runs the quote, reads the credit
+  and starts the drain there — the same gap as the watchdog host, and it is **not installed there
+  yet**. Without it the Vast tab renders and says so, and no button spends.
+- The panel's cold-start and session-cost figures are estimates built from two hosts (warm 4.4 min,
+  cold 9.3 min, `BOOT_AFTER_RUNNING_S` = 17 + 200 + 15 s measured on one warm host) and from
+  `MEASURED_STAGE_SEC`. The first paid sessions are what calibrate them.
+- A quote blocks the bot's poll loop while it runs (about 4 s measured, bounded at 120 s): if Vast's
+  API is slow the whole bot, progress edits included, pauses that long.
+- The bot has never been run against a real Vast rental end to end. The tab, the callbacks and the
+  refusals are covered by unit tests with the network patched out.
 
 Design: `docs/superpowers/specs/2026-09-19-vast-fallback-design.md`.
 
```

`docs/superpowers/specs/2026-09-19-vast-fallback-design.md`:

```diff
--- a/docs/superpowers/specs/2026-09-19-vast-fallback-design.md
+++ b/docs/superpowers/specs/2026-09-19-vast-fallback-design.md
@@ -178,6 +178,19 @@ model set is catalog ids **plus setup steps**. DWPose and RIFE self-download on
   (`batchlib_ext/vast_account.py`, modelled on `runpod_account.py`), or no machine passing the filters.
 - Progress text gains the provider and the running estimated cost.
 
+**As built (Plan 4, 2026-09-19), where it differs from the above:**
+- The RunPod tab is its own callback, `run:rp`, not `run:back`: Back insists on a drafted job in
+  memory, which the rent panel drawn after Phase A may no longer have after a bot restart.
+- The quote is `VAST_QUOTE=1 bash scripts/pod-provision.sh` (`vast_rent.py --quote`), so the search
+  filters come from the one place that derives them; the bot never re-derives them.
+- "One measured session per pipeline family before its button is enabled" is the
+  `VAST_ENABLED_PIPELINES` list, empty by default.
+- The spend handlers re-check the hidden-button conditions (minus the marketplace search) at tap
+  time, before the manifest is rewritten, so a refusal does not invalidate the panel's own buttons.
+- `pod-provision.sh` translates the RunPod GPU name in `.env` to Vast's spelling (`VAST_GPU`); the
+  bot's `.env` holds the RunPod one, and a search with it failed outright.
+- The progress and `/kill` text price a Vast pod at the rate quoted on the panel, or give time only.
+
 ## 4. Testing
 
 Free, no rental:
```

- [ ] **Step 2: Run every free gate**

```bash
make batch-test
make check-job-types && make check-comfy-nodes && make check-batch-params && make check-vast-models
motions-studio/setup/scrub-secrets.sh --check
grep -rn "CONFIRM=yes" scripts/tgbot/*.py | grep -v '^\s*#' | head
```
Expected: `batch-test` OK (about 1,670 tests, one skipped on macOS), each `check-*` prints `✓`, `scrub-secrets` exits 0, and the executable `CONFIRM=yes` literal appears only in `scripts/tgbot/run.py` (docstrings and comments may also mention it; the AST test in `test_batch_tgrun.py` is what actually enforces the single literal).

- [ ] **Step 3: Commit**

```bash
git add .env.example docs/gpu-pod.md docs/superpowers/specs/2026-09-19-vast-fallback-design.md
git commit -m "docs: the Vast tab in the bot, its knobs, and what is still unverified"
```

---

## After this plan

Spec §8 item 6 — the two paid Vast sessions (≈ $0.3–0.6 each) — is what turns a pipeline's Vast button on. Sequence for whoever runs them: install and log in `vastai` on the bot's host; run one `tryon-camera-motion-enhance` batch and one `character-swap-enhance` batch through `make drain PROVIDER=vast` (or through the bot once `VAST_ENABLED_PIPELINES` names the pipeline); record each machine in `batch/vast-machines.json` (the rent function already does); then add the pipeline name to `VAST_ENABLED_PIPELINES`. Nothing in this plan spends money.

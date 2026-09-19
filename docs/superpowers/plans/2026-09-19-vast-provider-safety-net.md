# Vast provider — safety net and provider plumbing (Plan 1 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Vast.ai instance a first-class, *watched* rental: the lease records its provider, the watchdog can list and destroy it, `drain.py --provider vast` runs a whole batch on it, and no script wires a RunPod volume onto it. Nothing in this plan rents a Vast machine.

**Architecture:** `Lease` gains a `provider` field (default `"runpod"`, so leases already on disk stay valid). `podctl.VastCtl` implements the existing `PodControl` protocol; `pod_watchdog.tick` picks the control from `lease.provider` for tiers 1–2 and lists every provider for tier 3. The provider of a run travels in the `GPU_PROVIDER` process environment (set by `drain.py --provider`), never in the root `.env`; the shell scripts and Makefile that read only `.env` learn to prefer it, and a Vast run never has a volume.

**Tech Stack:** Python 3 `unittest` (`scripts/tests/`), bash, GNU make, `vastai` CLI 1.3.0.

**Spec:** `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` (§3.1 and §3.4; §8 steps 1–2). Plans 2 (renting, model registry) and 3 (bot picker) follow once this lands.

## Global Constraints

- Docs, comments and commit messages in **English** (CLAUDE.md). Do not add `# #region ALD` markers.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before **every** commit (the repo is public).
- Never commit `.env` files. Never rent a machine while executing this plan — every test uses fakes or `make -n`.
- Run tests from the repo root: `python3 -m unittest discover -s scripts/tests -p '<file>' [-k <name>] -v`. The full gate is `make batch-test`.
- Commit trailers on every commit:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb`
- Tier 3 authority is scoped by *name*: the Vast instance label must be exactly `motion-transfer` (`batchlib_ext.watchdog.DESTROYABLE_NAMES`).
- Vast facts verified 2026-09-19 with `vastai` 1.3.0: `vastai show instances-v1 --raw --all` prints `{"instances": [...], "next_token": null, "success": true, ...}` (verified only for the empty case — the `id` and `label` keys inside an instance are **unverified until the first paid session in Plan 2**); `vastai destroy instance <id>` prompts `[y/N]` and this repo answers it by piping `y` (the Makefile does the same), so it works on any CLI version; `vastai show instances` is deprecated.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/batchlib_ext/lease.py` | modify | `Lease.provider`, read/write it |
| `scripts/batchlib_ext/podctl.py` | modify | add `VastCtl` (list/destroy) |
| `scripts/pod_watchdog.py` | modify | provider-aware `tick`, wire `VastCtl` in `main` |
| `scripts/drain.py` | modify | `--provider`, provider in lease, no volume off RunPod |
| `scripts/lib-gpu-provider.sh` | create | `gpu_provider`, `pod_volume` shell helpers |
| `scripts/pod-wait.sh`, `pod-bootstrap.sh`, `pod-smoke.sh` | modify | use the helpers |
| `scripts/pod-provision.sh` | modify | label Vast instances `motion-transfer` |
| `Makefile` | modify | `GPU_PROVIDER_EFF`, `POD_VOLUME_EFF`, `drain PROVIDER=` |
| `scripts/tgbot/bot.py` | modify | `/kill` destroys on the lease's provider |
| `scripts/tests/test_batch_lease.py` | modify | provider tests |
| `scripts/tests/test_batch_podctl.py` | modify | `VastCtl` tests |
| `scripts/tests/test_batch_pod_watchdog.py` | modify | two-provider tests |
| `scripts/tests/test_batch_drain.py` | modify | provider tests |
| `scripts/tests/test_batch_provider_wiring.py` | create | shell helper, Makefile and label tests |
| `scripts/tests/test_batch_bot.py` | modify | `/kill` provider test |
| `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` | modify | correct §3.1 |
| `docs/gpu-pod.md` | modify | how to run a Vast batch; watchdog coverage |

---

### Task 1: Correct the spec's §3.1

Planning found the spec's "pitfall" wrong. `pod-provision.sh` already reads `GPU_PROVIDER` from the environment first (line 21) and `POD_VOLUME` with `${POD_VOLUME-…}` (line 142). The real gap is that `pod-wait.sh`, `pod-bootstrap.sh`, `pod-smoke.sh` and the Makefile read **only** `.env`, and that the bot's `/kill` runs `make gpu-destroy` with the bot's own environment.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` (the `**Pitfall:**` bullet in §3.1)

**Interfaces:**
- Produces: nothing code-facing; the spec now states the design Tasks 5–8 implement (Vast run ⇒ no volume, derived from the provider).

- [ ] **Step 1: Replace the pitfall bullet**

Replace this exact text:

```
- **Pitfall:** `pod-provision.sh` resolves values as `${VAR:-$(env_get VAR)}`. `:-` treats an empty
  value as unset and falls back to `.env`, so "override to empty" silently fails. The volume variables
  must use `${VAR-…}` (no colon), otherwise the script dies on `POD_VOLUME=… but GPU_PROVIDER=vast`, or
  worse, attaches the RunPod volume path on Vast.
```

with:

```
- **Correction (found while planning, 2026-09-19).** `pod-provision.sh` already prefers the environment
  for `GPU_PROVIDER` (line 21) and reads `POD_VOLUME` with `${POD_VOLUME-…}` (line 142), so it needs
  only `POD_VOLUME=` passed empty. The gap is elsewhere: `pod-wait.sh`, `pod-bootstrap.sh`,
  `pod-smoke.sh` and every Makefile target read **only** `.env` through their own `env_get`, so an
  exported override never reaches them and `pod-bootstrap.sh` would wire the RunPod volume onto a Vast
  box. Rather than teach each script "override to empty", a **Vast run never has a volume**: those
  scripts derive `POD_VOLUME` as empty whenever the effective provider is not `runpod`
  (`scripts/lib-gpu-provider.sh`, and `GPU_PROVIDER_EFF` / `POD_VOLUME_EFF` in the Makefile).
- The bot's `/kill` (`_do_kill`) also runs `make gpu-destroy` with the bot's own environment, which
  would destroy against `.env`'s provider. It must pass the lease's provider.
```

- [ ] **Step 2: Run the scrub gate and commit**

Run: `bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo $?`
Expected: `0`

```bash
git add docs/superpowers/specs/2026-09-19-vast-fallback-design.md
git commit -m "$(cat <<'EOF'
docs: correct the Vast spec's provider-override design

pod-provision.sh already prefers the environment; the real gap is the scripts and Makefile that read
only .env, and the bot's /kill.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 2: `Lease.provider`, and keep it across a chained job

**Files:**
- Modify: `scripts/batchlib_ext/lease.py`
- Modify: `scripts/drain.py` (`chain_or_teardown`, the `write_lease(LEASE_PATH, Lease(...))` inside it)
- Test: `scripts/tests/test_batch_lease.py`, `scripts/tests/test_batch_drain.py`

**Interfaces:**
- Produces: `Lease(pod_id: str, provisioned_at: float, manifest: str, abs_max_min: int, provider: str = "runpod")`. `read_lease` returns `provider="runpod"` when the key is absent or empty. Every later task and Plans 2–3 read `lease.provider`.

- [ ] **Step 1: Write the failing tests**

Append to `TestLease` in `scripts/tests/test_batch_lease.py` (before `if __name__`):

```python
    def test_provider_defaults_to_runpod(self):
        self.assertEqual(Lease("a", 1.0, "b.yaml", 240).provider, "runpod")

    def test_provider_roundtrip(self):
        lease = Lease(pod_id="777", provisioned_at=1000.0, manifest="batch/x.yaml",
                      abs_max_min=240, provider="vast")
        write_lease(self.tmp, lease)
        self.assertEqual(read_lease(self.tmp), lease)

    def test_a_lease_written_before_providers_existed_reads_as_runpod(self):
        # Leases already on the VPS have no "provider" key. They must stay valid, or the
        # watchdog would treat a live RunPod pod's lease as garbage and reap it as an orphan.
        self.tmp.write_text(
            '{"pod_id": "abc", "provisioned_at": 5.0, "manifest": "batch/x.yaml", '
            '"abs_max_min": 90}', encoding="utf-8")
        self.assertEqual(read_lease(self.tmp),
                         Lease("abc", 5.0, "batch/x.yaml", 90, provider="runpod"))

    def test_an_empty_provider_reads_as_runpod(self):
        self.tmp.write_text(
            '{"pod_id": "abc", "provisioned_at": 5.0, "manifest": "m", '
            '"abs_max_min": 90, "provider": ""}', encoding="utf-8")
        self.assertEqual(read_lease(self.tmp).provider, "runpod")
```

Add to `TestChainOrTeardown` in `scripts/tests/test_batch_drain.py`, directly after `test_success_reports_a_running_handoff_and_extends_the_lease_total`:

```python
    def test_a_chained_link_keeps_the_leases_provider(self):
        # The lease is rewritten for every chained job. Dropping the provider would reset a
        # Vast lease to "runpod" mid-batch, and the watchdog would then try to destroy a Vast
        # instance through runpodctl.
        tmpdir = tempfile.mkdtemp()
        original = self._original(tmpdir)
        nxt = Path(tmpdir) / "tg-1-999.yaml"
        nxt.write_text(NEXT_YAML, encoding="utf-8")
        old_lease = Lease(pod_id="777", provisioned_at=1000.0,
                          manifest=str(original.resolve()), abs_max_min=330,
                          provider="vast")
        with mock.patch.object(drain, "claim_mailbox", side_effect=[nxt, None]), \
             mock.patch.object(drain, "batch_run", return_value=0), \
             mock.patch.object(drain, "read_lease", return_value=old_lease), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "teardown"):
            chain_or_teardown(original)
        self.assertEqual(mock_write_lease.call_args[0][1].provider, "vast")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_lease.py' -v`
Expected: FAIL — `TypeError: Lease.__init__() got an unexpected keyword argument 'provider'` (and `AttributeError` for the default test).

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -k test_a_chained_link_keeps_the_leases_provider -v`
Expected: FAIL — `TypeError` on `Lease(... provider="vast")`.

- [ ] **Step 3: Implement**

In `scripts/batchlib_ext/lease.py`, change the dataclass and `read_lease`:

```python
@dataclass(frozen=True)
class Lease:
    pod_id: str
    provisioned_at: float   # unix seconds, set once at provision time
    manifest: str           # path to the manifest this pod was rented for
    abs_max_min: int        # tier-2 ceiling, computed once at provision time
    provider: str = "runpod"   # which cloud rented it; decides which CLI can destroy it
```

and in `read_lease`, extend the constructor call:

```python
        return Lease(pod_id=str(raw["pod_id"]),
                     provisioned_at=float(raw["provisioned_at"]),
                     manifest=str(raw["manifest"]),
                     abs_max_min=int(raw["abs_max_min"]),
                     provider=str(raw.get("provider") or "runpod"))
```

In `scripts/drain.py` `chain_or_teardown`, replace the `write_lease(LEASE_PATH, Lease(...))` call with:

```python
                write_lease(LEASE_PATH, Lease(
                    pod_id=lease.pod_id, provisioned_at=lease.provisioned_at,
                    manifest=str(nxt.resolve()),
                    abs_max_min=lease.abs_max_min + abs_max_min(nxt_manifest),
                    provider=lease.provider))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_lease.py' -v`
Expected: PASS (8 tests).

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: PASS (all existing tests plus the new one).

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/batchlib_ext/lease.py scripts/drain.py scripts/tests/test_batch_lease.py scripts/tests/test_batch_drain.py
git commit -m "$(cat <<'EOF'
lease: record which cloud rented the pod

Defaults to runpod so leases already on disk stay valid, and survives a chained job's rewrite.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 3: `VastCtl`

**Files:**
- Modify: `scripts/batchlib_ext/podctl.py`
- Test: `scripts/tests/test_batch_podctl.py`

**Interfaces:**
- Consumes: `PodInfo(pod_id: str, name: str)`, the `PodControl` protocol and `DELETE_SETTLE_SEC` already in `podctl.py`.
- Produces: `class VastCtl` with `list_pods() -> list[PodInfo]` (name = the instance `label`) and `destroy(pod_id: str) -> None`. Both raise `RuntimeError` on any failure, exactly like `RunpodCtl`.

- [ ] **Step 1: Write the failing tests**

In `scripts/tests/test_batch_podctl.py` change the import line to:

```python
from batchlib_ext.podctl import PodInfo, RunpodCtl, VastCtl
```

and add this class before `if __name__ == "__main__":`:

```python
class TestVastCtl(unittest.TestCase):
    """VastCtl mirrors RunpodCtl's contract so pod_watchdog can treat both alike."""

    @patch("subprocess.run")
    def test_list_uses_the_non_deprecated_paginated_command_and_all_pages(self, mock_run):
        # `vastai show instances` is deprecated; instances-v1 paginates 25 at a time unless
        # --all is given, and a missed page is an instance the watchdog never sees.
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"instances": [], "next_token": null}', stderr="")
        VastCtl().list_pods()
        self.assertEqual(mock_run.call_args[0][0],
                         ["vastai", "show", "instances-v1", "--raw", "--all"])

    @patch("subprocess.run")
    def test_list_maps_id_and_label(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "instances": [{"id": 51518664, "label": "motion-transfer"},
                          {"id": 51518665, "label": None},
                          {"id": 51518666}],
            "next_token": None}))
        pods = VastCtl().list_pods()
        # ids are integers on vast; PodInfo.pod_id is a string everywhere else.
        self.assertEqual(pods, [PodInfo("51518664", "motion-transfer"),
                                PodInfo("51518665", ""),
                                PodInfo("51518666", "")])

    @patch("subprocess.run")
    def test_empty_stdout_is_no_instances(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertEqual(VastCtl().list_pods(), [])

    @patch("subprocess.run")
    def test_nonzero_exit_raises_runtime_error_with_stderr(self, mock_run):
        # Returning [] would read as "no instances" and tier 3 would do nothing — the safe
        # direction when we cannot see. Raising lets the watchdog say so and skip this provider.
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad api key")
        with self.assertRaises(RuntimeError) as cm:
            VastCtl().list_pods()
        self.assertIn("bad api key", str(cm.exception))

    @patch("subprocess.run")
    def test_malformed_output_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ID  Machine  Status", stderr="")
        with self.assertRaises(RuntimeError) as cm:
            VastCtl().list_pods()
        self.assertIn("invalid JSON", str(cm.exception))

    @patch("subprocess.run")
    def test_missing_instances_key_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"success": false}', stderr="")
        with self.assertRaises(RuntimeError) as cm:
            VastCtl().list_pods()
        self.assertIn("invalid JSON", str(cm.exception))

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_destroy_answers_the_confirmation_prompt(self, mock_run, _sleep):
        # `vastai destroy instance` asks [y/N] and treats EOF as N — then exits 0. Without the
        # piped "y" this "succeeds" and deletes nothing (Makefile:144 records the same trap).
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        VastCtl().destroy("51518664")
        self.assertEqual(mock_run.call_args[0][0],
                         ["vastai", "destroy", "instance", "51518664"])
        self.assertEqual(mock_run.call_args.kwargs["input"], "y\n")

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_destroy_raises_on_non_zero_exit(self, mock_run, _sleep):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such instance")
        with self.assertRaises(RuntimeError) as cm:
            VastCtl().destroy("1")
        self.assertIn("no such instance", str(cm.exception))

    @patch("subprocess.run", side_effect=FileNotFoundError("vastai"))
    def test_a_missing_binary_is_a_runtime_error_not_a_crash(self, _run):
        # The VPS may not have vastai installed. A FileNotFoundError escaping list_pods() would
        # abort the whole watchdog tick and blind the RunPod scan too; tick() only catches
        # RuntimeError, so that is what a provider that cannot be listed must raise.
        with self.assertRaises(RuntimeError) as cm:
            VastCtl().list_pods()
        self.assertIn("vastai", str(cm.exception))

    @patch("time.sleep")
    @patch("subprocess.run", side_effect=FileNotFoundError("vastai"))
    def test_destroy_with_a_missing_binary_is_a_runtime_error(self, _run, _sleep):
        with self.assertRaises(RuntimeError):
            VastCtl().destroy("1")

    @patch("subprocess.run",
           side_effect=subprocess.TimeoutExpired(cmd="vastai", timeout=60))
    def test_a_hung_cli_is_a_runtime_error(self, _run):
        with self.assertRaises(RuntimeError):
            VastCtl().list_pods()
```

Add `import subprocess` to the imports at the top of `scripts/tests/test_batch_podctl.py` (it currently imports `json`, `sys`, `unittest`, `Path` and `unittest.mock` names, not `subprocess`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_podctl.py' -v`
Expected: FAIL — `ImportError: cannot import name 'VastCtl'`.

- [ ] **Step 3: Implement**

Append to `scripts/batchlib_ext/podctl.py`:

```python
class VastCtl:
    """The same list/destroy contract as RunpodCtl, over the vastai CLI.

    Instances are identified to the watchdog by their `label`: pod-provision.sh creates every
    one with `--label motion-transfer`, which is exactly the name
    batchlib_ext.watchdog.DESTROYABLE_NAMES lets tier 3 destroy. An unlabelled instance is not
    ours to kill.

    The JSON shape was checked against `vastai show instances-v1 --raw` (CLI 1.3.0,
    2026-09-19) for the empty case only: `{"instances": [], "next_token": null, ...}`. That an
    instance carries `id` and `label` is taken from the CLI's own column list and is confirmed
    on the first real rental.
    """

    def list_pods(self) -> list[PodInfo]:
        try:
            out = subprocess.run(["vastai", "show", "instances-v1", "--raw", "--all"],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            # OSError covers a vastai that is not installed (likely on the VPS); tick() catches
            # only RuntimeError, so anything else would take the RunPod scan down with it.
            raise RuntimeError(f"could not run vastai: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(f"vastai show instances-v1 failed: {out.stderr.strip()}")
        try:
            data = json.loads(out.stdout or '{"instances": []}')
            return [PodInfo(pod_id=str(i["id"]), name=str(i.get("label") or ""))
                    for i in data["instances"]]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            snippet = out.stdout[:100] if out.stdout else "(empty)"
            raise RuntimeError(
                f"vastai returned invalid JSON: {exc} — output: {snippet}") from exc

    def destroy(self, pod_id: str) -> None:
        """Ask Vast to destroy the instance. Exit code is NOT proof — the caller re-lists.

        The prompt is answered by piping `y` rather than passing `-y`, so this works on CLI
        versions that predate the flag (the Makefile's gpu-destroy does the same).
        """
        try:
            out = subprocess.run(["vastai", "destroy", "instance", pod_id],
                                 input="y\n", capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"could not run vastai: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(
                f"vastai destroy instance {pod_id} failed: {out.stderr.strip()}")
        time.sleep(DELETE_SETTLE_SEC)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_podctl.py' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/batchlib_ext/podctl.py scripts/tests/test_batch_podctl.py
git commit -m "$(cat <<'EOF'
podctl: add VastCtl so the watchdog can list and destroy vast instances

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 4: Watchdog guards both providers

**Files:**
- Modify: `scripts/pod_watchdog.py`
- Test: `scripts/tests/test_batch_pod_watchdog.py`

**Interfaces:**
- Consumes: `Lease.provider` (Task 2), `VastCtl` (Task 3), `PodInfo`, `reconcile`, `reconcile_migration`.
- Produces: `tick(pods_api, first_seen, *, now, dry_run, extra_apis=None)`. `pods_api` stays the RunPod control (so the 23 existing call sites are untouched); `extra_apis` maps a provider name to another `PodControl`, e.g. `{"vast": VastCtl()}`. Tier 1–2 destroy through `apis[lease.provider]`; tier 3 lists every provider independently.

- [ ] **Step 1: Write the failing tests**

In `scripts/tests/test_batch_pod_watchdog.py`, add this class before `class TestOnceExitCode`:

```python
class TestTwoProviders(unittest.TestCase):
    """A Vast instance must be as well guarded as a RunPod pod (spec §3.4)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lease_path = Path(self.temp_dir.name) / "pod-lease.json"
        self.patcher = patch.object(pod_watchdog, "LEASE_PATH", self.lease_path)
        self.patcher.start()
        self.logs: list[str] = []
        self.log_patcher = patch.object(pod_watchdog, "log", self.logs.append)
        self.log_patcher.start()

    def tearDown(self):
        self.log_patcher.stop()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _lease(self, provider: str, *, abs_max_min: int = 10) -> Lease:
        return Lease(pod_id="777", provisioned_at=0.0, manifest="batch/test.yaml",
                     abs_max_min=abs_max_min, provider=provider)

    def test_a_vast_lease_is_destroyed_through_the_vast_control_not_runpod(self):
        write_lease(self.lease_path, self._lease("vast"))
        runpod, vast = FakePods(), FakePods()

        pod_watchdog.tick(runpod, {}, now=1000.0 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(vast.destroyed, ["777"])
        self.assertEqual(runpod.destroyed, [],
                         "a vast lease was sent to runpodctl, which cannot destroy it")
        self.assertFalse(self.lease_path.is_file())

    def test_a_runpod_lease_still_goes_through_runpod(self):
        write_lease(self.lease_path, self._lease("runpod"))
        runpod, vast = FakePods(), FakePods()

        pod_watchdog.tick(runpod, {}, now=1000.0 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(runpod.destroyed, ["777"])
        self.assertEqual(vast.destroyed, [])

    def test_tier_three_reaps_an_unleased_vast_instance_past_grace(self):
        # The instance was created but drain never wrote a lease (or it was lost): the label
        # `motion-transfer` is what gives tier 3 the authority to kill it.
        runpod, vast = FakePods(), FakePods()
        vast.pods = [PodInfo("888", "motion-transfer")]

        pod_watchdog.tick(runpod, {"888": 0.0}, now=11 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(vast.destroyed, ["888"])
        self.assertEqual(runpod.destroyed, [])

    def test_an_unlabelled_vast_instance_is_never_destroyed(self):
        runpod, vast = FakePods(), FakePods()
        vast.pods = [PodInfo("999", "")]

        pod_watchdog.tick(runpod, {"999": 0.0}, now=600 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(vast.destroyed, [])

    def test_a_leased_vast_instance_is_not_an_orphan(self):
        write_lease(self.lease_path, self._lease("vast", abs_max_min=10_000))
        runpod, vast = FakePods(), FakePods()
        vast.pods = [PodInfo("777", "motion-transfer")]

        pod_watchdog.tick(runpod, {"777": 0.0}, now=11 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(vast.destroyed, [])

    def test_a_failing_runpod_listing_does_not_stop_the_vast_scan(self):
        runpod, vast = FakePods(), FakePods()
        runpod._list_error = "runpodctl not configured"
        vast.pods = [PodInfo("888", "motion-transfer")]

        pod_watchdog.tick(runpod, {"888": 0.0}, now=11 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(vast.destroyed, ["888"])
        self.assertIn("cannot list runpod pods", "\n".join(self.logs))

    def test_a_failing_vast_listing_does_not_stop_the_runpod_scan(self):
        runpod, vast = FakePods(), FakePods()
        runpod.pods = [PodInfo("stray", "motion-transfer")]
        vast._list_error = "bad api key"

        pod_watchdog.tick(runpod, {"stray": 0.0}, now=11 * 60.0, dry_run=False,
                          extra_apis={"vast": vast})

        self.assertEqual(runpod.destroyed, ["stray"])
        self.assertIn("cannot list vast pods", "\n".join(self.logs))

    def test_a_failing_listing_keeps_the_first_seen_of_pods_it_could_not_see(self):
        # Not seeing a provider must not reset the grace clock of its instances, or an orphan
        # would get a fresh 10 minutes every time the CLI hiccuped.
        runpod, vast = FakePods(), FakePods()
        vast._list_error = "bad api key"

        out = pod_watchdog.tick(runpod, {"888": 100.0}, now=200.0, dry_run=False,
                                extra_apis={"vast": vast})

        self.assertEqual(out.get("888"), 100.0)

    def test_a_lease_for_an_unconfigured_provider_is_reported_and_tier_three_still_runs(self):
        write_lease(self.lease_path, self._lease("nope", abs_max_min=10_000))
        runpod = FakePods()
        runpod.pods = [PodInfo("stray", "motion-transfer")]

        pod_watchdog.tick(runpod, {"stray": 0.0}, now=11 * 60.0, dry_run=False)

        joined = "\n".join(self.logs)
        self.assertIn("tier 1/2 failed, falling through to tier 3", joined)
        self.assertEqual(runpod.destroyed, ["stray"])
        self.assertTrue(self.lease_path.is_file(), "an unknown-provider lease was cleared")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pod_watchdog.py' -k TestTwoProviders -v`
Expected: FAIL — `TypeError: tick() got an unexpected keyword argument 'extra_apis'`.

- [ ] **Step 3: Implement**

In `scripts/pod_watchdog.py`:

(a) Import `VastCtl`:

```python
from batchlib_ext.podctl import RunpodCtl, VastCtl
```

(b) Replace the signature and the first lines of `tick`:

```python
def tick(pods_api, first_seen: dict[str, float], *, now: float,
         dry_run: bool, extra_apis: dict | None = None) -> dict[str, float]:
    """`pods_api` is the RunPod control; `extra_apis` maps another provider's name to its
    control. A lease is destroyed through the control its own `provider` names — sending a
    Vast lease to runpodctl would "succeed" against nothing and leave the instance billing."""
    apis = {"runpod": pods_api, **(extra_apis or {})}
    lease = read_lease(LEASE_PATH)
```

(c) Inside the tier-1/2 `try:` block, immediately after `if lease is not None:` insert:

```python
            # Resolved before deciding, so a lease naming a provider this process has no
            # control for is reported on every tick (via the except below) instead of only on
            # the tick that finally needs to kill.
            lease_api = apis[lease.provider]
```

and change `if destroy_verified(pods_api, lease.pod_id):` to:

```python
                    if destroy_verified(lease_api, lease.pod_id):
```

Then make the "still billing" message provider-neutral. Replace these two lines inside that `log(...)`:

```python
                            f"still in 'runpodctl pod list' and STILL BILLING. "
```
```python
                            f"hand: runpodctl pod delete {lease.pod_id}")
```

with:

```python
                            f"still in the {lease.provider} listing and STILL BILLING. "
```
```python
                            f"hand ({lease.provider}): {lease.pod_id}")
```

`TestDestroyIsVerified` asserts only `DESTROY NOT CONFIRMED`, `STILL BILLING` and the pod id, all kept.

(d) Replace the tier-3 listing block

```python
    try:
        pods = pods_api.list_pods()
    except RuntimeError as exc:
        # Not seeing is not the same as nothing being there. Skip this tick.
        log(f"cannot list pods, skipping reconciliation: {exc}")
        return first_seen
```

with:

```python
    # Every provider is listed on its own. One CLI failing must not blind the others: a broken
    # `runpodctl` would otherwise stop the scan for a Vast instance that is billing right now.
    pods: list = []
    owner: dict = {}
    any_failed = False
    for provider_name, api in apis.items():
        try:
            listed = api.list_pods()
        except RuntimeError as exc:
            # Not seeing is not the same as nothing being there. Skip this provider.
            log(f"cannot list {provider_name} pods, skipping its reconciliation: {exc}")
            any_failed = True
            continue
        for p in listed:
            pods.append(p)
            owner[p.pod_id] = api
```

(e) In the tier-3 section, change the two `reconcile_migration` / kill lines. Replace

```python
    migrate_kill, _ = reconcile_migration(pods=pods, lease=migrate_lease,
                                          first_seen=first_seen, now=now)
```

with (migration temp pods only ever exist on RunPod):

```python
    migrate_kill, _ = reconcile_migration(
        pods=[p for p in pods if owner[p.pod_id] is pods_api], lease=migrate_lease,
        first_seen=first_seen, now=now)
```

and in the `for pod_id in kill:` loop change `destroy_verified(pods_api, pod_id)` to `destroy_verified(owner[pod_id], pod_id)`, and the log line's manual hint `runpodctl pod delete {pod_id}` to `delete it by hand at the provider`. Keep the words `DESTROY NOT CONFIRMED` and `STILL BILLING` — `TestDestroyIsVerified` asserts them.

(f) Replace the final `return seen` of `tick` with:

```python
    # When a provider could not be listed, its instances are absent from `seen`; carrying the
    # old entries over keeps their grace clock instead of restarting it next tick.
    return {**first_seen, **seen} if any_failed else seen
```

(g) In `main()`:

```python
    pods_api = RunpodCtl()
    extra_apis = {"vast": VastCtl()}
```

and pass `extra_apis=extra_apis` to the `tick(...)` call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pod_watchdog.py' -v`
Expected: PASS — the new class and every existing class (they call `tick` without `extra_apis`).

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_watchdog.py' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/pod_watchdog.py scripts/tests/test_batch_pod_watchdog.py
git commit -m "$(cat <<'EOF'
watchdog: guard vast instances in every tier

Tier 1/2 destroy through the lease's own provider; tier 3 lists each provider independently so one
broken CLI cannot blind the other.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 5: `drain.py --provider`

**Files:**
- Modify: `scripts/drain.py`
- Modify: `Makefile` (`drain` target only)
- Test: `scripts/tests/test_batch_drain.py`, `scripts/tests/test_batch_provider_wiring.py` (created here)

**Interfaces:**
- Consumes: `Lease.provider` (Task 2).
- Produces: `drain.effective_provider() -> str` (`os.environ["GPU_PROVIDER"]`, else `.env`'s value, else `"vast"` — the same order as `pod-provision.sh:21`); CLI flag `--provider {runpod,vast}`; `make drain … PROVIDER=vast`. After `main()` parses `--provider`, `os.environ["GPU_PROVIDER"]` is set, so every child process (provision, wait, bootstrap, `make gpu-destroy`, `make gpu-logs`) inherits it.

- [ ] **Step 1: Write the failing tests**

Add `import os` to the imports at the top of `scripts/tests/test_batch_drain.py`, then append before `if __name__ == "__main__":`:

```python
class TestProvider(unittest.TestCase):
    """The provider of a run follows the run — env and lease — never the root .env."""

    def _manifest_path(self) -> Path:
        return Path(tempfile.mkdtemp()) / "tg-1.yaml"

    def test_effective_provider_prefers_the_environment(self):
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}), \
             mock.patch.object(drain, "env_get", return_value="runpod"):
            self.assertEqual(drain.effective_provider(), "vast")

    def test_effective_provider_falls_back_to_dotenv_then_vast(self):
        env = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(drain, "env_get", return_value="runpod"):
                self.assertEqual(drain.effective_provider(), "runpod")
            # pod-provision.sh:21 defaults to vast when nothing says otherwise.
            with mock.patch.object(drain, "env_get", return_value=""):
                self.assertEqual(drain.effective_provider(), "vast")

    def test_provision_passes_no_volume_off_runpod(self):
        # pod-provision.sh dies on POD_VOLUME set with a non-runpod provider. `.env` keeps the
        # RunPod volume for the home provider, so a vast run has to blank it for the child.
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "vast"}), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "777"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertIn("POD_VOLUME= ", mock_run.call_args[0][0])

    def test_provision_leaves_the_volume_alone_on_runpod(self):
        with mock.patch.dict(os.environ, {"GPU_PROVIDER": "runpod"}), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertNotIn("POD_VOLUME", mock_run.call_args[0][0])

    def test_provision_is_unchanged_when_no_provider_was_chosen(self):
        env = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(drain.subprocess, "run") as mock_run, \
             mock.patch.object(drain, "env_get", side_effect=["8", "pod-xyz"]):
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            drain.provision(ceiling_min=120, manifest_path=self._manifest_path())
        self.assertNotIn("POD_VOLUME", mock_run.call_args[0][0])

    def test_main_exports_the_provider_and_writes_it_into_the_lease(self):
        tmp = Path(tempfile.mkdtemp())
        manifest = tmp / "tg-1.yaml"
        manifest.write_text(
            "runs:\n  - id: a\n    pipeline: motion-enhance\n"
            "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n", encoding="utf-8")
        seen_env = {}

        def fake_provision(**_kw):
            seen_env["GPU_PROVIDER"] = os.environ.get("GPU_PROVIDER")
            return "777"

        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(sys, "argv", ["drain.py", "--file", str(manifest),
                                             "--yes", "--provider", "vast"]), \
             mock.patch.object(drain, "batch_run", side_effect=[drain.EXIT_NEEDS_POD, 0]), \
             mock.patch.object(drain, "provision", side_effect=fake_provision), \
             mock.patch.object(drain, "write_lease") as mock_write_lease, \
             mock.patch.object(drain, "wait_and_bootstrap"), \
             mock.patch.object(drain, "chain_or_teardown"):
            self.assertEqual(drain.main(), 0)
        self.assertEqual(seen_env["GPU_PROVIDER"], "vast")
        lease = mock_write_lease.call_args[0][1]
        self.assertEqual((lease.pod_id, lease.provider), ("777", "vast"))
```

Create `scripts/tests/test_batch_provider_wiring.py`:

```python
"""Wiring that tests elsewhere cannot see: the Makefile and shell scripts that read only .env.

These run `make -n` (prints the recipe, executes nothing) and small bash snippets; no rental,
no network.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from batchlib_ext.watchdog import DESTROYABLE_NAMES

LIB = ROOT / "scripts" / "lib-gpu-provider.sh"


def _make(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k != "GPU_PROVIDER"}
    e.update(env or {})
    return subprocess.run(["make", "-n", *args], cwd=ROOT, env=e,
                          capture_output=True, text=True)


class TestDrainTarget(unittest.TestCase):
    def test_provider_is_forwarded_to_drain_py(self):
        out = _make("drain", "FILE=batch/x.yaml", "PROVIDER=vast", "CONFIRM=yes")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--provider vast", out.stdout)
        self.assertIn("--yes", out.stdout)

    def test_no_provider_adds_no_flag(self):
        out = _make("drain", "FILE=batch/x.yaml")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("--provider", out.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -k TestProvider -v`
Expected: FAIL — `AttributeError: module 'drain' has no attribute 'effective_provider'`.

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: FAIL — `--provider vast` not in the printed recipe.

- [ ] **Step 3: Implement**

In `scripts/drain.py`:

(a) Add `import os` next to the other stdlib imports.

(b) Add after `def sh(...)`:

```python
def effective_provider() -> str:
    """The cloud THIS run rents from. Same order as pod-provision.sh:21: the process
    environment (set by --provider), then .env, then vast."""
    return os.environ.get("GPU_PROVIDER") or env_get(ROOT / ".env", "GPU_PROVIDER") or "vast"
```

(c) In `provision(...)`, replace the `subprocess.run(...)` command with a prefix that blanks the volume off RunPod. Only the process environment is consulted here (an `env_get` call would consume the ordered `side_effect` lists in the existing `TestProvision` tests):

```python
    # A vast run has no volume. `.env` keeps the RunPod one for the home provider, and
    # pod-provision.sh dies on POD_VOLUME set with a non-runpod provider — so blank it for the
    # child. Only an EXPLICIT non-runpod provider (from --provider) triggers this; with none
    # chosen the old behaviour is byte-for-byte unchanged.
    chosen = os.environ.get("GPU_PROVIDER", "")
    no_volume = "POD_VOLUME= " if chosen and chosen != "runpod" else ""
    result = subprocess.run(
        f"{no_volume}POD_MAX_HOURS={hours} CONFIRM=yes bash scripts/pod-provision.sh",
        shell=True, cwd=ROOT, stderr=subprocess.PIPE, text=True)
```

(d) In `main()`, add the argument next to `--yes`:

```python
    ap.add_argument("--provider", choices=("runpod", "vast"),
                    help="rent from this cloud for this run only; .env is never rewritten")
```

and right after `args = ap.parse_args()`:

```python
    if args.provider:
        # Exported, not written to .env: every child (provision, wait, bootstrap,
        # `make gpu-destroy`, `make gpu-logs`) inherits it, and a crash cannot leave the
        # root .env pointing at the wrong cloud.
        os.environ["GPU_PROVIDER"] = args.provider
```

(e) In `main()` change the lease write to record the provider:

```python
    write_lease(LEASE_PATH, Lease(pod_id=pod_id, provisioned_at=time.time(),
                                  manifest=str(manifest_path.resolve()),
                                  abs_max_min=ceiling,
                                  provider=effective_provider()))
```

In `Makefile`, change the `drain` recipe's argument list:

```make
drain: ## Rent a pod, run FILE, destroy it (dry run unless CONFIRM=yes; PROVIDER=vast|runpod overrides .env for this run; PHASE_A=1 stops before renting)
	@test -n "$(FILE)" || { echo "usage: make drain FILE=batch/….yaml [CONFIRM=yes] [RESUME=1] [FORCE_LOCAL=1] [PHASE_A=1] [PROVIDER=vast|runpod]"; exit 1; }
	@python3 scripts/drain.py --file "$(FILE)" \
		$(if $(filter yes,$(CONFIRM)),--yes) $(if $(RESUME),--resume) \
		$(if $(FORCE_LOCAL),--force-local) $(if $(PHASE_A),--phase-a-only) \
		$(if $(PROVIDER),--provider $(PROVIDER))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: PASS (all).

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/drain.py Makefile scripts/tests/test_batch_drain.py scripts/tests/test_batch_provider_wiring.py
git commit -m "$(cat <<'EOF'
drain: --provider chooses the cloud for one run

Exported through the process environment and recorded in the lease; the root .env is never
rewritten, so a crash cannot leave it pointing at the wrong cloud. make drain PROVIDER=vast.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 6: Scripts and Makefile learn the run's provider; label Vast instances

**Files:**
- Create: `scripts/lib-gpu-provider.sh`
- Modify: `scripts/pod-wait.sh`, `scripts/pod-bootstrap.sh`, `scripts/pod-smoke.sh`, `scripts/pod-provision.sh`, `Makefile`
- Test: `scripts/tests/test_batch_provider_wiring.py`

**Interfaces:**
- Consumes: `drain.py` exports `GPU_PROVIDER` (Task 5). Callers define `env_get()` (reads `.env` in cwd) before sourcing.
- Produces: bash `gpu_provider` (prints `$GPU_PROVIDER`, else `.env`'s, else `vast`) and `pod_volume` (prints `.env`'s `POD_VOLUME` only when the provider is `runpod`, else nothing); Make variables `GPU_PROVIDER_EFF` and `POD_VOLUME_EFF`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_batch_provider_wiring.py` (add `_sh` before the classes and these classes before `if __name__`):

```python
def _sh(env_file: str, extra_env: dict, snippet: str) -> str:
    """Run `snippet` in bash the way pod-*.sh do: a cwd holding a .env that env_get greps."""
    cwd = Path(tempfile.mkdtemp())
    (cwd / ".env").write_text(env_file, encoding="utf-8")
    script = (
        "env_get() { grep -E \"^$1=\" .env 2>/dev/null | cut -d= -f2- "
        "| sed -E 's/[[:space:]]*#.*$//' | tr -d '\"'; }\n"
        f'. "{LIB}"\n{snippet}\n')
    env = {k: v for k, v in os.environ.items() if k not in ("GPU_PROVIDER", "POD_VOLUME")}
    env.update(extra_env)
    out = subprocess.run(["bash", "-c", script], cwd=cwd, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


class TestProviderHelpers(unittest.TestCase):
    DOTENV = "GPU_PROVIDER=runpod\nPOD_VOLUME=/workspace\n"

    def test_provider_comes_from_dotenv(self):
        self.assertEqual(_sh(self.DOTENV, {}, "gpu_provider"), "runpod")

    def test_the_environment_overrides_dotenv(self):
        self.assertEqual(_sh(self.DOTENV, {"GPU_PROVIDER": "vast"}, "gpu_provider"), "vast")

    def test_nothing_set_defaults_to_vast_like_pod_provision(self):
        self.assertEqual(_sh("", {}, "gpu_provider"), "vast")

    def test_runpod_keeps_its_volume(self):
        self.assertEqual(_sh(self.DOTENV, {}, "pod_volume"), "/workspace")

    def test_a_vast_run_never_has_a_volume_whatever_dotenv_says(self):
        # The bug this prevents: .env keeps the RunPod volume for the home provider, and
        # pod-bootstrap.sh would wire /workspace onto a Vast box that has no such mount.
        self.assertEqual(_sh(self.DOTENV, {"GPU_PROVIDER": "vast"}, "pod_volume"), "")


class TestScriptsUseTheHelpers(unittest.TestCase):
    def test_wait_bootstrap_and_smoke_source_the_shared_helper(self):
        for name in ("pod-wait.sh", "pod-bootstrap.sh", "pod-smoke.sh"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("lib-gpu-provider.sh", text, name)

    def test_bootstrap_and_smoke_no_longer_read_the_volume_straight_from_dotenv(self):
        for name in ("pod-bootstrap.sh", "pod-smoke.sh"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn('POD_VOLUME="$(env_get POD_VOLUME)"', text, name)


class TestGpuDestroyTarget(unittest.TestCase):
    def test_a_vast_run_destroys_with_vastai_and_skips_the_volume_db_backup(self):
        out = _make("gpu-destroy", env={"GPU_PROVIDER": "vast"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("vastai destroy instance", out.stdout)
        self.assertNotIn("runpodctl pod delete", out.stdout)
        # pod-pgdump.sh needs the RunPod volume; on Vast it can only fail noisily.
        self.assertNotIn("pod-pgdump.sh --dump", out.stdout)

    def test_a_runpod_run_still_destroys_with_runpodctl(self):
        out = _make("gpu-destroy", env={"GPU_PROVIDER": "runpod"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("runpodctl pod delete", out.stdout)
        self.assertNotIn("vastai destroy instance", out.stdout)
        self.assertIn("pod-pgdump.sh --dump", out.stdout)


class TestVastInstancesAreLabelled(unittest.TestCase):
    def test_create_labels_the_instance_with_a_name_tier_three_may_destroy(self):
        # Two hand-copied lists (this label, watchdog.DESTROYABLE_NAMES) that must agree; the
        # comment above DESTROYABLE_NAMES admits no gate ties them together. This is that gate.
        text = (ROOT / "scripts" / "pod-provision.sh").read_text(encoding="utf-8")
        found = re.search(r"CREATE=\(vastai create instance .*--label (\S+?)\)", text)
        self.assertIsNotNone(found, "the vast create command carries no --label")
        self.assertIn(found.group(1), DESTROYABLE_NAMES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: FAIL — helper file missing (`gpu_provider: command not found`), no `--label`, `vastai destroy instance` printed for a `runpod` run because the Makefile still greps `.env`.

- [ ] **Step 3: Implement**

Create `scripts/lib-gpu-provider.sh`:

```bash
# shellcheck shell=bash
#
# Which cloud THIS run rents from, and whether it can have a Network Volume.
#
# Sourced by pod-wait.sh, pod-bootstrap.sh and pod-smoke.sh. The caller must define env_get()
# (reads KEY from ./.env) first.
#
# Why this exists: those scripts read only .env, but a Vast run must not change the root .env
# (a crash would leave it pointing at the wrong cloud). drain.py --provider exports
# GPU_PROVIDER instead; this is where the scripts learn to prefer it. Same order as
# pod-provision.sh:21: environment, then .env, then vast.

gpu_provider() {
  local p="${GPU_PROVIDER:-$(env_get GPU_PROVIDER)}"
  printf '%s' "${p:-vast}"
}

# A Network Volume is a RunPod feature. .env keeps the RunPod volume for the home provider, so
# a Vast run must not inherit it — pod-bootstrap.sh would otherwise try to wire /workspace onto a
# box that has no such mount.
pod_volume() {
  if [ "$(gpu_provider)" = "runpod" ]; then env_get POD_VOLUME; fi
  return 0
}
```

`scripts/pod-wait.sh` — after the `env_set()` function (the block that ends before `PROVIDER=`), replace

```bash
PROVIDER="$(env_get GPU_PROVIDER)"; PROVIDER="${PROVIDER:-vast}"
```

with:

```bash
# shellcheck source=lib-gpu-provider.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-gpu-provider.sh"
PROVIDER="$(gpu_provider)"
```

`scripts/pod-bootstrap.sh` — after the `env_get()` definition (line 16) add the same two source lines (without the `PROVIDER=` line), and replace `POD_VOLUME="$(env_get POD_VOLUME)"` with:

```bash
POD_VOLUME="$(pod_volume)"
```

`scripts/pod-smoke.sh` — after the `env_get()` definition (line 37) add the same source line using `$ROOT`-independent path, and replace `POD_VOLUME="$(env_get POD_VOLUME)"` with `POD_VOLUME="$(pod_volume)"`:

```bash
# shellcheck source=lib-gpu-provider.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-gpu-provider.sh"
```

`scripts/pod-provision.sh` — label the Vast instance:

```bash
CREATE=(vastai create instance "$BEST" --image "$IMAGE" --disk "$DISK" --ssh --direct --label motion-transfer)
```

with this comment on the line above:

```bash
# --label motion-transfer is what gives the watchdog's tier 3 authority to reap this instance if
# no lease ever claims it (batchlib_ext.watchdog.DESTROYABLE_NAMES; test_batch_provider_wiring.py
# keeps the two in step).
```

`Makefile` — after the `env = …` line (line 30) add:

```make
# The provider of THIS run. drain.py exports GPU_PROVIDER for a Vast run so the root .env can keep
# saying runpod; a bare `make gpu-destroy` with nothing exported still reads .env as it always did.
GPU_PROVIDER_EFF := $(or $(GPU_PROVIDER),$(call env,GPU_PROVIDER))
# A Network Volume is RunPod-only — a Vast box has none, whatever .env says.
POD_VOLUME_EFF := $(if $(filter runpod,$(GPU_PROVIDER_EFF)),$(call env,POD_VOLUME))
```

Then in `gpu-up`, `gpu-down`, `gpu-destroy` replace each of the three lines

```make
ifeq ($(shell grep -E '^GPU_PROVIDER=' .env 2>/dev/null | cut -d= -f2),runpod)
```

with

```make
ifeq ($(GPU_PROVIDER_EFF),runpod)
```

In `gpu-down`, change `POD_VOLUME='$(call env,POD_VOLUME)'` to `POD_VOLUME='$(POD_VOLUME_EFF)'`.

In `gpu-destroy`, wrap the final-backup `ssh` command (from `@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \` through its `|| echo "!! sao lưu lần cuối KHÔNG thành công …"` line) in a directive at column 0, and use the effective volume inside it:

```make
ifeq ($(GPU_PROVIDER_EFF),runpod)
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(POD_VOLUME_EFF)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu lần cuối KHÔNG thành công (lý do ở ngay trên) — vẫn XOÁ pod theo yêu cầu."
endif
```

(Keep the existing `@#` comment lines above it. Only the `ssh` command moves inside the `ifeq`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: PASS.

Run: `for f in pod-wait pod-bootstrap pod-smoke pod-provision lib-gpu-provider; do bash -n scripts/$f.sh || echo "BAD $f"; done; echo checked`
Expected: only `checked` (no `BAD …` line). `bash -n` takes one script, hence the loop.

Run: `make -n gpu-destroy GPU_PROVIDER=runpod | head -5` and `make -n gpu-destroy GPU_PROVIDER=vast | head -5`
Expected: both print a recipe (no `make: *** ` errors).

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/lib-gpu-provider.sh scripts/pod-wait.sh scripts/pod-bootstrap.sh scripts/pod-smoke.sh scripts/pod-provision.sh Makefile scripts/tests/test_batch_provider_wiring.py
git commit -m "$(cat <<'EOF'
scripts: a vast run never gets a network volume; label vast instances

pod-wait/bootstrap/smoke and the Makefile read only .env. They now prefer the run's provider, and
derive an empty POD_VOLUME off RunPod. Vast instances carry the label tier 3 may destroy.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 7: `/kill` destroys on the lease's provider

**Files:**
- Modify: `scripts/tgbot/bot.py` (`import os`, `_do_kill`)
- Test: `scripts/tests/test_batch_bot.py` (`TestKillCommand`)

**Interfaces:**
- Consumes: `Lease.provider` (Task 2); `lease_for(manifest_path)` already imported in `bot.py` (returns the `Lease` for that manifest or `None`).
- Produces: `_do_kill` passes `env={**os.environ, "GPU_PROVIDER": lease.provider}` to `make gpu-destroy` when a lease names the run, and `env=None` (inherit, unchanged) when there is none.

- [ ] **Step 1: Write the failing tests**

In `scripts/tests/test_batch_bot.py` add to the imports block `from batchlib_ext.lease import Lease`, then add to `TestKillCommand` after `test_confirming_terminates_a_tracked_popen_then_destroys_the_pod`:

```python
    def test_kill_destroys_on_the_leases_provider_not_the_env_files(self):
        # The bot's own environment says nothing about the run: without this, /kill on a Vast
        # batch would run `make gpu-destroy` against .env's provider (RunPod) and leave the
        # Vast instance billing while telling the user it was destroyed.
        lease = Lease(pod_id="777", provisioned_at=0.0, manifest=str(self.manifest),
                      abs_max_min=60, provider="vast")
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("tgbot.bot.lease_for", return_value=lease), \
             mock.patch("tgbot.bot.subprocess.run", return_value=ok) as run, \
             mock.patch("tgbot.bot.clear_lease"):
            bot.handle(self.tg, cb_from(ME, bot._CB_KILL_GO), allowed_user_id=ME)
        self.assertEqual(run.call_args.args[0], ["make", "gpu-destroy"])
        self.assertEqual(run.call_args.kwargs["env"]["GPU_PROVIDER"], "vast")

    def test_kill_without_a_lease_leaves_the_environment_alone(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch("tgbot.bot.lease_for", return_value=None), \
             mock.patch("tgbot.bot.subprocess.run", return_value=ok) as run, \
             mock.patch("tgbot.bot.clear_lease"):
            bot.handle(self.tg, cb_from(ME, bot._CB_KILL_GO), allowed_user_id=ME)
        self.assertIsNone(run.call_args.kwargs.get("env"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -k TestKillCommand -v`
Expected: FAIL — `KeyError: 'env'` on the first new test.

- [ ] **Step 3: Implement**

In `scripts/tgbot/bot.py` add `import os` after `import json` in the stdlib import block. In `_do_kill`, replace

```python
    tg.send_message(chat_id, "🛑 destroying the pod…")
    try:
        result = subprocess.run(["make", "gpu-destroy"], cwd=_REPO_ROOT,
                                capture_output=True, text=True, timeout=180)
```

with:

```python
    tg.send_message(chat_id, "🛑 destroying the pod…")
    # The lease says which cloud rented the pod. The bot's own environment does not, and a bare
    # `make gpu-destroy` would fall back to .env's provider. Read before clear_lease below.
    lease = lease_for(manifest_path)
    kill_env = {**os.environ, "GPU_PROVIDER": lease.provider} if lease is not None else None
    try:
        result = subprocess.run(["make", "gpu-destroy"], cwd=_REPO_ROOT,
                                capture_output=True, text=True, timeout=180, env=kill_env)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -k TestKillCommand -v`
Expected: PASS (existing kill tests assert only `args[0]`, so the extra `env` kwarg does not affect them).

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -m "$(cat <<'EOF'
bot: /kill destroys on the lease's provider

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 8: Document it and run every gate

**Files:**
- Modify: `docs/gpu-pod.md` (append a short section after the `#vast-search-sampling` section)

**Interfaces:**
- Produces: the runbook line a human needs to run a Vast batch by hand and what the watchdog now covers.

- [ ] **Step 1: Add the doc section**

Append after the `#vast-search-sampling` section of `docs/gpu-pod.md`:

```markdown
<a id="vast-provider"></a>
### Running one batch on Vast — `PROVIDER=vast` (2026-09-19)

`make drain FILE=batch/<name>.yaml PROVIDER=vast CONFIRM=yes` rents on Vast for that run only. The
root `.env` keeps `GPU_PROVIDER=runpod`; the provider travels in the process environment and in the
lease (`batch/pod-lease.json`, field `provider`). A Vast run never has a Network Volume, whatever
`POD_VOLUME` says in `.env`.

What guards a Vast instance: the lease (tiers 1–2, destroyed through `vastai`) and the label
`motion-transfer` that `pod-provision.sh` puts on every instance it creates (tier 3, which now lists
Vast as well as RunPod). `/kill` in the Telegram bot reads the lease's provider. **There is no
`--terminate-after` on Vast** — the lease's `abs_max_min` is the only hard ceiling.

A failed job is **not** inspectable after `gpu-destroy` on Vast (the database dies with the box, unlike
RunPod's volume). `teardown()` pulls `pod-job.log` into `out/<batch>/runs/*/` before destroying when a
stage failed; that is the only post-mortem.

Not yet automated (Plan 2): choosing the machine, per-batch model download, the pull deadline.
Design: `docs/superpowers/specs/2026-09-19-vast-fallback-design.md`.
```

- [ ] **Step 2: Run every gate**

Run: `make batch-test`
Expected: PASS (all modules, including the new ones).

Run: `make check-job-types && make check-batch-params`
Expected: both exit 0.

Run: `make watchdog-dry`
Expected: exit 0; the log shows `tier 3: N pod(s) visible` (and, if `vastai` has no key configured, `cannot list vast pods, skipping its reconciliation` — that line is correct behaviour, not a failure).

Run: `bash motions-studio/setup/scrub-secrets.sh --check; echo $?`
Expected: `0`

- [ ] **Step 3: Commit**

```bash
git add docs/gpu-pod.md
git commit -m "$(cat <<'EOF'
docs: how to run a batch on Vast and what guards it

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

## Self-Review

**Spec coverage (§8 steps 1–2, §3.1, §3.4):**
- `Lease.provider`, default for old leases → Task 2. Kept across chained jobs → Task 2.
- `VastCtl` (list/label filter/`y` piped/verify by re-list) → Task 3; re-list verification is `pod_watchdog.destroy_verified`, unchanged.
- Watchdog `provider → PodControl`, tier 1–2 by `lease.provider`, tier 3 lists both, a failing provider does not blind the other → Task 4.
- Label `--label motion-transfer` on create, tied to `DESTROYABLE_NAMES` by a test → Task 6.
- `drain.py --provider`, exported env, provider in lease, `make drain PROVIDER=` → Task 5.
- "Vast run has no volume" (corrected §3.1) → Tasks 5 (provision), 6 (wait/bootstrap/smoke/Makefile).
- Makefile prefers `$(GPU_PROVIDER)` → Task 6.
- `/kill` on the right provider (found while planning) → Task 7.
- Spec corrected → Task 1. Docs → Task 8.
- **Deliberately not here** (Plans 2–3): rent function, scoreboard, pull deadline, model registry, `--cancel-unavail`, direct-SSH handling, `start_drain` forwarding `PROVIDER`, bot picker, `vast_account.py`.

**Known gaps, stated:** `bot.py`'s `start_drain` does not yet pass `PROVIDER=` (Plan 3), so the bot cannot start a Vast run yet — only `make drain PROVIDER=vast` can. `pod-wait.sh` still uses the Vast SSH *proxy* address (Plan 2 switches to the direct address). `id`/`label` keys of a real Vast instance are unverified until Plan 2's first paid session.

**Placeholder scan:** none — every code step carries the code.

**Type consistency:** `Lease(..., provider: str = "runpod")` used identically in Tasks 2, 4, 5, 7; `tick(..., extra_apis=)` defined in Task 4 and only called with that name; `VastCtl.list_pods/destroy` match `PodControl`; `gpu_provider`/`pod_volume` (Task 6) match the names asserted in `TestProviderHelpers`; `GPU_PROVIDER_EFF`/`POD_VOLUME_EFF` used consistently in the Makefile edits; `effective_provider()` (Task 5) is the only new `drain` symbol referenced by tests.

## Carry-forward to Plans 2–3

Found by the whole-branch review; none of these is fixed on this branch.

1. `vastai` must be installed and authenticated on the VPS (watchdog + bot host) before anything can rent on Vast from the bot.
2. The first paid Vast session must confirm that `--label motion-transfer` survives `create` and comes back as `label` (and `id`) in `vastai show instances-v1 --raw` — all of tier 3's Vast authority rests on it.
3. `scripts/tgbot/run.py start_drain` must forward `PROVIDER=`; until then the bot cannot start a Vast run and `_do_kill`'s provider handling is untriggered in production.
4. `batch/vast-machines.json` must be added to `.gitignore` (spec §3.2 step 5) — **Done in Plan 2 (2026-09-19):** added to root `.gitignore`.
5. Plan 2's rent function must write `GPU_INSTANCE_ID` before the lease and never inherit a stale one (`pod-provision.sh` warns instead of dying when `vastai create` succeeds but the id cannot be parsed, and `drain.provision` then reads the previous `GPU_INSTANCE_ID` into a `provider=vast` lease). Every abandoned rent attempt must carry the label; use `--cancel-unavail` — **Done in Plan 2 (2026-09-19):** implemented in `scripts/vast_rent.py` (`rent()` handles deadline, retry, instance destruction, and blacklist); every `vastai create instance` call includes `--label motion-transfer --cancel-unavail`; `GPU_INSTANCE_ID` is parsed safely and never inherited across providers.
6. Known, deferred watchdog hazards:
   - (M4) `destroy_verified` destroys before it lists, so destroying an already-gone instance raises and the lease is never cleared (`drain_running` stays true and the bot refuses the next `/confirm`) — same shape on RunPod — **Done in Plan 2 (2026-09-19):** a watchdog that destroys a gone instance then re-lists counts it gone; `vastai_rent.py`'s `rent()` now verifies destruction by re-listing.
   - (M5) `VastCtl()` on a box without `vastai` logs one failure line per minute; rate-limit the message but keep the `"vast"` key in `apis`.
   - (M6) `POD_VOLUME_EFF` is empty when `.env` names no provider at all (already refused by `pod-provision.sh:158`); and the "tier 3: 0 pod(s) visible" line reads as "nothing is billing" when every provider failed to list.
7. Two parked Makefile-verify minors — **Done in Plan 2 (2026-09-19):** (a) `vastai show instances-v1` on an auth error returns exit 0 with non-listing text; `gpu-destroy`'s verify now matches `"instances": [` to catch both; (b) `vastai show instances-v1` output can contain whitespace in the `id` field, which `cut -d' '` would truncate; no longer parsed from Makefile, delegated to Python `VastCtl`.

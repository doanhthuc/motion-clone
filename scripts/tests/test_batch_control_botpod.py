"""The pod-side bot functions as the phone reaches them (spec §5.9): `_do_kill`
and `_start_migration` returning an `Outcome`, and `AppPod.kill` / `AppPod.resume`.

Everything here is free and nothing here can spend money: `subprocess.run`,
`subprocess.Popen`, `start_drain`, `start_phase_a`, `clear_lease` and the lease
readers are patched by name in `tgbot.bot`, so no test can reach `make
gpu-destroy`, `volume_migrate.py`, `runpodctl` or `vastai`. A test that could
reach one unpatched is a defect, not a slow test.
"""
import contextlib, itertools, json, subprocess, sys, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Not a package (there is no scripts/tests/__init__.py), so the sibling test
# module is imported by its bare name off this directory — which works both
# under `unittest discover -s scripts/tests` and under a direct module run.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from batchlib.manifest import state_path_for
from batchlib.config import env_get
from batchlib_ext.gpu_stock import Stock
from batchlib_ext.lease import Lease, read_lease, write_lease
from batchlib_ext.migrate_lease import MigrateLease, write_migrate_lease
from batchlib_ext.provision_failure import (ProvisionFailure, provision_failure_path,
                                            write_provision_failure)
from control.idempotency import IdempotencyStore
from control.runs import Outcome
import tgbot.bot as bot
import tgbot.run as run_mod
from tgbot.job import write_manifest

from test_batch_control_botruns import ME, _Fixture

KILL_PHASE_A_STOPPED = ("🛑 Stopped the try-on phase. Nothing was rented, and "
                        "Gemini calls already made are not refunded.")
KILL_PHASE_A_FINISHED = "the try-on phase had already finished — nothing to stop."
KILL_DESTROYING = "🛑 destroying the pod…"
KILL_DONE = "🛑 Killed. Pod destroyed and verified gone."
MIGRATE_ALREADY = "a volume migration is already in progress"
MIGRATE_LAUNCH_FAILED = ("could not start the migration — check the box. "
                         "Nothing was created.")


class _PodFixture(_Fixture):
    """`_Fixture` plus everything `_do_kill` would otherwise reach on the real
    box: the lease files and `make gpu-destroy` itself."""

    def setUp(self):
        super().setUp()
        for name, value in (("stop_phase_a", True), ("lease_for", None),
                            ("read_lease", None), ("clear_lease", None)):
            patcher = mock.patch(f"tgbot.bot.{name}", return_value=value)
            self.patches[name] = patcher.start()
            self.addCleanup(patcher.stop)
        # The one call that would destroy a real pod. Patched for every test in
        # this file, including the ones that assert it was NOT called — an
        # assertion about a call that could have been real is worth nothing.
        patcher = mock.patch("tgbot.bot.subprocess.run")
        self.run = patcher.start()
        self.run.return_value = subprocess.CompletedProcess(
            ["make", "gpu-destroy"], 0, stdout="", stderr="")
        self.addCleanup(patcher.stop)
        # `_something_live` resolves both predicates through run_mod, the way
        # `_busy_reason` does, so the app-side gate cannot disagree with busy().
        patcher = mock.patch("tgbot.run.phase_a_running", return_value=False)
        self.patches["run_mod.phase_a_running"] = patcher.start()
        self.addCleanup(patcher.stop)
        self.idem = IdempotencyStore(self.root / "batch" / "idempotency")
        self.pod = bot.AppPod(self.tg, ME, self.idem)

    def tearDown(self):
        thread = getattr(self.pod, "_kill_thread", None)
        if thread is not None:
            thread.join(5)
        bot._MIGRATE_PROC.pop(bot._MIGRATE_PROC_KEY, None)
        super().tearDown()

    def _phase_a_only(self) -> None:
        self.patches["phase_a_running"].return_value = True
        self.patches["drain_running"].return_value = False

    def _live_drain(self) -> None:
        self.patches["run_mod.drain_running"].return_value = True

    def _texts(self) -> list[str]:
        return [text for text, _ in self.tg.sent]


class TestDoKillOutcomes(_PodFixture):
    def test_do_kill_returns_outcomes(self):
        self._phase_a_only()
        out = bot._do_kill(self.tg, ME)
        self.assertEqual((out.ok, out.code), (True, "phase_a_stopped"))
        self.run.assert_not_called()

        self.patches["stop_phase_a"].return_value = False
        out = bot._do_kill(self.tg, ME)
        self.assertEqual((out.ok, out.code), (False, "phase_a_finished"))
        self.run.assert_not_called()

        self.patches["phase_a_running"].return_value = False
        self.patches["drain_running"].return_value = True
        out = bot._do_kill(self.tg, ME)
        self.assertEqual((out.ok, out.code), (True, "killed"))
        self.run.assert_called_once()

    def test_do_kill_unverified_destroy_is_not_ok(self):
        self.patches["drain_running"].return_value = True
        self.run.return_value = subprocess.CompletedProcess(
            ["make", "gpu-destroy"], 1, stdout="boom", stderr="")
        out = bot._do_kill(self.tg, ME)
        self.assertEqual((out.ok, out.code), (False, "destroy_unverified"))
        self.assertTrue(any("may not have worked" in text for text in self._texts()))
        # This message is what GET /v1/pod's last_kill hands the phone. It is
        # the one outcome where a pod may still be billing, so an empty or
        # vague message would hide the only thing the user has to act on.
        self.assertTrue(out.message)
        self.assertIn("billing", out.message)

    def test_do_kill_destroy_timeout_is_not_ok(self):
        self.patches["drain_running"].return_value = True
        self.run.side_effect = subprocess.TimeoutExpired(["make", "gpu-destroy"], 180)
        out = bot._do_kill(self.tg, ME)
        self.assertEqual((out.ok, out.code), (False, "destroy_unverified"))
        self.assertTrue(any("may not have worked" in text for text in self._texts()))

    def test_do_kill_telegram_messages_unchanged(self):
        """The literals as they were before the Outcome returns were added.
        Telegram behaviour does not change: these are outcomes the chat must
        see (a pod that may still be billing), not refusals."""
        self._phase_a_only()
        bot._do_kill(self.tg, ME)
        self.assertEqual(self.tg.sent, [(KILL_PHASE_A_STOPPED, {})])

        self.tg.sent.clear()
        self.patches["stop_phase_a"].return_value = False
        bot._do_kill(self.tg, ME)
        self.assertEqual(self.tg.sent, [(KILL_PHASE_A_FINISHED, {})])

        self.tg.sent.clear()
        self.patches["phase_a_running"].return_value = False
        self.patches["drain_running"].return_value = True
        bot._do_kill(self.tg, ME)
        self.assertEqual(self.tg.sent, [(KILL_DESTROYING, {}), (KILL_DONE, {})])

        self.tg.sent.clear()
        self.run.return_value = subprocess.CompletedProcess(
            ["make", "gpu-destroy"], 1, stdout="boom", stderr="")
        bot._do_kill(self.tg, ME)
        self.assertEqual(self.tg.sent[0], (KILL_DESTROYING, {}))
        text, kwargs = self.tg.sent[1]
        self.assertEqual(
            text,
            f"{bot.ICON_WARN} <b>gpu-destroy may not have worked</b> — check "
            f"manually, it may still be billing."
            f"\n<blockquote expandable>{bot._esc('boom')}</blockquote>")
        self.assertEqual(kwargs, {"parse_mode": bot.PARSE_HTML})


class TestStartMigrationOutcomes(_PodFixture):
    def test_already_running_is_a_refusal(self):
        self.patches["migration_running"].return_value = True
        with mock.patch("tgbot.bot.subprocess.Popen") as popen:
            out = bot._start_migration(self.tg, ME, "EU-CZ-1")
        self.assertEqual(out, Outcome(False, "migration", MIGRATE_ALREADY))
        self.assertEqual(self.tg.sent, [(MIGRATE_ALREADY, {})])
        popen.assert_not_called()

    def test_launch_failure_is_a_refusal_and_clears_the_marker(self):
        with mock.patch("tgbot.bot.subprocess.Popen", side_effect=OSError("nope")):
            out = bot._start_migration(self.tg, ME, "EU-CZ-1")
        self.assertEqual(out, Outcome(False, "launch_failed", MIGRATE_LAUNCH_FAILED))
        self.assertEqual(self.tg.sent, [(MIGRATE_LAUNCH_FAILED, {})])
        self.assertFalse(bot._migrate_launch_marker().exists())

    def test_success_writes_the_marker_before_popen(self):
        seen = {}

        def popen(*_args, **_kwargs):
            # The marker's whole point is to exist before the child does.
            seen["marker"] = bot._migrate_launch_marker().exists()
            return mock.Mock(pid=1234)

        with mock.patch("tgbot.bot.subprocess.Popen", side_effect=popen):
            out = bot._start_migration(self.tg, ME, "EU-CZ-1")
        self.assertEqual(out, Outcome(True, "started"))
        self.assertTrue(seen["marker"])
        self.assertTrue(any("Migration to EU-CZ-1 started" in text
                            for text in self._texts()))

    def test_app_call_keeps_the_refusal_off_telegram(self):
        self.patches["migration_running"].return_value = True
        out = bot._start_migration(bot._AppTg(self.tg), ME, "EU-CZ-1")
        self.assertEqual(out, Outcome(False, "migration", MIGRATE_ALREADY))
        self.assertEqual(self.tg.sent, [])


class TestAppPodKill(_PodFixture):
    def test_kill_refuses_when_nothing_is_running_and_never_destroys(self):
        status, body = self.pod.kill(self.pod.run_id, "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "nothing_running")
        # The reason this gate exists at all: _do_kill has no idle check of
        # its own and would destroy whatever pod .env happens to name.
        self.run.assert_not_called()
        self.assertIsNone(self.pod._kill_thread)

    def test_kill_wrong_run_id_is_404_and_does_not_touch_idempotency(self):
        status, body = self.pod.kill("not-the-run-id", "k1")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        self.assertEqual(list((self.root / "batch" / "idempotency").glob("*.json")), [])
        self.run.assert_not_called()

    def _blocking_kill(self):
        """A patched `_do_kill` that parks until the test lets it go."""
        release = threading.Event()
        entered = threading.Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return Outcome(True, "killed")

        patcher = mock.patch("tgbot.bot._do_kill", side_effect=blocked)
        do_kill = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(release.set)
        return do_kill, entered, release

    def test_kill_starts_a_worker_and_answers_202(self):
        self._live_drain()
        do_kill, entered, release = self._blocking_kill()
        status, body = self.pod.kill(self.pod.run_id, "k1")
        self.assertEqual(status, 202)
        self.assertEqual(body, {"run_id": self.pod.run_id, "outcome": "kill_started"})
        self.assertTrue(entered.wait(5))
        release.set()
        self.pod._kill_thread.join(5)
        do_kill.assert_called_once()
        self.assertEqual(self.pod.last_kill["ok"], True)
        self.assertEqual(self.pod.last_kill["code"], "killed")
        self.assertIsInstance(self.pod.last_kill["at"], float)

    def test_kill_worker_rechecks_under_the_lock(self):
        # Live at request time, finished by the time the worker holds the lock
        # — the seconds-wide window §5.9 names.
        self.patches["run_mod.drain_running"].side_effect = [True, False]
        with mock.patch("tgbot.bot._do_kill") as do_kill:
            status, _body = self.pod.kill(self.pod.run_id, "k1")
            self.assertEqual(status, 202)
            self.pod._kill_thread.join(5)
            do_kill.assert_not_called()
        self.run.assert_not_called()
        self.assertEqual(self.pod.last_kill["ok"], False)
        self.assertEqual(self.pod.last_kill["code"], "nothing_running")

    def test_second_kill_while_one_runs_is_409_kill_in_progress(self):
        """The window the guard is actually for: the worker thread is alive
        but has not taken BOT_LOCK yet (it has just been started, or is on its
        way out). Once it HOLDS the lock a second kill waits on the lock
        instead — that is the test below."""
        self._live_drain()
        release = threading.Event()
        alive = threading.Thread(target=release.wait, args=(5,), daemon=True)
        alive.start()
        self.pod._kill_thread = alive
        try:
            with mock.patch("tgbot.bot._do_kill") as do_kill:
                status, body = self.pod.kill(self.pod.run_id, "k2")
                do_kill.assert_not_called()
        finally:
            release.set()
            alive.join(5)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "kill_in_progress")
        self.run.assert_not_called()

    def test_second_kill_while_the_worker_holds_the_lock_is_503(self):
        self._live_drain()
        _do_kill, entered, release = self._blocking_kill()
        self.assertEqual(self.pod.kill(self.pod.run_id, "k1")[0], 202)
        self.assertTrue(entered.wait(5))
        with mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.2):
            status, body = self.pod.kill(self.pod.run_id, "k2")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "bot_busy")
        release.set()
        self.pod._kill_thread.join(5)

    def test_kill_replayed_with_same_key_starts_one_worker(self):
        self._live_drain()
        with mock.patch("tgbot.bot._do_kill",
                        return_value=Outcome(True, "killed")) as do_kill:
            first = self.pod.kill(self.pod.run_id, "k1")
            self.pod._kill_thread.join(5)
            second = self.pod.kill(self.pod.run_id, "k1")
        self.assertEqual(first, (202, {"run_id": self.pod.run_id,
                                       "outcome": "kill_started"}))
        self.assertEqual(second[0], 202)
        self.assertEqual(second[1], first[1])
        do_kill.assert_called_once()

    def test_kill_holds_bot_lock_while_it_runs(self):
        self._live_drain()
        _do_kill, entered, release = self._blocking_kill()
        self.assertEqual(self.pod.kill(self.pod.run_id, "k1")[0], 202)
        self.assertTrue(entered.wait(5))
        # RLock is per-thread reentrant, so the probe runs on its own thread.
        acquired = []

        def probe():
            got = bot.BOT_LOCK.acquire(blocking=False)
            acquired.append(got)
            if got:
                bot.BOT_LOCK.release()

        t = threading.Thread(target=probe)
        t.start(); t.join(5)
        self.assertEqual(acquired, [False])
        release.set()
        self.pod._kill_thread.join(5)

    def test_pod_is_readable_while_a_kill_runs_and_says_so(self):
        self._live_drain()
        _do_kill, entered, release = self._blocking_kill()
        self.assertEqual(self.pod.kill(self.pod.run_id, "k1")[0], 202)
        self.assertTrue(entered.wait(5))
        status, body = self.pod.pod()
        self.assertEqual(status, 200)
        self.assertIs(body["kill_running"], True)
        self.assertIsNone(body["last_kill"])
        release.set()
        self.pod._kill_thread.join(5)
        status, body = self.pod.pod()
        self.assertEqual(status, 200)
        self.assertIs(body["kill_running"], False)
        self.assertIsNotNone(body["last_kill"])

    def test_a_worker_exception_is_recorded_not_swallowed(self):
        self._live_drain()
        with mock.patch("tgbot.bot._do_kill", side_effect=RuntimeError("boom")):
            self.assertEqual(self.pod.kill(self.pod.run_id, "k1")[0], 202)
            self.pod._kill_thread.join(5)
        self.assertEqual(self.pod.last_kill["ok"], False)
        self.assertEqual(self.pod.last_kill["code"], "error")


class TestAppPodResume(_PodFixture):
    def setUp(self):
        super().setUp()
        self.job = self._job("app")
        write_manifest([self.job], self._live(),
                       now=time.strftime("%Y-%m-%d %H:%M:%S"))
        state_path_for(self._live()).write_text(
            json.dumps({"batch": "2026-09-22-1200", "runs": {}}), encoding="utf-8")

    def _seed_failure(self) -> Path:
        path = provision_failure_path(self._live())
        write_provision_failure(path, ProvisionFailure(
            gpu="NVIDIA GeForce RTX 5090", datacenter="EU-RO-1",
            stock_out=True, detail="no instances available"))
        return path

    def _body(self, **over) -> dict:
        body = {"provider": "runpod", "run_token": bot._run_token(ME)}
        body.update(over)
        return body

    def test_resume_requires_an_outstanding_provision_failure(self):
        with mock.patch("tgbot.bot._do_resume") as do_resume:
            status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "no_failure")
        do_resume.assert_not_called()
        self.patches["start_drain"].assert_not_called()

    def test_resume_stale_run_token_is_409_stale_run(self):
        self._seed_failure()
        status, body = self.pod.resume(self.pod.run_id,
                                       self._body(run_token="0"), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "stale_run")
        self.patches["start_drain"].assert_not_called()

    def test_resume_bad_provider_is_400(self):
        self._seed_failure()
        status, body = self.pod.resume(self.pod.run_id,
                                       self._body(provider="nope"), "k1")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")
        self.patches["start_drain"].assert_not_called()

    def test_resume_without_a_run_token_is_400(self):
        self._seed_failure()
        body = self._body()
        del body["run_token"]
        status, resp = self.pod.resume(self.pod.run_id, body, "k1")
        self.assertEqual(status, 400)
        self.assertEqual(resp["error"]["code"], "bad_request")
        self.patches["start_drain"].assert_not_called()

    def test_resume_wrong_id_is_404(self):
        self._seed_failure()
        status, body = self.pod.resume("not-the-run-id", self._body(), "k1")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        self.assertEqual(list((self.root / "batch" / "idempotency").glob("*.json")), [])
        self.patches["start_drain"].assert_not_called()

    def test_resume_starts_once_and_replays(self):
        failure = self._seed_failure()
        first = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(first, (202, {"run_id": self.pod.run_id, "outcome": "started"}))
        self.patches["start_drain"].assert_called_once()
        self.assertEqual(self.patches["start_drain"].call_args.kwargs["gpu_provider"],
                         "runpod")
        # _do_resume clears the failure itself; the replay must not re-create
        # the conditions for a second rental.
        self.assertFalse(failure.exists())
        second = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(second, first)
        self.patches["start_drain"].assert_called_once()

    def test_resume_with_a_different_gpu_is_stale_panel_and_spends_nothing(self):
        (self.root / ".env").write_text("GPU=NVIDIA GeForce RTX 4090\n", encoding="utf-8")
        failure = self._seed_failure()
        status, body = self.pod.resume(
            self.pod.run_id, self._body(gpu="NVIDIA GeForce RTX 5090"), "k-mismatch")
        self.assertEqual((status, body["error"]["code"]), (409, "stale_panel"))
        self.patches["start_drain"].assert_not_called()
        self.assertTrue(failure.exists())

        # A matching gpu and an omitted one both go through as before.
        status, _ = self.pod.resume(
            self.pod.run_id, self._body(gpu="NVIDIA GeForce RTX 4090"), "k-match")
        self.assertEqual(status, 202)
        self.assertEqual(self.patches["start_drain"].call_count, 1)
        self._seed_failure()
        status, _ = self.pod.resume(self.pod.run_id, self._body(), "k-omitted")
        self.assertEqual(status, 202)
        self.assertEqual(self.patches["start_drain"].call_count, 2)

    def test_resume_refusal_is_returned_not_sent_to_telegram(self):
        self._seed_failure()
        self.patches["migration_running"].return_value = True
        status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "migration")
        self.assertEqual(self.tg.sent, [])
        self.patches["start_drain"].assert_not_called()

    def test_resume_bot_busy_is_503_and_forgets_the_key(self):
        self._seed_failure()
        held, release = threading.Event(), threading.Event()

        def hold():
            with bot.BOT_LOCK:
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.2):
                status, body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        finally:
            release.set()
            t.join(5)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "bot_busy")
        # Forgotten, not recorded: the same key may simply be retried.
        status, _body = self.pod.resume(self.pod.run_id, self._body(), "k1")
        self.assertEqual(status, 202)


P5090 = "NVIDIA GeForce RTX 5090"
P4090 = "NVIDIA GeForce RTX 4090"
PL40S = "NVIDIA L40S"


def _fake_stock() -> dict:
    """One stock answer, shared by the JSON data function and the Telegram
    report, so the parity test compares the two on identical input. The 5090
    is at home (Medium) and in four other regions; the L40S is only elsewhere
    and has no price; every other card is absent (sold out everywhere)."""
    def s(gpu, name, price, dc, status):
        return Stock(gpu_id=gpu, display_name=name, price_per_hr=price,
                     datacenter_id=dc, stock_status=status)
    return {
        P5090: [s(P5090, "RTX 5090", 0.99, "EU-RO-1", "Medium"),
                s(P5090, "RTX 5090", 0.99, "EU-CZ-1", "Low"),
                s(P5090, "RTX 5090", 1.05, "EUR-IS-1", "High"),
                s(P5090, "RTX 5090", 0.99, "US-KS-2", "none"),
                s(P5090, "RTX 5090", 0.99, "EU-NL-1", "Medium")],
        PL40S: [s(PL40S, "L40S", None, "EU-CZ-1", "Medium")],
    }


@contextlib.contextmanager
def _bot_lock_held_elsewhere():
    """BOT_LOCK is an RLock, so this thread taking it would always succeed:
    the holder has to be another thread for a test to prove anything."""
    held, release = threading.Event(), threading.Event()

    def hold():
        with bot.BOT_LOCK:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        yield
    finally:
        release.set()
        t.join(5)


def _lock_free_from_another_thread() -> bool:
    got = []

    def probe():
        ok = bot.BOT_LOCK.acquire(blocking=False)
        got.append(ok)
        if ok:
            bot.BOT_LOCK.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(5)
    return got[0]


class TestGpuStock(_PodFixture):
    def setUp(self):
        super().setUp()
        (self.root / ".env").write_text(
            f"GPU={P4090}\nPOD_VOLUME_ID=vol-1\n", encoding="utf-8")

    def test_gpu_stock_data_matches_report_gpu_stock(self):
        """The JSON is a twin of _report_gpu_stock, not a caller of it, so this
        is the only thing that stops the two drifting apart."""
        stock = _fake_stock()
        self.patches["stock_at_cached"].return_value = stock
        data = bot._gpu_stock_data(force=False)
        bot._report_gpu_stock(self.tg, ME)
        [(html, _)] = self.tg.sent
        lines = [line.strip() for line in bot._plain(html).splitlines()]

        self.assertEqual(data["selected"], P4090)
        self.assertEqual(data["home_datacenter"], "EU-RO-1")
        self.assertIn(f"Currently selected: {P4090}", " ".join(lines))
        self.assertTrue(any("EU-RO-1" in line and "your volume" in line for line in lines))

        def price_text(price):
            return f"${price:.2f}/h" if price else "?"

        self.assertEqual([g["gpu"] for g in data["gpus"]],
                         [bot._PRIMARY_GPU_ID, *bot._FALLBACK_GPU_IDS])
        for gpu in data["gpus"]:
            entries = stock.get(gpu["gpu"])
            if not entries:
                self.assertTrue(gpu["sold_out_everywhere"])
                self.assertIsNone(gpu["home"])
                self.assertIsNone(gpu["usd_per_hr"])
                self.assertIn(f"{gpu['name']}: 🔴 sold out everywhere", lines)
                continue
            self.assertFalse(gpu["sold_out_everywhere"])
            self.assertEqual(gpu["name"], entries[0].display_name)
            self.assertEqual(gpu["usd_per_hr"], entries[0].price_per_hr or None)
            [row] = [l for l in lines if l.startswith(gpu["name"] + ":")
                     or l.startswith(gpu["name"] + " ·")]
            self.assertTrue(row.endswith(price_text(entries[0].price_per_hr)), row)
            home = next((e for e in entries if e.datacenter_id == "EU-RO-1"), None)
            if home is None:
                self.assertIsNone(gpu["home"])
            else:
                self.assertEqual(gpu["home"], {"stock": home.stock_status})
                self.assertIn(home.stock_status, row)

        # Other regions: same entries, same order, same two-per-GPU cut.
        start = next(i for i, l in enumerate(lines) if l.startswith("Other regions"))
        rows = [l for l in lines[start + 1:] if l]
        self.assertEqual(len(rows), len(data["other_regions"]))
        for row, other in zip(rows, data["other_regions"]):
            self.assertTrue(row.startswith(f"{other['name']} — {other['datacenter']}:"), row)
            self.assertIn(other["stock"], row)
            self.assertTrue(row.endswith(price_text(other["usd_per_hr"])), row)
        # Two per GPU, best stock first: High before Medium, Low cut.
        self.assertEqual(
            [(o["gpu"], o["datacenter"], o["stock"]) for o in data["other_regions"]],
            [(P5090, "EUR-IS-1", "High"), (P5090, "EU-NL-1", "Medium"),
             (PL40S, "EU-CZ-1", "Medium")])
        self.assertIsNone(data["other_regions"][2]["usd_per_hr"])

    def test_gpu_stock_answers_200_with_the_data(self):
        self.patches["stock_at_cached"].return_value = _fake_stock()
        status, body = self.pod.gpu_stock(False)
        self.assertEqual(status, 200)
        self.assertEqual(body, bot._gpu_stock_data(force=False))
        self.assertNotIn("<", json.dumps(body))

    def test_gpu_stock_runpodctl_failure_is_502_upstream_unavailable(self):
        self.patches["stock_at_cached"].side_effect = RuntimeError("runpodctl down")
        status, body = self.pod.gpu_stock(False)
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "upstream_unavailable")
        self.assertIn("runpodctl down", body["error"]["message"])

    def test_gpu_stock_force_uses_live_check(self):
        with mock.patch("tgbot.bot.stock_at", return_value=_fake_stock()) as live:
            self.pod.gpu_stock(True)
            live.assert_called_once()
            self.patches["stock_at_cached"].assert_not_called()
        with mock.patch("tgbot.bot.stock_at", return_value=_fake_stock()) as live:
            self.pod.gpu_stock(False)
            live.assert_not_called()
            self.patches["stock_at_cached"].assert_called_once()

    def test_gpu_stock_runs_outside_the_bot_lock(self):
        seen = []

        def probing_stock(*_args, **_kwargs):
            seen.append(_lock_free_from_another_thread())
            return _fake_stock()

        self.patches["stock_at_cached"].side_effect = probing_stock
        status, _ = self.pod.gpu_stock(False)
        self.assertEqual((status, seen), (200, [True]))

    def test_gpu_stock_does_not_take_the_lock_at_all(self):
        # A kill worker holds BOT_LOCK for minutes; the stock read is not bot
        # state and must not queue behind it.
        self.patches["stock_at_cached"].return_value = _fake_stock()
        with _bot_lock_held_elsewhere(), \
             mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.05):
            status, _ = self.pod.gpu_stock(False)
        self.assertEqual(status, 200)


class TestBalance(_PodFixture):
    def _balance(self, usd: float, *, vast: bool = False, vast_credit=25.0):
        with mock.patch("tgbot.bot.account_balance", return_value=usd), \
             mock.patch("tgbot.bot.vast_credit", return_value=vast_credit) as credit:
            result = self.pod.balance(vast)
        return result, credit

    def test_balance_runway_and_low_flag(self):
        # The fixture's stock check answers {}, so the price is _gpu_price's
        # own 0.99 fallback, the same number the [Run] button quotes.
        (status, body), _ = self._balance(40.0)
        self.assertEqual(status, 200)
        self.assertEqual(body["runpod"]["usd"], 40.0)
        self.assertEqual(body["runpod"]["usd_per_hr"], 0.99)
        self.assertAlmostEqual(body["runpod"]["runway_hours"], 40.0 / 0.99, places=2)
        self.assertFalse(body["runpod"]["low_runway"])
        self.assertEqual(body["errors"], [])

        (status, body), _ = self._balance(0.50)
        self.assertTrue(body["runpod"]["low_runway"])

    def test_balance_low_flag_agrees_with_the_telegram_report(self):
        for usd in (0.50, 40.0):
            self.tg.sent.clear()
            with mock.patch("tgbot.bot.account_balance", return_value=usd):
                bot._report_balance(self.tg, ME)
                _, body = self.pod.balance(False)
            [(html, _)] = self.tg.sent
            self.assertEqual("Under 1h" in bot._plain(html), body["runpod"]["low_runway"])

    def test_balance_vast_only_when_asked(self):
        (_, body), credit = self._balance(40.0)
        credit.assert_not_called()
        self.assertNotIn("vast", body)
        (_, body), credit = self._balance(40.0, vast=True)
        credit.assert_called_once()
        self.assertEqual(body["vast"], {"usd": 25.0})

    def test_balance_vast_failure_is_soft(self):
        with mock.patch("tgbot.bot.account_balance", return_value=40.0), \
             mock.patch("tgbot.bot.vast_credit", side_effect=RuntimeError("vastai down")):
            status, body = self.pod.balance(True)
        self.assertEqual(status, 200)
        self.assertEqual(body["vast"], {"usd": None})
        self.assertEqual(body["runpod"]["usd"], 40.0)
        self.assertTrue(any("vastai down" in e for e in body["errors"]))

    def test_balance_runpodctl_failure_is_200_with_error_not_500(self):
        with mock.patch("tgbot.bot.account_balance",
                        side_effect=RuntimeError("could not run <b>runpodctl</b>")):
            status, body = self.pod.balance(False)
        self.assertEqual(status, 200)
        self.assertIsNone(body["runpod"])
        [error] = body["errors"]
        self.assertIn("runpodctl", error)
        self.assertNotIn("<", error)

    def test_balance_network_runs_outside_the_lock_and_never_takes_it(self):
        seen = []

        def probing_balance():
            seen.append(_lock_free_from_another_thread())
            return 40.0

        with mock.patch("tgbot.bot.account_balance", side_effect=probing_balance):
            self.assertEqual(self.pod.balance(False)[0], 200)
        self.assertEqual(seen, [True])
        with _bot_lock_held_elsewhere(), \
             mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.05), \
             mock.patch("tgbot.bot.account_balance", return_value=40.0):
            self.assertEqual(self.pod.balance(False)[0], 200)


class TestPodState(_PodFixture):
    IDLE_MIGRATION = {"running": False, "phase": None, "to_dc": None,
                      "started_at": None, "bytes_copied": None, "total_bytes": None}

    def _real_leases(self) -> Path:
        """Undo the fixture's lease stubs so the lease files on disk are what
        is read, with both lease paths pointed at the temp root."""
        path = self.root / "batch" / "pod-lease.json"
        for target, value in (("tgbot.run.LEASE_PATH", path), ("tgbot.bot.LEASE_PATH", path)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.patches["lease_for"].side_effect = run_mod.lease_for
        self.patches["read_lease"].side_effect = read_lease
        return path

    def test_pod_reports_idle(self):
        status, body = self.pod.pod()
        self.assertEqual(status, 200)
        self.assertEqual(body, {
            "run_id": f"tg-{ME}", "gpu": bot._PRIMARY_GPU_ID, "lease": None,
            "migration": self.IDLE_MIGRATION, "kill_running": False,
            "last_kill": None, "failed_rental": None})

    def test_pod_gpu_is_the_env_gpu(self):
        (self.root / ".env").write_text(f"GPU={P4090}\n", encoding="utf-8")
        self.assertEqual(self.pod.pod()[1]["gpu"], P4090)

    def test_pod_reports_lease_migration_failed_rental_last_kill(self):
        lease_path = self._real_leases()
        write_lease(lease_path, Lease(pod_id="p1", provisioned_at=1_800_000_000.0,
                                      manifest=str(self._live()), abs_max_min=120,
                                      provider="runpod"))
        write_migrate_lease(self.root / "batch" / "volume-migrate-lease.json",
                            MigrateLease(pod_a_id="a", pod_b_id="b",
                                         started_at=1_800_000_500.0, to_dc="EU-CZ-1"))
        (self.root / "batch" / "volume-migrate.progress.json").write_text(json.dumps({
            "phase": "sync", "at": 1_800_000_900.0, "started_at": 1_800_000_600.0,
            "total_bytes": 100, "bytes_copied": 40}), encoding="utf-8")
        self.patches["migration_running"].return_value = True
        write_provision_failure(provision_failure_path(self._live()), ProvisionFailure(
            gpu=P5090, datacenter="EU-RO-1", stock_out=True, detail="no instances"))
        self.pod.last_kill = {"at": 1.0, "ok": True, "code": "killed", "message": "done"}

        status, body = self.pod.pod()
        self.assertEqual(status, 200)
        self.assertEqual(body["lease"], {
            "provider": "runpod", "provisioned_at": 1_800_000_000.0, "abs_max_min": 120,
            "quoted_usd_per_hr": 0.99, "run_id": f"tg-{ME}"})
        self.assertEqual(body["migration"], {
            "running": True, "phase": "sync", "to_dc": "EU-CZ-1",
            "started_at": 1_800_000_600.0, "bytes_copied": 40, "total_bytes": 100})
        self.assertEqual(body["failed_rental"], {
            "gpu": P5090, "datacenter": "EU-RO-1", "stock_out": True,
            "detail": "no instances"})
        self.assertEqual(body["last_kill"],
                         {"at": 1.0, "ok": True, "code": "killed", "message": "done"})

    def test_pod_lease_falls_back_to_the_global_file_for_a_chained_link(self):
        # drain.py rewrites lease.manifest to the claimed link, so
        # lease_for(this manifest) is None while a pod is still live.
        lease_path = self._real_leases()
        write_lease(lease_path, Lease(pod_id="p1", provisioned_at=5.0,
                                      manifest=str(self.root / "batch" / "other.yaml"),
                                      abs_max_min=90, provider="vast"))
        lease = self.pod.pod()[1]["lease"]
        self.assertEqual((lease["provider"], lease["run_id"], lease["quoted_usd_per_hr"]),
                         ("vast", "other", None))

    def test_pod_migration_phase_survives_when_no_lease_exists_yet(self):
        # The window between the launch marker and volume_migrate.py's own
        # lease: the destination comes from the marker.
        self.patches["migration_running"].return_value = True
        (self.root / "batch" / "volume-migrate.launching.json").write_text(
            json.dumps({"at": 1.0, "to_dc": "EUR-IS-1"}), encoding="utf-8")
        migration = self.pod.pod()[1]["migration"]
        self.assertEqual((migration["running"], migration["phase"], migration["to_dc"]),
                         (True, None, "EUR-IS-1"))

    def test_pod_makes_no_network_call(self):
        def boom(name):
            return mock.patch(f"tgbot.bot.{name}",
                              side_effect=AssertionError(f"{name} called from pod()"))

        with boom("stock_at"), boom("stock_at_cached"), boom("volume_datacenter"), \
             boom("account_balance"), boom("vast_credit"), \
             mock.patch("tgbot.bot.subprocess.Popen",
                        side_effect=AssertionError("Popen called from pod()")):
            self.run.side_effect = AssertionError("subprocess.run called from pod()")
            status, _ = self.pod.pod()
        self.assertEqual(status, 200)

    def test_pod_body_is_stable_between_calls(self):
        # The ETag-safety property (spec 5.3): with a lease, a migration and a
        # failed rental all present, nothing in the body may move with the
        # clock, or If-None-Match never gets a 304.
        lease_path = self._real_leases()
        write_lease(lease_path, Lease(pod_id="p1", provisioned_at=1_800_000_000.0,
                                      manifest=str(self._live()), abs_max_min=120))
        (self.root / "batch" / "volume-migrate.progress.json").write_text(
            json.dumps({"phase": "sync", "started_at": 1.0}), encoding="utf-8")
        self.patches["migration_running"].return_value = True
        write_provision_failure(provision_failure_path(self._live()), ProvisionFailure(
            gpu=P5090, datacenter=None, stock_out=False, detail="x"))
        ticking = itertools.count(1_900_000_000.0, 7.0)
        with mock.patch("tgbot.bot.time.time", side_effect=lambda: next(ticking)):
            first = self.pod.pod()
            second = self.pod.pod()
        self.assertEqual(first, second)

    def test_pod_names_no_absolute_path(self):
        lease_path = self._real_leases()
        write_lease(lease_path, Lease(pod_id="p1", provisioned_at=1.0,
                                      manifest=str(self._live()), abs_max_min=60))
        write_provision_failure(provision_failure_path(self._live()), ProvisionFailure(
            gpu=P5090, datacenter="EU-RO-1", stock_out=False,
            detail=f"failed in {self.root}/batch/tg.yaml <b>badly</b>"))
        self.pod.last_kill = {"at": 1.0, "ok": False, "code": "error",
                              "message": f"see {self.root}/x"}
        text = json.dumps(self.pod.pod()[1])
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("<b>", text)

    def test_pod_answers_while_the_bot_lock_is_held(self):
        # The phone polls this route to learn a kill finished, and the kill
        # worker holds BOT_LOCK for the whole destroy: behind the lock the
        # poll would be a 503 for exactly as long as the answer is wanted.
        with _bot_lock_held_elsewhere(), mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.05):
            status, body = self.pod.pod()
        self.assertEqual(status, 200)
        self.assertIs(body["kill_running"], False)


class TestSetGpu(_PodFixture):
    ENV = f"OTHER=1\nGPU={P5090}\n"

    def setUp(self):
        super().setUp()
        self.env = self.root / ".env"
        self.env.write_text(self.ENV, encoding="utf-8")

    def test_set_gpu_writes_env_and_rejects_unknown(self):
        for bad in ({"gpu": "NVIDIA H100"}, {"gpu": "5090"}, {"gpu": None}, {},
                    {"gpu": ["x"]}, {"gpu": 5}):
            status, body = self.pod.set_gpu(bad)
            self.assertEqual((status, body["error"]["code"]), (400, "bad_request"), bad)
            self.assertEqual(self.env.read_text(encoding="utf-8"), self.ENV)

        status, body = self.pod.set_gpu({"gpu": P4090})
        self.assertEqual(status, 200)
        self.assertEqual(body["gpu"], P4090)
        self.assertEqual(env_get(self.env, "GPU"), P4090)
        self.assertEqual(env_get(self.env, "OTHER"), "1")

    def test_set_gpu_takes_the_bot_lock(self):
        with _bot_lock_held_elsewhere(), mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.05):
            status, body = self.pod.set_gpu({"gpu": P4090})
        self.assertEqual((status, body["error"]["code"]), (503, "bot_busy"))
        self.assertEqual(self.env.read_text(encoding="utf-8"), self.ENV)


class TestMigrate(_PodFixture):
    """The two-step migration as the phone reaches it (spec §5.9).

    The real `volume_migrate.py` DELETES the source Network Volume, so this
    class patches both `_start_migration` and `subprocess.Popen`: the Popen
    stub raises if anything ever reaches it, and every refusal test asserts
    neither was called. A refusal proved against an unpatched launcher would
    be worth nothing.
    """

    ENV = "POD_VOLUME_ID=vol-1\n"

    def setUp(self):
        super().setUp()
        self.env = self.root / ".env"
        self.env.write_text(self.ENV, encoding="utf-8")
        # EU-CZ-1 (Low) and EUR-IS-1 / EU-NL-1 are offered; US-KS-2 is listed
        # with stock "none"; EU-RO-1 is home.
        self.patches["stock_at_cached"].return_value = _fake_stock()
        patcher = mock.patch("tgbot.bot._start_migration",
                             return_value=Outcome(True, "started"))
        self.start = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "tgbot.bot.subprocess.Popen",
            side_effect=AssertionError("volume_migrate.py must never be launched"))
        self.popen = patcher.start()
        self.addCleanup(patcher.stop)

    def _ask(self, to_dc="EU-CZ-1"):
        return self.pod.migrate_ask({"to_dc": to_dc})

    def _token(self, to_dc="EU-CZ-1") -> str:
        status, body = self._ask(to_dc)
        self.assertEqual(status, 200)
        return body["confirm_token"]

    def _nothing_started(self):
        self.start.assert_not_called()
        self.popen.assert_not_called()

    def _live_lease(self):
        self.patches["read_lease"].return_value = Lease(
            pod_id="p1", provisioned_at=1.0, manifest=str(self._live()),
            abs_max_min=120, provider="runpod")

    # ---- ask ----------------------------------------------------------

    def test_ask_bad_to_dc_is_400_and_stores_nothing(self):
        for bad in ({}, {"to_dc": None}, {"to_dc": ""}, {"to_dc": 5},
                    {"to_dc": ["EU-CZ-1"]}, "not-a-dict"):
            status, body = self.pod.migrate_ask(bad)
            self.assertEqual((status, body["error"]["code"]), (400, "bad_request"), bad)
            self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_while_a_migration_is_running(self):
        self.patches["migration_running"].return_value = True
        status, body = self._ask()
        self.assertEqual((status, body["error"]["code"]), (409, "migration"))
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_while_a_lease_is_live(self):
        # Telegram lets a migration start under a live drain; the phone must
        # not — the volume being copied is the one the pod is reading.
        self._live_lease()
        status, body = self._ask()
        self.assertEqual((status, body["error"]["code"]), (409, "run_active"))
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_while_the_chats_run_is_busy(self):
        self.patches["busy"].return_value = True
        status, body = self._ask()
        self.assertEqual((status, body["error"]["code"]), (409, "run_active"))
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_the_home_datacenter(self):
        status, body = self._ask("EU-RO-1")
        self.assertEqual((status, body["error"]["code"]), (409, "same_datacenter"))
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_a_datacenter_the_stock_check_does_not_list(self):
        for to_dc in ("XX-YY-1", "US-KS-2"):   # unknown, and listed as "none"
            status, body = self._ask(to_dc)
            self.assertEqual((status, body["error"]["code"]),
                             (409, "unknown_datacenter"), to_dc)
            self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_fails_closed_when_the_stock_check_is_down(self):
        # Fail CLOSED: with no stock answer there is no evidence the
        # destination exists, and this is the one operation that deletes data.
        self.patches["stock_at_cached"].side_effect = RuntimeError("runpodctl down")
        status, body = self._ask()
        self.assertEqual((status, body["error"]["code"]), (502, "upstream_unavailable"))
        self.assertIn("runpodctl down", body["error"]["message"])
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_refuses_when_the_home_datacenter_is_unknown(self):
        self.patches["volume_datacenter"].return_value = None
        status, body = self._ask()
        self.assertEqual((status, body["error"]["code"]), (409, "home_unknown"))
        self.assertIsNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_ask_returns_a_token_and_the_warning_without_html_or_paths(self):
        status, body = self._ask()
        self.assertEqual(status, 200)
        self.assertEqual(body["to_dc"], "EU-CZ-1")
        self.assertEqual(body["home_datacenter"], "EU-RO-1")
        self.assertEqual(body["expires_in_sec"], 600)
        self.assertTrue(body["confirm_token"])
        warning = body["warning"]
        self.assertIn("deletes the current volume", warning)
        self.assertIn("Cannot be undone", warning)
        self.assertIn("EU-CZ-1", warning)
        self.assertIn(bot.MIGRATE_DURATION_PLAIN, warning)
        self.assertNotIn("<", warning)
        self.assertNotIn(str(self.root), json.dumps(body))
        record = self.pod._migrate_ask
        self.assertEqual((record["to_dc"], record["volume_id"], record["token"]),
                         ("EU-CZ-1", "vol-1", body["confirm_token"]))
        self._nothing_started()

    def test_ask_network_runs_outside_the_bot_lock(self):
        # A runpodctl round trip held under BOT_LOCK would stall every
        # Telegram update for as long as it runs (_rent_panel_data's reason).
        seen = []

        def probing(*_args, **_kwargs):
            seen.append(_lock_free_from_another_thread())
            return _fake_stock()

        def probing_home(*_args, **_kwargs):
            seen.append(_lock_free_from_another_thread())
            return "EU-RO-1"

        self.patches["stock_at_cached"].side_effect = probing
        self.patches["volume_datacenter"].side_effect = probing_home
        status, _ = self._ask()
        self.assertEqual((status, seen), (200, [True, True]))

    def test_a_new_ask_replaces_the_old_token(self):
        first = self._token()
        second = self._token("EUR-IS-1")
        self.assertNotEqual(first, second)
        self.assertEqual(self.pod._migrate_ask["token"], second)
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": first}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "bad_confirm_token"))
        self._nothing_started()

    # ---- go -----------------------------------------------------------

    def test_go_bad_body_is_400(self):
        self._token()
        for bad in ({}, {"to_dc": "EU-CZ-1"}, {"confirm_token": "x"},
                    {"to_dc": 5, "confirm_token": "x"},
                    {"to_dc": "EU-CZ-1", "confirm_token": 5}):
            status, body = self.pod.migrate(bad, "m-bad")
            self.assertEqual((status, body["error"]["code"]), (400, "bad_request"), bad)
        self._nothing_started()

    def test_go_without_a_prior_ask_is_bad_confirm_token(self):
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": "made-up"}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "bad_confirm_token"))
        self._nothing_started()

    def test_go_with_the_wrong_token_or_the_wrong_datacenter_is_refused(self):
        token = self._token()
        for body_in, key in (({"to_dc": "EU-CZ-1", "confirm_token": token + "x"}, "m1"),
                             ({"to_dc": "EUR-IS-1", "confirm_token": token}, "m2")):
            status, body = self.pod.migrate(body_in, key)
            self.assertEqual((status, body["error"]["code"]),
                             (409, "bad_confirm_token"), body_in)
        # Still stored: a wrong guess must not burn the real confirmation.
        self.assertIsNotNone(self.pod._migrate_ask)
        self._nothing_started()

    def test_go_with_an_expired_token_is_refused(self):
        token = self._token()
        later = time.monotonic() + 601
        with mock.patch("tgbot.bot.time.monotonic", return_value=later):
            status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                             "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "bad_confirm_token"))
        self._nothing_started()

    def test_go_after_the_volume_id_changed_is_refused(self):
        # The token is bound to the volume it was shown for. If .env now names
        # a different volume, the ask's warning described a different deletion.
        token = self._token()
        self.env.write_text("POD_VOLUME_ID=vol-2\n", encoding="utf-8")
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "bad_confirm_token"))
        self._nothing_started()

    def test_go_after_a_migration_started_is_refused(self):
        token = self._token()
        self.patches["migration_running"].return_value = True
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "migration"))
        self._nothing_started()

    def test_go_after_a_drain_went_live_is_refused(self):
        token = self._token()
        self._live_lease()
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "run_active"))
        self._nothing_started()

    def test_go_makes_no_network_call(self):
        # Every re-check under the lock reads files, .env or memory. A
        # runpodctl round trip here would hold BOT_LOCK for its duration.
        token = self._token()
        self.patches["stock_at_cached"].side_effect = AssertionError("stock from migrate()")
        self.patches["volume_datacenter"].side_effect = AssertionError("home from migrate()")
        status, _ = self.pod.migrate({"to_dc": "EU-CZ-1", "confirm_token": token}, "m1")
        self.assertEqual(status, 202)

    def test_go_consumes_the_token_and_starts_exactly_once(self):
        token = self._token()
        first = self.pod.migrate({"to_dc": "EU-CZ-1", "confirm_token": token}, "m1")
        self.assertEqual(first, (202, {"outcome": "started", "to_dc": "EU-CZ-1"}))
        self.start.assert_called_once()
        tg_arg, chat_arg, dc_arg = self.start.call_args.args
        self.assertIsInstance(tg_arg, bot._AppTg)
        self.assertIs(tg_arg._tg, self.tg)
        self.assertEqual((chat_arg, dc_arg), (ME, "EU-CZ-1"))
        # Popped before the launch, so a crash inside _start_migration cannot
        # leave a token that would start a second, concurrent migration.
        self.assertIsNone(self.pod._migrate_ask)

        # A retry of the same request replays; a fresh key does not get a
        # second migration out of the same single-use token.
        self.assertEqual(
            self.pod.migrate({"to_dc": "EU-CZ-1", "confirm_token": token}, "m1"), first)
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": token}, "m2")
        self.assertEqual((status, body["error"]["code"]), (409, "bad_confirm_token"))
        self.start.assert_called_once()
        self.popen.assert_not_called()

    def test_go_refusal_from_start_migration_is_returned_not_sent(self):
        token = self._token()
        self.start.return_value = Outcome(False, "launch_failed",
                                          MIGRATE_LAUNCH_FAILED)
        status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                         "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (409, "launch_failed"))
        self.assertEqual(body["error"]["message"], MIGRATE_LAUNCH_FAILED)
        self.assertEqual(self.tg.sent, [])

    def test_go_bot_busy_is_503_and_forgets_the_key(self):
        token = self._token()
        with _bot_lock_held_elsewhere(), \
             mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.05):
            status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                             "confirm_token": token}, "m1")
        self.assertEqual((status, body["error"]["code"]), (503, "bot_busy"))
        self._nothing_started()
        # Forgotten, not recorded: the same key may simply be retried.
        status, _ = self.pod.migrate({"to_dc": "EU-CZ-1",
                                      "confirm_token": token}, "m1")
        self.assertEqual(status, 202)
        self.start.assert_called_once()

    def test_go_with_a_non_ascii_token_is_400_and_leaves_no_pending_record(self):
        # hmac.compare_digest raises TypeError on a non-ASCII str. Refused
        # before the idempotency record exists, so the key stays usable
        # instead of answering `outcome_unknown` for a migration that never
        # began.
        token = self._token()
        for bad in ("tok\u00e9n", "\ud800"):
            status, body = self.pod.migrate({"to_dc": "EU-CZ-1",
                                             "confirm_token": bad}, "m-bad")
            self.assertEqual((status, body["error"]["code"]), (400, "bad_request"), bad)
        self._nothing_started()
        # The same key, now with the real token, is not stuck `pending`.
        status, _ = self.pod.migrate({"to_dc": "EU-CZ-1", "confirm_token": token}, "m-bad")
        self.assertEqual(status, 202)
        self.start.assert_called_once()

    def test_two_threads_racing_one_token_start_one_migration(self):
        # Check-and-pop is one critical section under BOT_LOCK; a second
        # caller with a different key must find the token already spent.
        token = self._token()
        results, barrier = [], threading.Barrier(2)

        def go(key):
            barrier.wait(5)
            results.append(self.pod.migrate(
                {"to_dc": "EU-CZ-1", "confirm_token": token}, key))

        threads = [threading.Thread(target=go, args=(k,)) for k in ("r1", "r2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(status for status, _ in results), [202, 409])
        self.start.assert_called_once()
        self.popen.assert_not_called()


class TestBotLockedHelper(_PodFixture):
    def test_bot_locked_yields_none_when_it_gets_the_lock(self):
        with bot._bot_locked() as busy:
            self.assertIsNone(busy)

    def test_bot_locked_yields_the_503_on_timeout(self):
        held, release = threading.Event(), threading.Event()

        def hold():
            with bot.BOT_LOCK:
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(bot, "BOT_LOCK_TIMEOUT_SEC", 0.1):
                with bot._bot_locked() as busy:
                    self.assertEqual(busy[0], 503)
                    self.assertEqual(busy[1]["error"]["code"], "bot_busy")
        finally:
            release.set()
            t.join(5)

    def test_app_runs_locked_still_delegates(self):
        runs = bot.AppRuns(self.tg, ME, None, self.idem)
        with runs._locked() as busy:
            self.assertIsNone(busy)


class TestOutcomeStatusTable(unittest.TestCase):
    def test_new_codes_map(self):
        self.assertEqual(status_for_code("upstream_unavailable"), 502)
        self.assertEqual(status_for_code("bad_request"), 400)
        self.assertEqual(status_for_code("nothing_running"), 409)


def status_for_code(code: str) -> int:
    from control.runs import status_for
    return status_for(Outcome(False, code, ""))


if __name__ == "__main__":
    unittest.main()

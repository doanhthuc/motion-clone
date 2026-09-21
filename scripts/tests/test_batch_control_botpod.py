"""The pod-side bot functions as the phone reaches them (spec §5.9): `_do_kill`
and `_start_migration` returning an `Outcome`, and `AppPod.kill` / `AppPod.resume`.

Everything here is free and nothing here can spend money: `subprocess.run`,
`subprocess.Popen`, `start_drain`, `start_phase_a`, `clear_lease` and the lease
readers are patched by name in `tgbot.bot`, so no test can reach `make
gpu-destroy`, `volume_migrate.py`, `runpodctl` or `vastai`. A test that could
reach one unpatched is a defect, not a slow test.
"""
import json, subprocess, sys, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Not a package (there is no scripts/tests/__init__.py), so the sibling test
# module is imported by its bare name off this directory — which works both
# under `unittest discover -s scripts/tests` and under a direct module run.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from batchlib.manifest import state_path_for
from batchlib_ext.provision_failure import (ProvisionFailure, provision_failure_path,
                                            write_provision_failure)
from control.idempotency import IdempotencyStore
from control.runs import Outcome
import tgbot.bot as bot
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

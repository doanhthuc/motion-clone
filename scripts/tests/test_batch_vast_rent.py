import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import vast_rent
from batchlib_ext.podctl import PodInfo
from batchlib_ext.vast_scoreboard import MachineRecord, Scoreboard, load_board
from batchlib_ext.vast_select import Criteria
from batchlib_ext.watchdog import DESTROYABLE_NAMES, GRACE_MIN


def offer(id, machine_id, dph=0.40, **over):
    base = dict(id=id, machine_id=machine_id, gpu_name="RTX 5090", dph_total=dph,
                inet_down=1800.0, internet_down_cost_per_tb=2.0, disk_bw=3800.0, cpu_ghz=3.0,
                direct_port_count=12, rentable=True, geolocation="Bulgaria, BG")
    base.update(over)
    return base


CRIT = Criteria(0.60, 3000.0, 2.5, 1000.0, 20.0)


def cfg(**over):
    base = dict(gpu="RTX_5090", disk_gb=120, image="ghcr.io/x/motion-prebuilt:t",
                reliability=0.95, criteria=CRIT, gb=50.0, pull_deadline_s=480.0)
    base.update(over)
    return vast_rent.RentConfig(**base)


class FakeVast:
    """Scripted Vast. `statuses[instance_id]` is the sequence of actual_status values returned
    one per poll (the last repeats). `create_fail` lists offer ids whose create raises."""

    def __init__(self, offers, statuses=None, create_fail=(), destroy_fail=(),
                 create_ambiguous=(), destroy_noop=(), destroy_lag=None, exists_error=False):
        self.offers = offers
        self.statuses = statuses or {}
        self.create_fail = set(create_fail)
        self.create_ambiguous = set(create_ambiguous)   # create raises AmbiguousCreate
        self.destroy_fail = set(destroy_fail)
        self.destroy_noop = set(destroy_noop)           # destroy "succeeds" but does nothing
        self.destroy_lag = destroy_lag or {}            # polls that still see it after destroy
        self.exists_error = exists_error
        self.queries, self.created, self.destroyed = [], [], []
        self.create_attempts, self.destroy_calls, self.exists_checks = [], [], []
        self._polls = {}
        self._seen_after_destroy = {}
        self._n = 0

    def search_offers(self, query):
        self.queries.append(query)
        if query.startswith("machine_id="):
            mid = int(query.split()[0].split("=")[1])
            return [o for o in self.offers if o["machine_id"] == mid]
        return list(self.offers)

    def create_instance(self, offer_id, *, image, disk_gb, label):
        self.create_attempts.append(offer_id)
        if offer_id in self.create_ambiguous:
            raise vast_rent.AmbiguousCreate(f"offer {offer_id}: unclear answer")
        if offer_id in self.create_fail:
            raise vast_rent.RentError(f"offer {offer_id} is gone")
        self._n += 1
        iid = f"i{self._n}"
        self.created.append(dict(offer=offer_id, iid=iid, image=image, disk=disk_gb,
                                 label=label))
        return iid

    def instance_status(self, instance_id):
        seq = self.statuses.get(instance_id, ["running"])
        i = self._polls.get(instance_id, 0)
        self._polls[instance_id] = i + 1
        return {"actual_status": seq[min(i, len(seq) - 1)]}

    def destroy_instance(self, instance_id):
        if instance_id in self.destroy_fail:
            raise vast_rent.RentError("destroy refused")
        self.destroy_calls.append(instance_id)
        if instance_id in self.destroy_noop:
            return
        self.destroyed.append(instance_id)

    def instance_exists(self, instance_id):
        self.exists_checks.append(instance_id)
        if self.exists_error:
            raise vast_rent.RentError("cannot list instances")
        if instance_id not in {c["iid"] for c in self.created}:
            return False
        if instance_id not in self.destroyed:
            return True
        seen = self._seen_after_destroy.get(instance_id, 0)
        self._seen_after_destroy[instance_id] = seen + 1
        return seen < self.destroy_lag.get(instance_id, 0)

    def ssh_url(self, instance_id):
        return "ssh://root@1.2.3.4:40022"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_rent(api, *, confirm=True, board=None, config=None, clock=None, **kw):
    clock = clock or Clock()
    board = board if board is not None else Scoreboard({})
    events = {"created": [], "released": [], "persisted": 0}
    result = vast_rent.rent(
        api, config or cfg(), board, confirm=confirm, now=clock.now, sleep=clock.sleep,
        log=lambda m: None,
        on_created=events["created"].append, on_released=events["released"].append,
        persist=lambda: events.__setitem__("persisted", events["persisted"] + 1), **kw)
    return result, events, board, clock


class TestDryRun(unittest.TestCase):
    def test_a_dry_run_creates_nothing_and_returns_the_ranking(self):
        api = FakeVast([offer(1, 11, 0.50), offer(2, 22, 0.40)])
        result, events, _, _ = run_rent(api, confirm=False)
        self.assertEqual(api.created, [])
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.chosen.offer["id"], 2)   # equal readiness -> cheaper first
        self.assertEqual(events["created"], [])

    def test_no_qualifying_offer_raises_with_the_reasons(self):
        api = FakeVast([offer(1, 11, dph=0.99)])
        with self.assertRaises(vast_rent.NoOffers) as cm:
            run_rent(api, confirm=False)
        self.assertIn("over price cap", str(cm.exception))

    def test_a_pinned_offer_must_still_qualify(self):
        api = FakeVast([offer(1, 11), offer(2, 22)])
        result, *_ = run_rent(api, confirm=False, config=cfg(pin="1"))
        self.assertEqual(result.chosen.offer["id"], 1)
        with self.assertRaises(vast_rent.NoOffers):
            run_rent(api, confirm=False, config=cfg(pin="999"))

    def test_known_good_machines_are_queried_directly_by_machine_id(self):
        # `search offers` returns a random ~40-row sample; machine_id= queries are deterministic.
        board = Scoreboard({})
        board.record(MachineRecord(144253, 35.0, None, None, 1.0, "ok"))
        api = FakeVast([offer(1, 144253)])
        run_rent(api, confirm=False, board=board)
        self.assertTrue(any(q.startswith("machine_id=144253") for q in api.queries), api.queries)
        # The lookup must keep the base filters: rank() does not check gpu/num_gpus/disk/
        # reliability, so a known-good host's other-GPU or multi-GPU offer would rank first.
        q = next(q for q in api.queries if q.startswith("machine_id=144253"))
        for clause in ("gpu_name=RTX_5090", "num_gpus=1", "disk_space>=120",
                       "reliability>0.95", "rentable=true"):
            self.assertIn(clause, q.split())

    def test_the_base_query_carries_gpu_disk_and_reliability(self):
        api = FakeVast([offer(1, 11)])
        run_rent(api, confirm=False)
        self.assertEqual(api.queries[0],
                         "gpu_name=RTX_5090 num_gpus=1 disk_space>=120 reliability>0.95 "
                         "rentable=true")


class TestRent(unittest.TestCase):
    def test_success_creates_with_the_label_records_the_pull_and_returns(self):
        api = FakeVast([offer(1, 11)], statuses={"i1": ["loading", "loading", "running"]})
        result, events, board, clock = run_rent(api)
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.created[0]["label"], "motion-transfer")
        self.assertEqual(api.created[0]["image"], "ghcr.io/x/motion-prebuilt:t")
        self.assertEqual(api.created[0]["disk"], 120)
        self.assertEqual(events["created"], ["i1"])
        self.assertEqual(events["released"], [])
        rec = board.get(11)
        self.assertEqual(rec.outcome, "ok")
        self.assertEqual(rec.pull_s, result.pull_s)
        self.assertEqual(result.pull_s, 2 * vast_rent.POLL_S)
        self.assertGreaterEqual(events["persisted"], 1)

    def test_a_pull_past_the_deadline_is_destroyed_blacklisted_and_the_next_machine_tried(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        result, events, board, clock = run_rent(api)
        self.assertEqual(api.destroyed, ["i1"])
        self.assertEqual(events["released"], ["i1"])
        self.assertEqual(board.get(11).outcome, "slow_pull")
        self.assertTrue(board.is_blacklisted(11, now=clock.now()))
        self.assertEqual(result.instance_id, "i2")
        self.assertEqual(events["created"], ["i1", "i2"])

    def test_at_most_two_retries_then_a_clear_failure(self):
        offers = [offer(i, i * 10, 0.40 + i / 100) for i in range(1, 6)]
        api = FakeVast(offers, statuses={f"i{n}": ["loading"] for n in range(1, 6)})
        with self.assertRaises(vast_rent.RentError) as cm:
            run_rent(api)
        self.assertEqual(len(api.created), 3)         # the first pull plus MAX_PULL_RETRIES
        self.assertEqual(api.destroyed, ["i1", "i2", "i3"])
        self.assertIn("3", str(cm.exception))

    def test_a_vanished_offer_does_not_cost_a_pull_retry(self):
        # Offers disappear between search and create (measured: pinning failed ~3 of 4 times).
        api = FakeVast([offer(1, 11), offer(2, 22), offer(3, 33)], create_fail={1, 2})
        result, *_ = run_rent(api)
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.created[0]["offer"], 3)

    def test_too_many_create_failures_give_up(self):
        offers = [offer(i, i * 10, 0.40 + i / 100) for i in range(1, 9)]
        api = FakeVast(offers, create_fail={o["id"] for o in offers})
        with self.assertRaises(vast_rent.RentError):
            run_rent(api)
        self.assertEqual(api.created, [])

    def test_an_exited_container_is_a_failure_not_a_wait(self):
        api = FakeVast([offer(1, 11), offer(2, 22, 0.45)],
                       statuses={"i1": ["exited"], "i2": ["running"]})
        result, _, board, clock = run_rent(api)
        self.assertEqual(board.get(11).outcome, "failed")
        self.assertEqual(result.instance_id, "i2")

    def test_an_instance_that_cannot_be_destroyed_is_reported_loudly_with_its_id(self):
        api = FakeVast([offer(1, 11), offer(2, 22)], statuses={"i1": ["loading"]},
                       destroy_fail={"i1"})
        with self.assertRaises(vast_rent.RentError) as cm:
            run_rent(api)
        self.assertIn("i1", str(cm.exception))
        self.assertIn("STILL BILLING", str(cm.exception))
        self.assertEqual(len(api.created), 1, "kept renting while an instance was undestroyed")

    def test_an_interrupt_while_waiting_destroys_the_new_instance(self):
        api = FakeVast([offer(1, 11)], statuses={"i1": ["loading"]})
        clock = Clock()

        def boom(_s):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            vast_rent.rent(api, cfg(), Scoreboard({}), confirm=True, now=clock.now,
                           sleep=boom, log=lambda m: None, on_created=lambda i: None,
                           on_released=lambda i: None, persist=lambda: None)
        self.assertEqual(api.destroyed, ["i1"])

    def test_a_transient_status_error_is_tolerated(self):
        api = FakeVast([offer(1, 11)])
        real = api.instance_status
        calls = {"n": 0}

        def flaky(iid):
            calls["n"] += 1
            if calls["n"] == 1:
                raise vast_rent.RentError("api hiccup")
            return real(iid)

        api.instance_status = flaky
        result, *_ = run_rent(api)
        self.assertEqual(result.instance_id, "i1")

    def test_the_pull_deadline_leaves_room_inside_the_watchdog_grace(self):
        # Tier 3 destroys an unleased labelled instance GRACE_MIN minutes after it first sees it.
        # The instance we keep must be `running` and leased before that, so the deadline plus a
        # minute of bookkeeping has to stay under it.
        self.assertLess(vast_rent.DEFAULT_PULL_DEADLINE_S + 60, GRACE_MIN * 60)

    def test_the_label_is_one_tier_three_may_destroy(self):
        self.assertIn(vast_rent.VAST_LABEL, DESTROYABLE_NAMES)


def rent_direct(api, logs, *, config=None, board=None, sleep=None, **hooks):
    """rent() with hooks the caller controls (run_rent fixes them). Messages go to `logs`."""
    clock = Clock()
    kwargs = dict(on_created=lambda i: None, on_released=lambda i: None, persist=lambda: None)
    kwargs.update(hooks)
    return vast_rent.rent(api, config or cfg(), board if board is not None else Scoreboard({}),
                          confirm=True, now=clock.now, sleep=sleep or clock.sleep,
                          log=logs.append, **kwargs)


def raiser(exc):
    def _raise(*_a, **_k):
        raise exc
    return _raise


class TestCreatedInstanceIsNeverOrphaned(unittest.TestCase):
    """C1: nothing between create returning and the unwinding `try` may leak the instance."""

    def test_on_created_failing_destroys_the_instance_and_propagates(self):
        api = FakeVast([offer(1, 11), offer(2, 22)])
        logs = []
        with self.assertRaises(OSError):
            rent_direct(api, logs, on_created=raiser(OSError("disk full")))
        self.assertEqual(api.destroyed, ["i1"])
        self.assertEqual(len(api.created), 1, "kept renting after the bookkeeping failed")

    def test_an_interrupt_inside_on_created_destroys_the_instance(self):
        api = FakeVast([offer(1, 11)])
        with self.assertRaises(KeyboardInterrupt):
            rent_direct(api, [], on_created=raiser(KeyboardInterrupt()))
        self.assertEqual(api.destroyed, ["i1"])

    def test_the_instance_id_is_logged_the_moment_it_exists(self):
        api = FakeVast([offer(1, 11)])
        logs = []
        with self.assertRaises(OSError):
            rent_direct(api, logs, on_created=raiser(OSError("disk full")))
        self.assertTrue(any("i1" in m for m in logs), logs)


class TestBookkeepingNeverAbortsARent(unittest.TestCase):
    """C2: the scoreboard and .env are advisory once the instance is real."""

    def test_persist_failing_on_the_success_path_still_returns_the_instance(self):
        api = FakeVast([offer(1, 11)])
        logs = []
        result = rent_direct(api, logs, persist=raiser(OSError("read-only fs")))
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.destroyed, [])
        self.assertTrue(any("could not update" in m for m in logs), logs)

    def test_persist_failing_on_the_abandon_path_still_tries_the_next_machine(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        result = rent_direct(api, [], persist=raiser(OSError("read-only fs")))
        self.assertEqual(result.instance_id, "i2")
        self.assertEqual(api.destroyed, ["i1"])

    def test_on_released_failing_after_a_verified_destroy_is_logged_and_the_loop_goes_on(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        logs = []
        result = rent_direct(api, logs, on_released=raiser(OSError("cannot write .env")))
        self.assertEqual(result.instance_id, "i2")
        self.assertTrue(any("stale" in m and "i1" in m for m in logs), logs)


class TestAmbiguousCreate(unittest.TestCase):
    """I1: a create whose outcome is unknown may have made an instance — stop, never re-create."""

    def test_an_ambiguous_create_stops_the_loop_without_a_second_create(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)], create_ambiguous={1})
        with self.assertRaises(vast_rent.AmbiguousCreate):
            run_rent(api)
        self.assertEqual(api.create_attempts, [1], "created again after an unclear answer")

    def test_a_definite_create_failure_still_moves_on_to_the_next_offer(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)], create_fail={1})
        result, *_ = run_rent(api)
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.create_attempts, [1, 2])

    def test_ambiguous_create_is_a_rent_error(self):
        self.assertTrue(issubclass(vast_rent.AmbiguousCreate, vast_rent.RentError))


class TestVerifiedDestroy(unittest.TestCase):
    """I2: an exit code is not proof of a destroy — re-list, as the repo learned the hard way."""

    def test_a_destroy_that_does_nothing_is_reported_as_still_billing_and_stops(self):
        api = FakeVast([offer(1, 11), offer(2, 22)], statuses={"i1": ["loading"]},
                       destroy_noop={"i1"})
        released = []
        with self.assertRaises(vast_rent.RentError) as cm:
            rent_direct(api, [], on_released=released.append)
        self.assertIn("STILL BILLING", str(cm.exception))
        self.assertIn("i1", str(cm.exception))
        self.assertIn("vastai destroy instance i1", str(cm.exception))
        self.assertEqual(len(api.created), 1, "kept renting while an instance was undestroyed")
        self.assertEqual(released, [], "cleared the instance id although it still exists")

    def test_a_verified_destroy_asks_the_api_whether_the_instance_is_gone(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        run_rent(api)
        self.assertIn("i1", api.exists_checks)

    def test_a_destroy_that_settles_after_a_moment_is_accepted(self):
        # Vast destroys asynchronously: the instance may be listed for a poll or two.
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]},
                       destroy_lag={"i1": vast_rent.VERIFY_POLLS - 1})
        result, events, _, _ = run_rent(api)
        self.assertEqual(result.instance_id, "i2")
        self.assertEqual(events["released"], ["i1"])

    def test_an_unverifiable_destroy_is_treated_as_still_billing(self):
        api = FakeVast([offer(1, 11), offer(2, 22)], statuses={"i1": ["loading"]},
                       exists_error=True)
        with self.assertRaises(vast_rent.RentError) as cm:
            run_rent(api)
        self.assertIn("STILL BILLING", str(cm.exception))
        self.assertEqual(len(api.created), 1)

    def test_the_interrupt_path_says_still_billing_loudly_but_lets_the_interrupt_through(self):
        api = FakeVast([offer(1, 11)], statuses={"i1": ["loading"]}, destroy_noop={"i1"})
        logs = []

        def boom(_s):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            rent_direct(api, logs, sleep=boom)
        self.assertTrue(any("STILL BILLING" in m and "i1" in m for m in logs), logs)


class TestABadMachineStaysBadWithinTheRun(unittest.TestCase):
    """I3: ranked is computed once, and one machine can carry several offers."""

    def test_a_second_offer_on_a_machine_that_just_failed_is_skipped(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 11, 0.41), offer(3, 33, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        result, *_ = run_rent(api)
        self.assertEqual([c["offer"] for c in api.created], [1, 3])
        self.assertEqual(result.instance_id, "i2")


class TestParseSshUrl(unittest.TestCase):
    def test_the_usual_shape(self):
        self.assertEqual(vast_rent.parse_ssh_url("ssh://root@1.2.3.4:40022"),
                         ("1.2.3.4", "40022"))

    def test_trailing_newline_and_a_preceding_warning_line(self):
        self.assertEqual(vast_rent.parse_ssh_url("Welcome to vast.ai\nssh://root@h.example:2222\n"),
                         ("h.example", "2222"))

    def test_no_scheme_or_no_user(self):
        self.assertEqual(vast_rent.parse_ssh_url("root@1.2.3.4:22"), ("1.2.3.4", "22"))
        self.assertEqual(vast_rent.parse_ssh_url("1.2.3.4:22"), ("1.2.3.4", "22"))

    def test_garbage_is_none(self):
        for text in ("", "no instance", "ssh://root@host"):
            with self.subTest(text=text):
                self.assertIsNone(vast_rent.parse_ssh_url(text))


class TestRealVastApi(unittest.TestCase):
    def _run(self, stdout="", rc=0, stderr=""):
        return mock.patch.object(vast_rent.subprocess, "run",
                                 return_value=mock.Mock(returncode=rc, stdout=stdout,
                                                        stderr=stderr))

    def test_create_uses_the_safety_flags_and_returns_the_new_contract(self):
        with self._run(json.dumps({"success": True, "new_contract": 51518664})) as run:
            iid = vast_rent.RealVastApi().create_instance(
                42230244, image="img:t", disk_gb=120, label="motion-transfer")
        argv = run.call_args[0][0]
        self.assertEqual(iid, "51518664")
        self.assertEqual(argv[:4], ["vastai", "create", "instance", "42230244"])
        for flag in ("--cancel-unavail", "--direct", "--ssh", "--raw"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--label") + 1], "motion-transfer")
        self.assertEqual(argv[argv.index("--image") + 1], "img:t")
        self.assertEqual(argv[argv.index("--disk") + 1], "120")

    def test_create_failure_and_missing_id_raise_rent_error(self):
        with self._run(stderr="offer no longer available", rc=1):
            with self.assertRaises(vast_rent.RentError) as cm:
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")
        self.assertIn("no longer available", str(cm.exception))
        with self._run(json.dumps({"success": False})):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")

    def test_search_passes_the_query_as_one_argument_and_parses_a_list(self):
        with self._run(json.dumps([{"id": 1}])) as run:
            out = vast_rent.RealVastApi().search_offers("gpu_name=RTX_5090 rentable=true")
        self.assertEqual(out, [{"id": 1}])
        self.assertEqual(run.call_args[0][0][:4],
                         ["vastai", "search", "offers", "gpu_name=RTX_5090 rentable=true"])

    def test_search_rejects_non_list_output(self):
        with self._run(json.dumps({"error": "x"})):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().search_offers("q")

    def test_a_missing_binary_is_a_rent_error(self):
        with mock.patch.object(vast_rent.subprocess, "run", side_effect=FileNotFoundError("vastai")):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().search_offers("q")

    def test_status_reads_show_instance_and_is_empty_on_failure(self):
        with self._run(json.dumps({"actual_status": "running"})):
            self.assertEqual(vast_rent.RealVastApi().instance_status("1")["actual_status"],
                             "running")
        with self._run(rc=1):
            self.assertEqual(vast_rent.RealVastApi().instance_status("1"), {})

    def test_destroy_goes_through_vastctl_so_the_prompt_is_answered(self):
        with mock.patch.object(vast_rent.VastCtl, "destroy") as destroy:
            vast_rent.RealVastApi().destroy_instance("51518664")
        destroy.assert_called_once_with("51518664")

    def test_a_create_timeout_is_ambiguous_because_the_instance_may_exist(self):
        with mock.patch.object(vast_rent.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("vastai", 120)):
            with self.assertRaises(vast_rent.AmbiguousCreate) as cm:
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")
        self.assertIn("instances-v1", str(cm.exception))
        self.assertIn("tier 3", str(cm.exception))

    def test_a_successful_exit_without_an_instance_id_is_ambiguous(self):
        for stdout in (json.dumps({"success": True}), json.dumps([1, 2]), "not json", ""):
            with self.subTest(stdout=stdout):
                with self._run(stdout):
                    with self.assertRaises(vast_rent.AmbiguousCreate):
                        vast_rent.RealVastApi().create_instance(
                            1, image="i", disk_gb=1, label="l")

    def test_definite_create_failures_are_not_ambiguous(self):
        for kwargs in (dict(stderr="offer no longer available", rc=1),
                       dict(stdout=json.dumps({"success": False}))):
            with self.subTest(kwargs=kwargs):
                with self._run(**kwargs):
                    with self.assertRaises(vast_rent.RentError) as cm:
                        vast_rent.RealVastApi().create_instance(
                            1, image="i", disk_gb=1, label="l")
                self.assertNotIsInstance(cm.exception, vast_rent.AmbiguousCreate)

    def test_a_missing_binary_on_create_is_definite_nothing_was_created(self):
        with mock.patch.object(vast_rent.subprocess, "run", side_effect=FileNotFoundError("vastai")):
            with self.assertRaises(vast_rent.RentError) as cm:
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")
        self.assertNotIsInstance(cm.exception, vast_rent.AmbiguousCreate)

    def test_instance_exists_lists_through_vastctl(self):
        pods = [PodInfo("51518664", "motion-transfer"), PodInfo("7", "")]
        with mock.patch.object(vast_rent.VastCtl, "list_pods", return_value=pods):
            self.assertTrue(vast_rent.RealVastApi().instance_exists("51518664"))
            self.assertFalse(vast_rent.RealVastApi().instance_exists("999"))

    def test_instance_exists_raises_rent_error_when_it_cannot_list(self):
        with mock.patch.object(vast_rent.VastCtl, "list_pods",
                               side_effect=RuntimeError("vastai show instances-v1 failed")):
            with self.assertRaises(vast_rent.RentError) as cm:
                vast_rent.RealVastApi().instance_exists("1")
        self.assertIn("could not verify", str(cm.exception))


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".env").write_text("GPU_INSTANCE_ID=old\n", encoding="utf-8")
        self.patches = [mock.patch.object(vast_rent, "ROOT", self.tmp),
                        mock.patch.object(vast_rent, "BOARD_PATH", self.tmp / "vast-machines.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _main(self, api, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(vast_rent, "make_api", return_value=api), \
             redirect_stdout(out), redirect_stderr(err):
            rc = vast_rent.main(["--gpu", "RTX_5090", "--disk", "120", "--image", "img:t",
                                 "--max-dph", "0.60", "--reliability", "0.95",
                                 "--min-disk-bw", "3000", "--min-cpu-ghz", "2.5", *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_shortlist_and_only_the_offer_id_on_stdout(self):
        rc, out, err = self._main(FakeVast([offer(7, 70)]))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "7")
        self.assertIn("unmeasured", err)
        self.assertIn("CONFIRM=yes", err)

    def test_confirm_prints_only_the_instance_id_and_writes_it_to_env_and_the_board(self):
        rc, out, _ = self._main(FakeVast([offer(7, 70)]), "--confirm")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "i1")
        self.assertIn("GPU_INSTANCE_ID=i1", (self.tmp / ".env").read_text(encoding="utf-8"))
        self.assertEqual(load_board(self.tmp / "vast-machines.json").get(70).outcome, "ok")

    def test_no_offers_is_exit_1_with_the_reason_on_stderr(self):
        rc, out, err = self._main(FakeVast([offer(7, 70, dph=0.99)]))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("over price cap", err)

    def test_a_failed_rent_leaves_no_stale_instance_id_in_env(self):
        # "exited" fails at once, so the real clock and the real sleep are never needed here.
        api = FakeVast([offer(7, 70)], statuses={"i1": ["exited"]})
        rc, _, _ = self._main(api, "--confirm")
        self.assertEqual(rc, 1)
        self.assertEqual(api.destroyed, ["i1"])
        self.assertNotIn("GPU_INSTANCE_ID=i1", (self.tmp / ".env").read_text(encoding="utf-8"))

    def test_ssh_target_prints_host_and_port(self):
        rc, out, _ = self._main(FakeVast([]), "--ssh-target", "51518664")
        self.assertEqual((rc, out.strip()), (0, "1.2.3.4 40022"))

    def test_ssh_target_exits_1_when_the_url_is_unparseable(self):
        api = FakeVast([])
        api.ssh_url = lambda iid: "not an address"
        rc, out, _ = self._main(api, "--ssh-target", "1")
        self.assertEqual((rc, out), (1, ""))


if __name__ == "__main__":
    unittest.main()

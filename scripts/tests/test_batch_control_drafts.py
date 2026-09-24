import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control
from control import drafts
from control.tryon_library import TryonLibrary
from tgbot.ingest import Probe
from tgbot.job import Job, render_manifest

IMG = Probe(kind="image", width=1080, height=1920, duration_s=0.0, bitrate_kbps=0, size_bytes=10)
VID = Probe(kind="video", width=1080, height=1920, duration_s=12.0, bitrate_kbps=9000, size_bytes=10)


def job(pipeline="tryon-motion-enhance", provider="gemini", **slots):
    return Job(slots={r: Path(p) for r, p in slots.items()},
               probes={r: (VID if r == "driver" else IMG) for r in slots},
               pipeline=pipeline, provider=provider)


class TestPureHelpers(unittest.TestCase):
    def test_lock_is_reentrant(self):
        with control.LOCK:
            with control.LOCK:
                pass

    def test_copy_is_detached(self):
        a = job(character="/s/a.png")
        b = drafts.copy_job(a)
        b.slots["outfit"] = Path("/s/o.png")
        self.assertNotIn("outfit", a.slots)
        self.assertEqual(drafts.signature(a), drafts.signature(job(character="/s/a.png")))

    def test_digest_is_stable_and_material_keyed(self):
        a = job(character="/s/a.png")
        self.assertEqual(drafts.job_digest(a), drafts.job_digest(drafts.copy_job(a)))
        self.assertEqual(len(drafts.job_digest(a)), 10)
        self.assertNotEqual(drafts.job_digest(a), drafts.job_digest(job(character="/s/b.png")))

    def test_jobs_for_appends_a_complete_current_job_once(self):
        full = job(character="/s/c.png", outfit="/s/o.png", driver="/s/d.mp4")
        self.assertEqual(drafts.jobs_for(None, []), [])
        self.assertEqual(drafts.jobs_for(job(character="/s/c.png"), []), [])
        self.assertEqual(len(drafts.jobs_for(full, [])), 1)
        self.assertEqual(len(drafts.jobs_for(full, [drafts.copy_job(full)])), 1)

    def test_drop_unusable(self):
        j = job(character="/s/c.png", outfit="/s/o.png", driver="/s/d.mp4")
        dropped = drafts.drop_unusable(j, "motion-enhance")
        self.assertEqual(dropped, ["outfit"])
        self.assertEqual(j.pipeline, "motion-enhance")
        self.assertEqual(set(j.slots), {"character", "driver"})
        self.assertEqual(set(j.probes), {"character", "driver"})

    def test_dump_load_round_trip(self):
        jobs = [job(character="/s/c.png", driver="/s/d.mp4", pipeline="motion-enhance")]
        back = drafts.load_jobs(drafts.dump_jobs(jobs))
        self.assertEqual([drafts.signature(j) for j in back], [drafts.signature(j) for j in jobs])
        self.assertEqual(back[0].probes["driver"], VID)

    def test_role_kind(self):
        self.assertEqual(drafts.role_kind("driver"), "video")
        self.assertEqual(drafts.role_kind("character"), "image")

    def test_catalog(self):
        cat = {p["id"]: p for p in drafts.pipeline_catalog()}
        tme = cat["tryon-motion-enhance"]
        self.assertEqual(tme["stages"], ["tryon", "motion", "enhance"])
        self.assertEqual(tme["required"], ["character", "driver", "outfit"])
        self.assertEqual(tme["optional"], ["background"])
        self.assertEqual(tme["roles"]["driver"], "video")
        self.assertEqual(tme["roles"]["outfit"], "image")
        self.assertEqual({p["id"] for p in tme["providers"]}, set(drafts.PROVIDER_LABELS))
        self.assertEqual(cat["motion-enhance"]["providers"], [])
        self.assertEqual([p["id"] for p in drafts.pipeline_catalog()],
                         sorted(p["id"] for p in drafts.pipeline_catalog()))


class TestBotUsesTheMovedHelpers(unittest.TestCase):
    def test_aliases(self):
        import tgbot.bot as bot
        self.assertIs(bot.PROVIDER_LABELS, drafts.PROVIDER_LABELS)
        self.assertIs(bot._copy_job, drafts.copy_job)
        self.assertIs(bot._signature, drafts.signature)
        self.assertIs(bot._job_digest, drafts.job_digest)
        self.assertIs(bot._dump_jobs, drafts.dump_jobs)
        self.assertIs(bot._load_jobs, drafts.load_jobs)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.batch = self.tmp / "batch"
        self.staging = self.batch / "tg-staging"
        (self.staging / "app").mkdir(parents=True)
        (self.staging / "123").mkdir()
        for name in ("me.png", "dress.png", "bg.png"):
            (self.staging / "app" / name).write_bytes(b"img")
        (self.staging / "app" / "dance.mp4").write_bytes(b"vid")
        (self.staging / "123" / "tg.png").write_bytes(b"img")
        self.probed = []

        def fake_probe(path):
            self.probed.append(path.name)
            if path.name.startswith("broken"):
                raise RuntimeError("ffprobe could not read it")
            return VID if path.suffix == ".mp4" else IMG

        self.library = TryonLibrary(self.batch / "tryon-library", "app")
        self.store = drafts.DraftStore(self.batch, self.staging, "app",
                                       default_pipeline="tryon-motion-enhance",
                                       default_provider="gemini",
                                       tryon_library=self.library, probe=fake_probe)

    def saved_seed(self, data=b"seed-bytes"):
        source = self.tmp / "seed.png"
        source.write_bytes(data)
        return self.library.save(image=source, material_ids={}, provider="gemini")["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fill(self):
        return self.store.patch({"slots": {"character": "app/me.png", "outfit": "app/dress.png",
                                           "driver": "app/dance.mp4"}})

    def assertRefused(self, code, fn, *args, **kwargs):
        with self.assertRaises(drafts.DraftError) as cm:
            fn(*args, **kwargs)
        self.assertEqual(cm.exception.code, code)


class TestRunnable(StoreCase):
    def test_runnable(self):
        self.assertEqual(self.store.runnable(), ([], None, 0))
        v = self.fill()
        jobs, validated, generation = self.store.runnable()
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(validated)
        self.assertEqual(generation, v["generation"])
        d = self.store._load()
        d.validated = True
        self.store._save(d)
        jobs, validated, generation = self.store.runnable()
        self.assertEqual(len(jobs), 1)
        self.assertTrue(validated)
        self.assertEqual(generation, v["generation"])


class TestView(StoreCase):
    def test_fresh_draft_uses_defaults_and_writes_nothing(self):
        v = self.store.view()
        self.assertEqual((v["owner"], v["pipeline"], v["provider"]), ("app", "tryon-motion-enhance", "gemini"))
        self.assertEqual(v["slots"], {})
        self.assertEqual(v["missing"], ["character", "driver", "outfit"])
        self.assertEqual((v["validated"], v["batch"], v["jobs"], v["estimate_min"]), (None, [], 0, None))
        self.assertFalse(self.store.path.exists())

    def test_view_has_no_absolute_paths(self):
        self.fill()
        self.assertNotIn(str(self.tmp), json.dumps(self.store.view()))

    def test_unreadable_file_is_set_aside(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json")
        v = self.store.view()
        self.assertEqual(v["slots"], {})
        self.assertEqual(len(list(self.store.path.parent.glob("app.draft.json.*bad"))), 1)

    def test_a_second_corrupt_file_does_not_clobber_the_first_bad(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json")
        self.store.view()
        self.store.path.write_text("{still not json")
        self.store.view()
        self.assertEqual(len(list(self.store.path.parent.glob("app.draft.json.*bad"))), 2)

    def test_a_hand_edited_wrong_shaped_field_is_set_aside_too(self):
        # "slots": [] has the right key with the wrong shape — load_jobs's
        # own `.items()` call raises AttributeError, not KeyError/ValueError,
        # so _load's except tuple has to name it explicitly or this crashes
        # the request instead of falling back to a fresh draft.
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(json.dumps({
            "job": {"pipeline": "motion-enhance", "provider": "qwen", "slots": [], "probes": {}},
            "basket": [], "validated": None, "generation": 0}))
        v = self.store.view()
        self.assertEqual(v["slots"], {})
        self.assertEqual(v["pipeline"], "tryon-motion-enhance")
        self.assertEqual(len(list(self.store.path.parent.glob("app.draft.json.*bad"))), 1)

    def test_fresh_view_has_no_seed(self):
        self.assertIsNone(self.store.view()["tryon_seed"])

    def test_view_reports_the_seed_as_a_library_id(self):
        # The phone shows a "saved try-on" badge from this and must not guess
        # it locally (Phase 6 spec §3). The id, never a path.
        self.fill()
        entry_id = self.saved_seed()
        v = self.store.patch({"tryon_seed": entry_id})
        self.assertEqual(v["tryon_seed"], entry_id)
        v = self.store.add_to_batch()
        self.assertEqual(v["batch"][0]["tryon_seed"], entry_id)
        v = self.store.patch({"tryon_seed": None})
        self.assertIsNone(v["tryon_seed"])
        self.assertEqual(v["batch"][0]["tryon_seed"], entry_id)
        self.assertNotIn(str(self.tmp), json.dumps(v))

    def test_a_deleted_entry_still_reports_its_id(self):
        self.fill()
        entry_id = self.saved_seed()
        self.store.patch({"tryon_seed": entry_id})
        self.library.delete(entry_id)
        self.assertEqual(self.store.view()["tryon_seed"], entry_id)


class TestPatch(StoreCase):
    def test_fill_all_slots(self):
        v = self.fill()
        self.assertEqual(v["missing"], [])
        self.assertEqual(v["slots"]["driver"]["material_id"], "app/dance.mp4")
        self.assertEqual(v["slots"]["driver"]["probe"]["kind"], "video")
        self.assertTrue(v["slots"]["driver"]["exists"])
        self.assertEqual(v["jobs"], 1)
        self.assertEqual(v["dropped"], [])
        self.assertEqual(self.store.view()["generation"], v["generation"])   # persisted

    def test_other_owners_material_is_usable(self):
        v = self.store.patch({"slots": {"character": "123/tg.png"}})
        self.assertEqual(v["slots"]["character"]["material_id"], "123/tg.png")

    def test_null_empties_a_slot(self):
        self.fill()
        v = self.store.patch({"slots": {"outfit": None}})
        self.assertNotIn("outfit", v["slots"])
        self.assertEqual(v["missing"], ["outfit"])

    def test_switch_pipeline_reports_dropped_roles(self):
        self.fill()
        v = self.store.patch({"pipeline": "motion-enhance"})
        self.assertEqual(v["dropped"], ["outfit"])
        self.assertEqual(set(v["slots"]), {"character", "driver"})

    def test_pipeline_and_slots_in_one_patch_check_roles_against_the_new_pipeline(self):
        self.assertRefused("unknown_role", self.store.patch,
                           {"pipeline": "motion-enhance", "slots": {"outfit": "app/dress.png"}})

    def test_refusals(self):
        self.assertRefused("unknown_pipeline", self.store.patch, {"pipeline": "nope"})
        self.assertRefused("unknown_provider", self.store.patch, {"provider": "nope"})
        self.assertRefused("unknown_role", self.store.patch, {"slots": {"hat": "app/me.png"}})
        self.assertRefused("wrong_kind", self.store.patch, {"slots": {"driver": "app/me.png"}})
        self.assertRefused("wrong_kind", self.store.patch, {"slots": {"character": "app/dance.mp4"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "app/missing.png"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "app/../123/tg.png"}})
        self.assertRefused("not_found", self.store.patch, {"slots": {"character": "me.png"}})
        self.assertRefused("bad_request", self.store.patch, {"colour": "red"})
        self.assertRefused("bad_request", self.store.patch, {"slots": ["app/me.png"]})
        self.assertRefused("bad_request", self.store.patch, {"pipeline": 3})
        self.assertRefused("not_applicable", self.store.patch,
                           {"pipeline": "motion-enhance", "provider": "qwen"})

    def test_unprobeable(self):
        (self.staging / "app" / "broken.png").write_bytes(b"x")
        self.assertRefused("unprobeable", self.store.patch, {"slots": {"character": "app/broken.png"}})

    def test_unprobeable_message_has_no_absolute_path(self):
        # ffprobe's own stderr names the absolute staged path; that must
        # never reach the phone verbatim.
        path = self.staging / "app" / "broken.png"
        path.write_bytes(b"x")

        def bad_probe(p):
            raise RuntimeError(f"{p}: Invalid data found when processing input")

        self.store._probe = bad_probe
        with self.assertRaises(drafts.DraftError) as cm:
            self.store.patch({"slots": {"character": "app/broken.png"}})
        self.assertEqual(cm.exception.code, "unprobeable")
        self.assertNotIn(str(self.tmp), cm.exception.message)
        self.assertNotIn(str(path), cm.exception.message)
        self.assertIn("broken.png", cm.exception.message)

    def test_file_deleted_between_resolve_and_apply_is_not_found(self):
        # Closes the race between resolving/probing a path (outside
        # control.LOCK, since ffprobe can take up to 60s) and applying it
        # (under the lock): a delete landing in that gap must not leave the
        # draft pointing at a file that no longer exists.
        before = self.fill()
        orig = self.store._probe

        def vanish(path):
            result = orig(path)
            path.unlink()
            return result

        self.store._probe = vanish
        self.assertRefused("not_found", self.store.patch, {"slots": {"background": "app/bg.png"}})
        after = self.store.view()
        self.assertEqual(after["generation"], before["generation"])
        self.assertNotIn("background", after["slots"])

    def test_a_refused_patch_changes_nothing(self):
        before = self.fill()
        self.assertRefused("wrong_kind", self.store.patch,
                           {"pipeline": "motion-enhance", "slots": {"driver": "app/me.png"}})
        after = self.store.view()
        self.assertEqual(after["pipeline"], "tryon-motion-enhance")
        self.assertEqual(after["generation"], before["generation"])

    def test_every_change_bumps_generation_and_resets_the_verdict(self):
        g = self.fill()["generation"]
        d = self.store._load()
        d.validated = True
        self.store._save(d)
        v = self.store.patch({"provider": "qwen-max"})
        self.assertEqual(v["generation"], g + 1)
        self.assertIsNone(v["validated"])

    def test_probe_runs_outside_the_lock(self):
        seen = []
        orig = self.store._probe

        def probe_and_check(path):
            # RLock.release() must be called by the same thread that
            # acquired it, so the acquire-then-release round trip has to
            # happen inside the worker thread, not split across it and the
            # caller (a plain lambda handing the release back here raised
            # "cannot release un-acquired lock" — measured 2026-09-21).
            def worker():
                got = control.LOCK.acquire(timeout=1)
                seen.append(got)
                if got:
                    control.LOCK.release()

            t = threading.Thread(target=worker)
            t.start(); t.join()
            return orig(path)

        self.store._probe = probe_and_check
        self.store.patch({"slots": {"character": "app/me.png"}})
        self.assertEqual(seen, [True])

    def test_vanished_file_counts_as_missing(self):
        self.fill()
        (self.staging / "app" / "dress.png").unlink()
        v = self.store.view()
        self.assertFalse(v["slots"]["outfit"]["exists"])
        self.assertEqual(v["missing"], ["outfit"])
        # The current job is incomplete now (a required file vanished), so it
        # must drop out of `jobs`/`estimate_min` too — only the basket would
        # count (jobs_for's own missing_slots() only checks
        # which roles have a dict entry, not whether the file still exists).
        self.assertEqual(v["jobs"], 0)

    def test_tryon_seed_resolves_a_library_entry_and_reaches_the_manifest(self):
        # §5.10, slice 6. The id is resolved to a real path HERE (the phone only
        # ever names a library entry) and rendered into the manifest, which is
        # the only channel that reaches batchlib/runner.py's Phase A.
        self.fill()
        entry_id = self.saved_seed()
        v = self.store.patch({"tryon_seed": entry_id})
        self.assertEqual(v["missing"], [])
        seed = self.store._load().job.tryon_seed
        self.assertIsNotNone(seed)
        self.assertEqual(seed.read_bytes(), b"seed-bytes")
        # Survives the round trip through the on-disk draft, and lands in the
        # manifest the runner will actually read.
        jobs, _validated, _generation = self.store.runnable()
        self.assertIn(f"seedImage: {seed}", render_manifest(jobs, now="2026-09-22 09:00:00"))

    def test_tryon_seed_null_clears_it(self):
        self.fill()
        self.store.patch({"tryon_seed": self.saved_seed()})
        self.store.patch({"tryon_seed": None})
        self.assertIsNone(self.store._load().job.tryon_seed)

    def test_tryon_seed_refusals(self):
        # Its own code, not not_found: a deleted library entry is not a stale
        # material, and the phone reloads its materials list on not_found.
        self.assertRefused("seed_not_found", self.store.patch, {"tryon_seed": "nope"})
        self.assertRefused("bad_request", self.store.patch, {"tryon_seed": 3})
        # An unknown id must not half-apply the rest of the same patch.
        self.assertRefused("seed_not_found", self.store.patch,
                           {"provider": "qwen-max", "tryon_seed": "nope"})
        self.assertEqual(self.store.view()["provider"], "gemini")

    def test_seed_not_found_leaves_the_draft_byte_identical(self):
        # The app skips its ambiguity re-read on this code because the seed is
        # resolved above control.LOCK, before anything is written. Asserted
        # here rather than trusted.
        self.fill()
        before = self.store.path.read_bytes()
        self.assertRefused("seed_not_found", self.store.patch,
                           {"slots": {"background": "app/bg.png"}, "tryon_seed": "nope"})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_a_stale_material_beside_a_valid_seed_is_still_not_found(self):
        # The cross build sends tryon_seed together with slots.outfit. A gone
        # material in that patch must keep saying not_found, so the phone
        # still refreshes its materials list — narrowing pinned from this side.
        self.fill()
        self.assertRefused("not_found", self.store.patch,
                           {"slots": {"outfit": "app/missing.png"},
                            "tryon_seed": self.saved_seed()})

    def test_tryon_seed_needs_a_local_provider(self):
        # Money is a first-class constraint: a seed sitting beside a non-local
        # provider is never read by Phase A (runner.py's _local_tryon_stage
        # only names a stage whose provider is_local_provider), so the job goes
        # on to a real, paid pod try-on while the caller believes they asked
        # for a reuse. Refuse loudly, the way _regen_tryon's "not_local" does.
        self.fill()
        self.store.patch({"provider": "qwen"})
        self.assertRefused("not_local", self.store.patch, {"tryon_seed": self.saved_seed()})
        self.assertIsNone(self.store._load().job.tryon_seed)

    def test_provider_cannot_move_away_from_local_while_a_seed_is_set(self):
        # The other direction of the same rule — otherwise the manifest ends up
        # carrying BOTH a non-local provider and a seed the runner never sees.
        self.fill()
        self.store.patch({"tryon_seed": self.saved_seed()})
        self.assertRefused("not_local", self.store.patch, {"provider": "qwen"})
        d = self.store._load()
        self.assertEqual(d.job.provider, "gemini")
        self.assertIsNotNone(d.job.tryon_seed)

    def test_seed_and_local_provider_in_one_patch_is_allowed(self):
        # The check must read the POST-patch provider, not the stored one:
        # setting both at once is a perfectly good request.
        self.fill()
        self.store.patch({"provider": "qwen"})
        v = self.store.patch({"provider": "qwen-max", "tryon_seed": self.saved_seed()})
        self.assertEqual(v["provider"], "qwen-max")
        self.assertIsNotNone(self.store._load().job.tryon_seed)

    def test_tryon_seed_survives_add_to_batch(self):
        # copy_job must carry it, or the batch entry silently loses the seed and
        # re-spends on an image the user already has.
        self.fill()
        self.store.patch({"tryon_seed": self.saved_seed()})
        self.store.add_to_batch()
        d = self.store._load()
        self.assertEqual(d.basket[0].tryon_seed, d.job.tryon_seed)

    def test_two_jobs_differing_only_by_seed_are_two_jobs(self):
        # Same reasoning as provider: same material through a seed vs a fresh
        # try-on are different runs, so signature() must tell them apart or
        # job_digest collides and a drop tap acts on the wrong basket row.
        a = job(character="/s/a.png")
        b = job(character="/s/a.png")
        b.tryon_seed = Path("/lib/app/abc.png")
        self.assertNotEqual(drafts.signature(a), drafts.signature(b))
        self.assertEqual(drafts.load_jobs(drafts.dump_jobs([b]))[0].tryon_seed, b.tryon_seed)
        self.assertIsNone(drafts.load_jobs(drafts.dump_jobs([a]))[0].tryon_seed)


class TestBatch(StoreCase):
    def test_add_keeps_editing_a_copy(self):
        self.fill()
        v = self.store.add_to_batch()
        self.assertEqual(len(v["batch"]), 1)
        self.assertEqual(v["jobs"], 1)                  # the current job equals the batch entry
        v = self.store.patch({"slots": {"outfit": "app/bg.png"}})
        self.assertEqual(v["jobs"], 2)
        self.assertEqual(v["batch"][0]["slots"]["outfit"], "app/dress.png")

    def test_add_refusals(self):
        self.assertRefused("missing_slots", self.store.add_to_batch)
        self.fill()
        self.store.add_to_batch()
        self.assertRefused("duplicate", self.store.add_to_batch)

    def test_add_refuses_when_a_file_vanished(self):
        self.fill()
        (self.staging / "app" / "dance.mp4").unlink()
        self.assertRefused("missing_slots", self.store.add_to_batch)

    def test_drop_by_digest(self):
        self.fill()
        digest = self.store.add_to_batch()["batch"][0]["digest"]
        self.assertRefused("not_found", self.store.drop_from_batch, "0000000000")
        v = self.store.drop_from_batch(digest)
        self.assertEqual(v["batch"], [])

    def test_batch_run_ids_are_unique_like_the_manifest_gives_them(self):
        # Same material, different provider: signature() (provider is part of
        # it) lets both sit in the basket at once, but run_id_for hashes only
        # the slots, so both entries get the same base id. The view must
        # match what render_manifest/_unique_ids actually suffixes onto the
        # manifest, or the app would show two rows named the same thing.
        self.fill()
        v = self.store.add_to_batch()
        v = self.store.patch({"provider": "qwen-max"})
        v = self.store.add_to_batch()
        self.assertEqual(len(v["batch"]), 2)
        run_ids = [b["run_id"] for b in v["batch"]]
        self.assertEqual(len(set(run_ids)), 2)
        self.assertTrue(run_ids[1].startswith(run_ids[0] + "-"))

    def test_concurrent_adds_make_one_entry(self):
        self.fill()
        errors = []

        def add():
            try:
                self.store.add_to_batch()
            except drafts.DraftError as exc:
                errors.append(exc.code)

        threads = [threading.Thread(target=add) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.store.view()["batch"]), 1)
        self.assertEqual(errors, ["duplicate"] * 7)

    def test_clear_resets_but_deletes_no_material(self):
        g = self.fill()["generation"]
        self.store.add_to_batch()
        v = self.store.clear()
        self.assertEqual((v["slots"], v["batch"]), ({}, []))
        self.assertGreater(v["generation"], g)
        self.assertTrue((self.staging / "app" / "me.png").exists())


class TestValidate(StoreCase):
    def fake_run(self, returncode=0, out="  ✓ manifest hợp lệ · 1 run\n", err="", before=None):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            # cmd is [sys.executable, "scripts/batch_run.py", "--file",
            # <manifest>, "--validate-only"] — see drafts.validate's comment
            # on why this runs the validator directly instead of `make`.
            manifest = Path(cmd[3])
            calls.append(manifest.read_text())
            if before:
                before()
            return mock.Mock(returncode=returncode, stdout=out, stderr=err)
        return run, calls

    def test_nothing_to_validate(self):
        self.assertRefused("nothing_to_validate", self.store.validate, repo_root=self.tmp)

    def test_success_records_the_verdict_and_removes_the_manifest(self):
        self.fill()
        run, calls = self.fake_run()
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual((result["valid"], result["stale"]), (True, False))
        self.assertTrue(result["draft"]["validated"])
        self.assertIsNotNone(result["draft"]["estimate_min"])
        cmd, kw = calls[0]
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1:3], ["scripts/batch_run.py", "--file"])
        self.assertEqual(cmd[4], "--validate-only")
        self.assertEqual(kw["cwd"], self.tmp)
        self.assertEqual(kw["timeout"], drafts.VALIDATE_TIMEOUT_SEC)
        self.assertIn("pipeline: tryon-motion-enhance", calls[1])
        manifest = Path(cmd[3])
        self.assertEqual(manifest.parent, self.batch / ".validate")
        self.assertFalse(manifest.exists())
        self.assertEqual(list(self.batch.glob("*.yaml")), [])

    def test_failure_is_422_with_output_and_no_absolute_paths(self):
        self.fill()
        # DraftStore resolves staging_root, so a real batch-validate error
        # names a staged file under self.tmp.resolve(), not self.tmp itself —
        # on macOS these differ (/var -> /private/var). Use the resolved form
        # so this actually exercises the stripping, not just the unresolved
        # one repo_root happens to be passed as.
        real = self.tmp.resolve()
        run, _ = self.fake_run(returncode=1, err=f"✗ missing {real}/batch/tg-staging/app/me.png\n")
        with self.assertRaises(drafts.DraftError) as cm:
            self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(cm.exception.code, "invalid")
        self.assertIn("batch/tg-staging/app/me.png", cm.exception.message)
        self.assertNotIn(str(self.tmp), cm.exception.message)
        self.assertNotIn(str(real), cm.exception.message)
        self.assertIs(self.store.view()["validated"], False)

    def test_timeout_is_recorded_as_failed(self):
        self.fill()

        def run(cmd, **kw):
            raise drafts.subprocess.TimeoutExpired(cmd, kw["timeout"])

        with self.assertRaises(drafts.DraftError) as cm:
            self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(cm.exception.code, "invalid")
        self.assertIn("90", cm.exception.message)

    def test_a_change_during_validation_makes_the_verdict_stale(self):
        self.fill()
        run, _ = self.fake_run(before=lambda: self.store.patch({"provider": "qwen-max"}))
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertTrue(result["stale"])
        self.assertIsNone(self.store.view()["validated"])

    def test_a_stale_failed_validate_is_a_200_not_a_raise(self):
        # The draft this verdict is about no longer exists by the time the
        # subprocess answers (edited mid-run), so a failing verdict is stale
        # information about a gone draft, not an error the caller must fix.
        self.fill()
        run, _ = self.fake_run(returncode=1, err="✗ nope\n",
                               before=lambda: self.store.patch({"provider": "qwen-max"}))
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual((result["valid"], result["stale"]), (False, True))
        self.assertIn("nope", result["output"])
        self.assertIsNone(self.store.view()["validated"])

    def test_a_stale_successful_validate_has_no_output_key(self):
        self.fill()
        run, _ = self.fake_run(before=lambda: self.store.patch({"provider": "qwen-max"}))
        result = self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual((result["valid"], result["stale"]), (True, True))
        self.assertNotIn("output", result)

    def test_the_subprocess_runs_outside_the_lock(self):
        self.fill()
        got = []

        def probe_lock():
            # Acquire and release inside the same worker thread (RLock
            # ownership is per-thread — see test_probe_runs_outside_the_lock).
            def worker():
                acquired = control.LOCK.acquire(timeout=1)
                got.append(acquired)
                if acquired:
                    control.LOCK.release()

            t = threading.Thread(target=worker)
            t.start(); t.join()

        run, _ = self.fake_run(before=probe_lock)
        self.store.validate(repo_root=self.tmp, run=run)
        self.assertEqual(got, [True])

    def test_a_second_validate_while_one_is_running_gets_busy(self):
        self.fill()
        entered, release = threading.Event(), threading.Event()

        def blocking_run(cmd, **kw):
            entered.set()
            release.wait(5)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        t = threading.Thread(target=self.store.validate,
                             kwargs={"repo_root": self.tmp, "run": blocking_run})
        t.start()
        try:
            self.assertTrue(entered.wait(5))
            other_run, _ = self.fake_run()
            self.assertRefused("busy", self.store.validate, repo_root=self.tmp, run=other_run)
        finally:
            release.set()
            t.join(5)
        self.assertFalse(t.is_alive())

    def test_write_manifest_failure_leaves_no_partial_manifest(self):
        self.fill()

        def bad_write(jobs, path, *, now):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("partial")          # what an interrupted write leaves behind
            raise OSError("disk full")

        with mock.patch("control.drafts.write_manifest", side_effect=bad_write):
            with self.assertRaises(OSError):
                self.store.validate(repo_root=self.tmp)
        self.assertEqual(list((self.batch / ".validate").glob("*")), [])
        # The semaphore from fix 1 must also be released on this path, or
        # every validate call after this one would wrongly answer "busy".
        other_run, _ = self.fake_run()
        result = self.store.validate(repo_root=self.tmp, run=other_run)
        self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()

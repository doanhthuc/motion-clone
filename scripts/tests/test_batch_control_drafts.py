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
from tgbot.ingest import Probe
from tgbot.job import Job

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

        self.store = drafts.DraftStore(self.batch, self.staging, "app",
                                       default_pipeline="tryon-motion-enhance",
                                       default_provider="gemini", probe=fake_probe)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fill(self):
        return self.store.patch({"slots": {"character": "app/me.png", "outfit": "app/dress.png",
                                           "driver": "app/dance.mp4"}})

    def assertRefused(self, code, fn, *args):
        with self.assertRaises(drafts.DraftError) as cm:
            fn(*args)
        self.assertEqual(cm.exception.code, code)


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

    def test_file_deleted_between_resolve_and_apply_is_not_found(self):
        # Closes the race between resolving/probing a path (outside
        # control.LOCK, since ffprobe can take up to 60s) and applying it
        # (under the lock): a delete landing in that gap must not leave the
        # draft pointing at a file that no longer exists (fix round 1).
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
        # count (fix round 1: jobs_for's own missing_slots() only checks
        # which roles have a dict entry, not whether the file still exists).
        self.assertEqual(v["jobs"], 0)


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


if __name__ == "__main__":
    unittest.main()

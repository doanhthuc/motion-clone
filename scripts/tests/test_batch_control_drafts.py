import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

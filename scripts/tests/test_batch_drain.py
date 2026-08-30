import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest
from drain import abs_max_min, failed_job_ids

YAML = """
runs:
  - id: a
    pipeline: motion-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
  - id: b
    pipeline: character-swap-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
"""


class TestAbsMax(unittest.TestCase):
    def test_sums_stage_timeouts_plus_30(self):
        # motion 60 + enhance 90 + character-swap 60 + enhance 90 = 300, +30
        path = Path(tempfile.mkdtemp()) / "m.yaml"
        path.write_text(YAML, encoding="utf-8")
        self.assertEqual(abs_max_min(load_manifest(path)), 330)


class TestFailedJobIds(unittest.TestCase):
    def test_collects_job_ids_from_error_stages(self):
        state = {"runs": {
            "r1": {"status": "error", "stages": {
                "motion": {"status": "done", "job_id": "j1"},
                "enhance": {"status": "error", "job_id": "j2"}}},
            "r2": {"status": "done", "stages": {
                "motion": {"status": "done", "job_id": "j3"}}},
        }}
        self.assertEqual(failed_job_ids(state), [("r1", "j2")])

    def test_error_stage_without_a_job_id_is_skipped(self):
        # A run can fail before a job was ever submitted — there is nothing
        # to fetch logs for, and inventing an id would 404 noisily.
        state = {"runs": {"r1": {"status": "error",
                                 "stages": {"motion": {"status": "error"}}}}}
        self.assertEqual(failed_job_ids(state), [])


if __name__ == "__main__":
    unittest.main()

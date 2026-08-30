import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest
from drain import abs_max_min

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


if __name__ == "__main__":
    unittest.main()

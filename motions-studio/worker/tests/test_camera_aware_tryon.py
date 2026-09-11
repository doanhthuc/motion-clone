import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# linux.py only needs requests when the real worker calls APIs.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(
        ConnectionError=ConnectionError,
        RequestException=Exception,
    )
    sys.modules["requests"] = requests_stub

from worker_runtime import linux  # noqa: E402


class CameraAwareTryonTests(unittest.TestCase):
    def test_midpoint_respects_selected_segment(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "ffprobe" and "format=duration" in cmd:
                return mock.Mock(returncode=0, stdout="30\n", stderr="")
            if cmd[0] == "ffprobe":
                return mock.Mock(returncode=0, stdout="720x1280\n", stderr="")
            Path(cmd[-1]).write_bytes(b"png")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(linux.subprocess, "run", fake_run), \
             mock.patch.object(linux, "api_log"):
            dims = linux._extract_camera_guide_frame(
                "job-1", "driver.mp4", str(Path(d) / "guide.png"),
                {"driverStartSec": 4, "driverDurSec": 10},
            )
        ffmpeg = next(cmd for cmd in calls if cmd[0] == "ffmpeg")
        self.assertEqual(ffmpeg[ffmpeg.index("-ss") + 1], "9.000000")
        self.assertEqual(dims, (720, 1280))

    def test_missing_or_zero_duration_fails_before_image_edit(self):
        with mock.patch.object(
            linux.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="0\n", stderr="")
        ), mock.patch.object(linux, "_gemini_edit") as image_edit:
            with self.assertRaises(RuntimeError):
                linux._extract_camera_guide_frame("job-1", "driver.mp4", "guide.png", {})
        image_edit.assert_not_called()

    def test_prompt_loader_matches_checked_in_asset(self):
        asset = Path(linux.__file__).resolve().parents[1] / "assets" / "camera-aware-tryon.json"
        expected = json.loads(asset.read_text(encoding="utf-8"))
        self.assertEqual(linux._load_camera_compose_prompt(), (expected["positive"], expected["negative"]))

    def test_camera_guide_frame_other_than_middle_is_rejected(self):
        with self.assertRaises(RuntimeError):
            linux._extract_camera_guide_frame(
                "job-1", "driver.mp4", "guide.png", {"cameraGuideFrame": "first"}
            )

    def test_non_finite_segment_start_is_rejected(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="30\n", stderr="")

        with mock.patch.object(linux.subprocess, "run", fake_run):
            with self.assertRaises(RuntimeError):
                linux._extract_camera_guide_frame(
                    "job-1", "driver.mp4", "guide.png", {"driverStartSec": float("nan")}
                )
        self.assertFalse(any(cmd[0] == "ffmpeg" for cmd in calls))


if __name__ == "__main__":
    unittest.main()

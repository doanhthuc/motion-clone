import json
import os
import sys
import tempfile
import types
import unittest
import shutil
import subprocess
from contextlib import ExitStack
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


class CameraCompositionTests(unittest.TestCase):
    def _assert_camera_clean_requires_product(self, clean_flag):
        job = {"id": "missing-product", "inputs": {
            "model": "model.png", "background": "background.png", "cameraGuide": "driver.mp4",
        }, "params": {"cameraAware": True, clean_flag: True}}
        with mock.patch.object(linux, "api_download", side_effect=lambda *args: self.fail(
            "camera-aware job without product reached input download")):
            with self.assertRaises(RuntimeError):
                linux.run_tryon(job)

    def test_camera_clean_only_requires_product(self):
        self._assert_camera_clean_requires_product("cleanOnly")

    def test_camera_clean_only_snake_requires_product(self):
        self._assert_camera_clean_requires_product("clean_only")

    def test_ordinary_clean_only_still_accepts_model_without_product(self):
        for clean_flag in ("cleanOnly", "clean_only"):
            with self.subTest(clean_flag=clean_flag), tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                person = self.fixtures(Path(d))[0]
                uploads = []
                for name, replacement in {
                    "api_download": lambda key, dest: shutil.copyfile(key, dest),
                    "api_progress": lambda *args, **kwargs: None,
                    "api_upload_output": lambda job, out, **kwargs: uploads.append(out),
                    "comfy_upload": str, "comfy_submit": lambda graph: "pid",
                    "comfy_poll": lambda *args, **kwargs: {},
                    "comfy_fetch_output": lambda *args, **kwargs: str(person),
                }.items():
                    stack.enter_context(mock.patch.object(linux, name, replacement))
                output = linux.run_tryon({"id": "ordinary-clean", "inputs": {"model": str(person)},
                                         "params": {clean_flag: True}})
                self.assertEqual(output, str(person))
                self.assertEqual(uploads, [str(person)])

    def test_qwen_reference_cap_cannot_silently_drop_camera_guide(self):
        for provider in ("qwen", "qwen-max", "gemini"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                paths = self.fixtures(Path(d))
                sent = []
                def edit(images, prompt, key, out_path, **kwargs):
                    sent.append(images)
                    return shutil.copyfile(paths[0], out_path)
                for name, replacement in {
                    "QWEN_EDIT_MAX_REFS": 2, "TRYON_GEMINI_FALLBACK": True,
                    "_gemini_edit": mock.Mock(side_effect=RuntimeError("quota")), "_qwen_max_edit": edit,
                    "comfy_upload": str, "comfy_submit": lambda graph: sent.append(graph) or "pid",
                    "comfy_poll": lambda *args, **kwargs: {},
                    "comfy_fetch_output": lambda *args, **kwargs: str(paths[0]),
                    "api_log": lambda *args, **kwargs: None,
                }.items():
                    stack.enter_context(mock.patch.object(linux, name, replacement))
                with self.assertRaises(RuntimeError):
                    linux._tryon_compose_camera("job", provider, *map(str, paths), "camera", {"apiKey": "fake"})
                self.assertEqual(sent, [])

    def fixtures(self, tmp):
        from PIL import Image
        paths = [tmp / name for name in ("person.png", "background.png", "guide.png")]
        for path, color, dims in zip(paths, ("red", "green", "blue"), ((160, 160), (160, 160), (90, 160))):
            Image.new("RGB", dims, color).save(path)
        return paths

    def test_all_providers_compose_three_refs_and_frame_to_guide(self):
        self.assertTrue(hasattr(linux, "_tryon_compose_camera"), "camera composition helper missing")
        from PIL import Image, ImageDraw
        for provider in ("gemini", "qwen-max", "huggingface", "qwen"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                tmp = Path(d)
                paths = self.fixtures(tmp)
                calls = []
                generated = tmp / "generated.png"
                picture = Image.new("RGB", (160, 160), "black")
                ImageDraw.Draw(picture).rectangle((70, 70, 89, 89), fill="red")
                picture.save(generated)
                def edit(images, prompt, key, out_path, **kwargs):
                    calls.append((images, prompt, kwargs))
                    shutil.copyfile(generated, out_path)
                    return out_path
                def hf_call(fal_id, payload, *args):
                    calls.append(payload)
                    return {"images": [{"url": "fake-output"}]}
                def submit(graph):
                    calls.append(graph)
                    return "pid"
                for name, replacement in {
                    "_gemini_edit": edit, "_qwen_max_edit": edit, "_hf_call": hf_call,
                    "_hf_fetch": lambda url, dest: shutil.copyfile(generated, dest),
                    "comfy_upload": lambda path: str(path), "comfy_submit": submit,
                    "comfy_poll": lambda *args, **kwargs: {},
                    "comfy_fetch_output": lambda *args, **kwargs: str(generated),
                    "api_log": lambda *args, **kwargs: None,
                }.items():
                    stack.enter_context(mock.patch.object(linux, name, replacement))
                out = linux._tryon_compose_camera("job", provider, *map(str, paths),
                                                  "camera", {"apiKey": "hf_" + "x" * 30})
                self.assertEqual(linux._img_size(out), (90, 160))
                with Image.open(out) as picture:
                    self.assertEqual(picture.getbbox(), (35, 70, 55, 90))
                if provider in ("gemini", "qwen-max"):
                    self.assertEqual([part[0] for part in calls[0][0]], [p.read_bytes() for p in paths])
                    self.assertEqual(calls[0][1], linux._load_camera_compose_prompt()[0])
                    if provider == "gemini":
                        self.assertEqual(calls[0][2]["aspect_ratio"], "9:16")
                    else:
                        self.assertEqual(calls[0][2].get("size"), "752*1328")
                elif provider == "huggingface":
                    self.assertEqual(calls[0]["image_urls"], [linux._hf_data_uri(str(p)) for p in paths])
                    self.assertEqual(calls[0]["image_size"], {"width": 88, "height": 160})
                else:
                    graph = calls[0]
                    self.assertEqual([graph[str(i)]["inputs"]["image"] for i in (10, 11, 12)], list(map(str, paths)))
                    self.assertEqual(graph["40"]["inputs"]["prompt"], linux._load_camera_compose_prompt()[0])
                    self.assertNotEqual(graph["42"]["class_type"], "VAEEncode")
                    w, h = linux._fit_aligned(90, 160, mp=linux.TRYON_MP, align=64)
                    self.assertEqual((graph["42"]["inputs"]["width"], graph["42"]["inputs"]["height"]), (w, h))

    def test_empty_or_undecodable_composition_is_rejected(self):
        self.assertTrue(hasattr(linux, "_tryon_compose_camera"), "camera composition helper missing")
        for invalid in (None, "invalid"):
            with tempfile.TemporaryDirectory() as d:
                paths = self.fixtures(Path(d))
                bad = Path(d) / "bad.png"
                bad.write_bytes(b"bad")
                with mock.patch.object(linux, "comfy_upload", side_effect=str), \
                     mock.patch.object(linux, "comfy_submit", return_value="pid"), \
                     mock.patch.object(linux, "comfy_poll", return_value={}), \
                     mock.patch.object(linux, "comfy_fetch_output", return_value=str(bad) if invalid else None):
                    with self.assertRaises(RuntimeError):
                        linux._tryon_compose_camera("job", "qwen", *map(str, paths), "camera", {})

    def test_qwen_camera_size_is_serialized_and_ordinary_size_is_omitted(self):
        self.assertIn("size", __import__("inspect").signature(linux._qwen_max_edit).parameters)
        bodies = []
        def post(url, **kwargs):
            bodies.append(kwargs["json"])
            return mock.Mock(status_code=200, json=lambda: {"output": {"choices": [
                {"message": {"content": [{"image": "https://example.test/image"}]}}]}})
        with mock.patch.object(linux.requests, "post", post, create=True), \
             mock.patch.object(linux, "QWEN_IMAGE_BASE", "https://example.test"), \
             mock.patch.object(linux, "_hf_fetch", return_value="out.png"):
            linux._qwen_max_edit([(b"x", "image/png")], "prompt", "fake", "camera.png", size="752*1328")
            linux._qwen_max_edit([(b"x", "image/png")], "prompt", "fake", "ordinary.png")
        self.assertEqual(bodies[0]["parameters"]["size"], "752*1328")
        self.assertEqual(bodies[1]["parameters"], {"watermark": False})

    def test_ordinary_background_composition_remains_two_refs(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.fixtures(Path(d))
            graphs = []
            with mock.patch.object(linux, "comfy_upload", side_effect=str), \
                 mock.patch.object(linux, "comfy_submit", side_effect=lambda graph: graphs.append(graph) or "pid"), \
                 mock.patch.object(linux, "comfy_poll", return_value={}), \
                 mock.patch.object(linux, "comfy_fetch_output", return_value=str(paths[0])):
                self.assertEqual(linux._tryon_compose_background("job", *map(str, paths[:2]), "legacy"), str(paths[0]))
            self.assertNotIn("12", graphs[0])
            self.assertEqual(graphs[0]["42"]["class_type"], "VAEEncode")

    def test_run_tryon_rejects_invalid_driver_before_image_edit(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            paths = self.fixtures(tmp)
            bad = tmp / "bad.mp4"
            bad.write_bytes(b"invalid")
            calls = []
            def edit(images, prompt, key, out_path, **kwargs):
                calls.append(images)
                return shutil.copyfile(paths[0], out_path)
            with mock.patch.object(linux, "api_download", side_effect=lambda key, dest: shutil.copyfile(key, dest)), \
                 mock.patch.object(linux, "api_progress"), mock.patch.object(linux, "api_log"), \
                 mock.patch.object(linux, "api_upload_output"), \
                 mock.patch.object(linux, "TRYON_PRODUCT_AUTOCROP", False), \
                 mock.patch.object(linux, "_gemini_edit", edit):
                with self.assertRaises(RuntimeError):
                    linux.run_tryon({"id": "job", "inputs": {"model": str(paths[0]), "product": str(paths[0]),
                                     "background": str(paths[1]), "cameraGuide": str(bad)},
                                     "params": {"provider": "gemini", "cameraAware": True,
                                                "apiKey": "AIza" + "x" * 35}})
            self.assertEqual(calls, [])

    def test_run_tryon_every_provider_publishes_only_camera_composition(self):
        for provider in ("gemini", "qwen-max", "huggingface", "qwen"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                tmp = Path(d)
                person, background, guide = self.fixtures(tmp)
                driver = tmp / "driver.mp4"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(guide),
                                "-t", "0.2", "-pix_fmt", "yuv420p", str(driver)], check=True)
                calls, uploads = [], []
                def edit(images, prompt, key, out_path, **kwargs):
                    calls.append(images)
                    return shutil.copyfile(person, out_path)
                def hf_call(fal_id, payload, *args):
                    calls.append(payload["image_urls"])
                    return {"images": [{"url": "fake"}]}
                def submit(graph):
                    calls.append([node for node in graph.values() if node["class_type"] == "LoadImage"])
                    return "pid"
                for name, replacement in {
                    "api_download": lambda key, dest: shutil.copyfile(key, dest),
                    "api_progress": lambda *args, **kwargs: None, "api_log": lambda *args, **kwargs: None,
                    "api_upload_output": lambda job, out, **kwargs: uploads.append(out),
                    "TRYON_PRODUCT_AUTOCROP": False, "_gemini_edit": edit, "_qwen_max_edit": edit,
                    "_hf_call": hf_call, "_hf_fetch": lambda url, dest: shutil.copyfile(person, dest),
                    "comfy_upload": str, "comfy_submit": submit, "comfy_poll": lambda *args, **kwargs: {},
                    "comfy_fetch_output": lambda *args, **kwargs: str(person),
                }.items():
                    stack.enter_context(mock.patch.object(linux, name, replacement))
                out = linux.run_tryon({"id": "job", "inputs": {"model": str(person), "product": str(person),
                                      "background": str(background), "cameraGuide": str(driver)},
                                      "params": {"provider": provider, "cameraAware": True,
                                                 "apiKey": "hf_" + "x" * 30 if provider == "huggingface" else "AIza" + "x" * 35}})
                self.assertEqual([len(refs) for refs in calls], [2, 3])
                self.assertEqual(uploads, [out])
                self.assertEqual(linux._img_size(out), (90, 160))

    def test_camera_compose_failure_never_uploads_even_after_feet_detailer(self):
        for provider, garment, clean_only in (("gemini", "auto", False), ("qwen-max", "auto", False),
                                               ("huggingface", "auto", False), ("qwen", "auto", False),
                                               ("qwen", "shoes", False), ("gemini", "auto", True)):
            with self.subTest(provider=provider, garment=garment, clean_only=clean_only), \
                 tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                tmp = Path(d)
                person, background, guide = self.fixtures(tmp)
                if garment == "shoes":
                    from PIL import Image
                    Image.new("RGB", (320, 320), "red").save(person)
                driver = tmp / "driver.mp4"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(guide),
                                "-t", "0.2", "-pix_fmt", "yuv420p", str(driver)], check=True)
                uploads, graphs = [], []
                bad = tmp / "invalid.png"
                bad.write_bytes(b"undecodable")
                def edit(images, prompt, key, out_path, **kwargs):
                    return shutil.copyfile(bad if len(images) == 3 else person, out_path)
                def hf_call(fal_id, payload, *args):
                    return {"images": [{"url": str(bad if len(payload["image_urls"]) == 3 else person)}]}
                for name, replacement in {
                    "api_download": lambda key, dest: shutil.copyfile(key, dest),
                    "api_progress": lambda *args, **kwargs: None, "api_log": lambda *args, **kwargs: None,
                    "api_upload_output": lambda *args, **kwargs: uploads.append(args),
                    "TRYON_PRODUCT_AUTOCROP": False, "TRYON_FEET_DETAILER": True,
                    "_gemini_edit": edit, "_qwen_max_edit": edit, "_hf_call": hf_call,
                    "_hf_fetch": lambda url, dest: shutil.copyfile(url, dest),
                    "comfy_upload": str, "comfy_submit": lambda graph: graphs.append(graph) or graph,
                    "comfy_poll": lambda graph, *args, **kwargs: graph,
                    "comfy_fetch_output": lambda graph, **kwargs: str(bad if "12" in graph else person),
                }.items():
                    stack.enter_context(mock.patch.object(linux, name, replacement))
                with self.assertRaises(RuntimeError):
                    linux.run_tryon({"id": "job", "inputs": {"model": str(person), "product": str(person),
                                    "background": str(background), "cameraGuide": str(driver)},
                                    "params": {"provider": provider, "garmentType": garment, "cleanOnly": clean_only,
                                               "cameraAware": True,
                                               "apiKey": "hf_" + "x" * 30 if provider == "huggingface" else "AIza" + "x" * 35}})
                self.assertEqual(uploads, [])
                if garment == "shoes":
                    self.assertEqual(len(graphs), 2)
                    self.assertIn("-feet", graphs[0]["100"]["inputs"]["filename_prefix"])


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

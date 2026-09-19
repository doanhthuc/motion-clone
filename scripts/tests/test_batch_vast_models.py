import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import Manifest, Run
from batchlib.vast_models import (ALL_REGISTRY_IDS, CHARACTER_SWAP_IDS, FLASHVSR_IDS,
                                  WAN_ANIMATE_IDS, check_drift, models_for_manifest,
                                  resolve_stage, total_download_gb)


def _run(run_id: str, pipeline: str, stage_params: dict | None = None) -> Run:
    return Run(id=run_id, pipeline=pipeline, stage_params=stage_params or {})


def _manifest(*runs: Run) -> Manifest:
    return Manifest(path=Path("t.yaml"), runs=list(runs))


class TestResolveStage(unittest.TestCase):
    def test_motion_needs_the_wan_animate_group(self):
        self.assertEqual(resolve_stage("motion", {}), WAN_ANIMATE_IDS)

    def test_camera_motion_needs_the_same_group_as_plain_motion(self):
        self.assertEqual(resolve_stage("camera-motion", {}), WAN_ANIMATE_IDS)

    def test_character_swap_needs_the_scail2_sam3_group(self):
        self.assertEqual(resolve_stage("character-swap", {}), CHARACTER_SWAP_IDS)

    def test_tryon_and_camera_tryon_need_nothing_extra(self):
        self.assertEqual(resolve_stage("tryon", {}), frozenset())
        self.assertEqual(resolve_stage("camera-tryon", {}), frozenset())

    def test_enhance_defaults_to_flashvsr(self):
        self.assertEqual(resolve_stage("enhance", {}), FLASHVSR_IDS)

    def test_enhance_engine_lanczos_needs_nothing_extra(self):
        self.assertEqual(resolve_stage("enhance", {"engine": "lanczos"}), frozenset())

    def test_enhance_engine_seedvr2_needs_nothing_from_this_catalog(self):
        # seedvr2 has its own models, but none are in catalog-motion-transfer.json -- this
        # registry correctly has nothing extra to add, it does not silently misclassify it.
        self.assertEqual(resolve_stage("enhance", {"engine": "seedvr2"}), frozenset())

    def test_enhance_engine_is_case_and_whitespace_insensitive(self):
        self.assertEqual(resolve_stage("enhance", {"engine": " FlashVSR "}), FLASHVSR_IDS)


class TestModelsForManifest(unittest.TestCase):
    def test_unions_ids_across_every_run_and_stage(self):
        m = _manifest(
            _run("r1", "motion-enhance"),
            _run("r2", "character-swap"),
        )
        ids = models_for_manifest(m)
        self.assertEqual(ids, WAN_ANIMATE_IDS | FLASHVSR_IDS | CHARACTER_SWAP_IDS)

    def test_a_manifest_with_only_tryon_needs_nothing_extra(self):
        m = _manifest(_run("r1", "tryon-motion-enhance",
                            {"enhance": {"engine": "lanczos"}}))
        ids = models_for_manifest(m)
        self.assertEqual(ids, WAN_ANIMATE_IDS)  # tryon: nothing, motion: wan, enhance: lanczos=nothing

    def test_empty_manifest_needs_nothing(self):
        self.assertEqual(models_for_manifest(_manifest()), frozenset())


class TestTotalDownloadGb(unittest.TestCase):
    def test_matches_the_measured_wan_animate_group_total(self):
        m = _manifest(_run("r1", "motion-enhance", {"enhance": {"engine": "lanczos"}}))
        gb = total_download_gb(m)
        # Measured 2026-09-19 (spec section 3.3): ~34.4 GB for the Wan 2.2 Animate group.
        self.assertAlmostEqual(gb, 34.4, delta=0.1)

    def test_empty_manifest_is_zero(self):
        self.assertEqual(total_download_gb(_manifest()), 0.0)

    def test_uses_a_custom_catalog_path_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({
                "comfy": [{"id": i, "sizeBytes": 1_000_000_000} for i in WAN_ANIMATE_IDS],
            }))
            m = _manifest(_run("r1", "motion-enhance", {"enhance": {"engine": "lanczos"}}))
            gb = total_download_gb(m, catalog_path=catalog)
            self.assertAlmostEqual(gb, len(WAN_ANIMATE_IDS) * 1.0, delta=0.01)


class TestCheckDrift(unittest.TestCase):
    def test_the_real_catalog_and_registry_agree(self):
        self.assertEqual(check_drift(), [])

    def test_a_stage_missing_from_the_registry_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({"comfy": [
                {"id": i, "sizeBytes": 1} for i in ALL_REGISTRY_IDS
            ]}))
            import batchlib.vast_models as vm
            old = dict(vm.STAGE_MODEL_IDS)
            try:
                del vm.STAGE_MODEL_IDS["enhance"]
                errors = check_drift(catalog_path=catalog)
            finally:
                vm.STAGE_MODEL_IDS.clear()
                vm.STAGE_MODEL_IDS.update(old)
            self.assertTrue(any("enhance" in e for e in errors))

    def test_a_registry_id_missing_from_the_catalog_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({"comfy": [
                {"id": i, "sizeBytes": 1} for i in ALL_REGISTRY_IDS if i != "wan-vae"
            ]}))
            errors = check_drift(catalog_path=catalog)
            self.assertTrue(any("wan-vae" in e for e in errors))

    def test_an_unreadable_catalog_is_one_clear_error_not_a_crash(self):
        errors = check_drift(catalog_path=Path("/nonexistent/catalog.json"))
        self.assertEqual(len(errors), 1)
        self.assertIn("cannot read catalog", errors[0])


if __name__ == "__main__":
    unittest.main()

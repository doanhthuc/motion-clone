import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import Manifest, Run
from batchlib_ext.vast_quote import BOOT_AFTER_RUNNING_S, VastQuote
from tgbot import vast_panel
from tgbot.vast_panel import (build_view, gpu_seconds, parse_enabled, session_usd,
                              spend_blockers, static_blockers)


def _manifest(*runs):
    return Manifest(path=Path("batch/m.yaml"), runs=list(runs))


MOTION = Run(id="r1", pipeline="motion-enhance", stage_params={"enhance": {"engine": "lanczos"}})


def _quote(**over):
    base = dict(offer_id=1, machine_id=7, dph=0.90, gpu="RTX 5090", location="Bulgaria, BG",
                ready_s=556.0, known=False, bandwidth_usd=0.10, gb=51.8, qualifying=4,
                fetched_at=0.0)
    base.update(over)
    return VastQuote(**base)


class TestParseEnabled(unittest.TestCase):
    def test_splits_trims_and_ignores_blanks(self):
        self.assertEqual(parse_enabled(" motion-enhance , ,character-swap "),
                         frozenset({"motion-enhance", "character-swap"}))

    def test_none_and_empty_are_the_empty_set(self):
        self.assertEqual(parse_enabled(None), frozenset())
        self.assertEqual(parse_enabled(""), frozenset())


class TestStaticBlockers(unittest.TestCase):
    def test_an_unenabled_pipeline_is_named(self):
        reasons = static_blockers(_manifest(MOTION), frozenset())
        self.assertEqual(len(reasons), 1)
        self.assertIn("motion-enhance", reasons[0])
        self.assertIn("VAST_ENABLED_PIPELINES", reasons[0])

    def test_an_enabled_pipeline_with_registered_stages_has_no_blockers(self):
        self.assertEqual(static_blockers(_manifest(MOTION), frozenset({"motion-enhance"})), [])

    def test_a_stage_missing_from_the_registry_is_named(self):
        registry = {k: v for k, v in vast_panel.STAGE_MODEL_IDS.items() if k != "enhance"}
        with mock.patch.object(vast_panel, "STAGE_MODEL_IDS", registry):
            reasons = static_blockers(_manifest(MOTION), frozenset({"motion-enhance"}))
        self.assertEqual(reasons, ["stage enhance has no Vast model registry entry"])

    def test_two_runs_of_one_pipeline_are_reported_once(self):
        other = Run(id="r2", pipeline="motion-enhance")
        self.assertEqual(len(static_blockers(_manifest(MOTION, other), frozenset())), 1)

    def test_an_unknown_pipeline_is_blocked_not_a_crash(self):
        reasons = static_blockers(_manifest(Run(id="x", pipeline="nope")), frozenset())
        self.assertEqual(len(reasons), 1)


class TestGpuSeconds(unittest.TestCase):
    def test_sums_the_measured_stage_times(self):
        self.assertEqual(gpu_seconds(_manifest(MOTION)), 247 + 114)

    def test_a_local_tryon_costs_no_gpu_time(self):
        run = Run(id="t", pipeline="tryon-motion-enhance",
                  stage_params={"tryon": {"provider": "gemini"}})
        self.assertEqual(gpu_seconds(_manifest(run)), 247 + 114)

    def test_a_self_hosted_tryon_does_cost_gpu_time(self):
        run = Run(id="t", pipeline="tryon-motion-enhance",
                  stage_params={"tryon": {"provider": "qwen"}})
        self.assertEqual(gpu_seconds(_manifest(run)), 351 + 247 + 114)

    def test_an_unmeasured_stage_falls_back_to_its_timeout_ceiling(self):
        run = Run(id="c", pipeline="character-swap")
        self.assertEqual(gpu_seconds(_manifest(run)),
                         vast_panel.STAGES["character-swap"].timeout_min * 60)


class TestSessionUsd(unittest.TestCase):
    def test_is_gpu_time_from_create_plus_bandwidth(self):
        q = _quote(dph=0.90, ready_s=556.0, bandwidth_usd=0.10)
        expected = 0.90 * (556.0 + BOOT_AFTER_RUNNING_S + 361.0) / 3600 + 0.10
        self.assertAlmostEqual(session_usd(q, 361.0), expected)


class TestSpendBlockers(unittest.TestCase):
    ENABLED = frozenset({"motion-enhance"})

    def _blockers(self, *, credit=10.0, quote=None, enabled=None):
        def credit_fn():
            if isinstance(credit, Exception):
                raise credit
            return credit
        return spend_blockers(_manifest(MOTION),
                              self.ENABLED if enabled is None else enabled,
                              credit_fn=credit_fn, quote=quote)

    def test_nothing_blocks_an_enabled_pipeline_with_enough_credit(self):
        self.assertEqual(self._blockers(quote=_quote()), [])

    def test_the_static_reasons_apply_at_tap_time_too(self):
        self.assertEqual(len(self._blockers(enabled=frozenset())), 1)

    def test_credit_below_the_last_quotes_estimate_blocks(self):
        reasons = self._blockers(credit=0.05, quote=_quote())
        self.assertEqual(len(reasons), 1)
        self.assertIn("below this session's estimate", reasons[0])

    def test_with_no_quote_yet_only_a_readable_account_is_required(self):
        self.assertEqual(self._blockers(credit=0.05, quote=None), [])
        reasons = self._blockers(credit=RuntimeError("no api key"), quote=None)
        self.assertEqual(len(reasons), 1)
        self.assertIn("no api key", reasons[0])


class TestBuildView(unittest.TestCase):
    ENABLED = frozenset({"motion-enhance"})

    def _view(self, *, quote=None, credit=10.0, manifest=None, enabled=None):
        def quote_fn():
            if isinstance(quote, Exception):
                raise quote
            return quote or _quote()

        def credit_fn():
            if isinstance(credit, Exception):
                raise credit
            return credit
        return build_view(manifest or _manifest(MOTION), gb=51.8,
                          enabled=self.ENABLED if enabled is None else enabled,
                          quote_fn=quote_fn, credit_fn=credit_fn)

    def test_the_happy_path_may_spend_and_shows_the_offer_and_the_estimate(self):
        view = self._view()
        self.assertTrue(view.can_spend)
        text = "\n".join(view.lines)
        self.assertIn("$0.90/h", text)
        self.assertIn("Bulgaria, BG", text)
        self.assertIn("52 GB", text)
        self.assertIn("Vast credit: $10.00", text)
        self.assertNotIn("No spend button", text)
        self.assertAlmostEqual(view.session_usd, session_usd(_quote(), 361.0))

    def test_an_unmeasured_machine_says_so_and_a_measured_one_gives_its_own_time(self):
        self.assertIn("not measured", "\n".join(self._view().lines))
        measured = "\n".join(self._view(quote=_quote(known=True, ready_s=35.0)).lines)
        self.assertIn("35 s", measured)
        self.assertNotIn("not measured", measured)

    def test_no_qualifying_machine_hides_the_button_with_the_reason(self):
        view = self._view(quote=RuntimeError("0 of 40 offers qualify (40 over price cap)."))
        self.assertFalse(view.can_spend)
        self.assertIn("40 over price cap", "\n".join(view.lines))

    def test_an_unreadable_account_hides_the_button_and_says_why(self):
        view = self._view(credit=RuntimeError("no api key"))
        self.assertFalse(view.can_spend)
        self.assertIn("no api key", "\n".join(view.lines))

    def test_credit_below_the_estimate_hides_the_button(self):
        view = self._view(credit=0.05)
        self.assertFalse(view.can_spend)
        self.assertIn("below this session's estimate", "\n".join(view.lines))

    def test_an_unenabled_pipeline_hides_the_button_even_when_everything_else_is_fine(self):
        view = self._view(enabled=frozenset())
        self.assertFalse(view.can_spend)
        self.assertIn("no measured Vast session", "\n".join(view.lines))
        self.assertIn("$0.90/h", "\n".join(view.lines))   # the tab still shows the offer

    def test_reasons_are_html_escaped(self):
        view = self._view(quote=RuntimeError("bad <b>tag</b> & more"))
        text = "\n".join(view.lines)
        self.assertIn("bad &lt;b&gt;tag&lt;/b&gt; &amp; more", text)


if __name__ == "__main__":
    unittest.main()

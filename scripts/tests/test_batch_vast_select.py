import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.vast_scoreboard import (BLACKLIST_TTL_S, WORST_KNOWN_PULL_S,
                                          MachineRecord, Scoreboard)
from batchlib_ext.vast_select import (Criteria, dedupe, explain_rejections,
                                      format_table, rank, reject_reason)

NOW = 10_000.0


def offer(**over):
    """A real-shaped RTX 5090 offer (field names and magnitudes from a 2026-09-19 search)."""
    base = dict(id=42230244, machine_id=58908, gpu_name="RTX 5090", num_gpus=1,
                dph_total=0.40, inet_down=1800.0, internet_down_cost_per_tb=2.0,
                disk_bw=3800.0, cpu_ghz=3.0, reliability2=0.99,
                geolocation="Bulgaria, BG", direct_port_count=12, rentable=True)
    base.update(over)
    return base


CRIT = Criteria(max_dph=0.60, min_disk_bw=3000.0, min_cpu_ghz=2.5,
                min_inet_mbps=1000.0, max_down_usd_per_tb=20.0)


def board_with(*records):
    board = Scoreboard({})
    for r in records:
        board.record(r)
    return board


class TestRejectReason(unittest.TestCase):
    def test_a_good_offer_qualifies(self):
        self.assertIsNone(reject_reason(offer(), CRIT, Scoreboard({}), NOW))

    def test_each_criterion_rejects_with_its_own_reason(self):
        cases = [
            (dict(dph_total=0.61), "over price cap"),
            (dict(disk_bw=2999.0), "disk too slow"),
            (dict(cpu_ghz=2.4), "cpu too slow"),
            (dict(inet_down=999.0), "advertised bandwidth too low"),
            (dict(internet_down_cost_per_tb=20.5), "bandwidth too expensive"),
            (dict(internet_down_cost_per_tb=None), "bandwidth cost unknown"),
            (dict(direct_port_count=0), "no direct ports"),
            (dict(rentable=False), "not rentable"),
        ]
        for over, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(reject_reason(offer(**over), CRIT, Scoreboard({}), NOW),
                                 reason)

    def test_a_skipped_offer_is_rejected(self):
        crit = Criteria(0.60, 3000.0, 2.5, 1000.0, 20.0, skip_offers=frozenset({"42230244"}))
        self.assertEqual(reject_reason(offer(), crit, Scoreboard({}), NOW), "skipped")

    def test_a_blacklisted_machine_is_rejected_until_the_ttl_passes(self):
        bad = MachineRecord(58908, 481.0, None, None, measured_at=NOW - 10, outcome="slow_pull")
        self.assertEqual(reject_reason(offer(), CRIT, board_with(bad), NOW),
                         "machine blacklisted")
        later = NOW + BLACKLIST_TTL_S
        self.assertIsNone(reject_reason(offer(), CRIT, board_with(bad), later))

    def test_the_hungarian_flat_forty_dollars_per_tb_is_excluded_by_default(self):
        # A 50 GB boot is $2.00 there against a $0.07 boot at $1.37/TB.
        self.assertEqual(
            reject_reason(offer(internet_down_cost_per_tb=40.0), CRIT, Scoreboard({}), NOW),
            "bandwidth too expensive")


class TestRank(unittest.TestCase):
    def test_bandwidth_is_priced_per_gb_from_the_per_tb_rate(self):
        ranked, _ = rank([offer(internet_down_cost_per_tb=40.0)],
                         Criteria(0.60, 0, 0, 0, 100.0), Scoreboard({}), gb=50.0, now=NOW)
        self.assertAlmostEqual(ranked[0].bandwidth_usd, 2.00)
        ranked, _ = rank([offer(internet_down_cost_per_tb=1.37)],
                         Criteria(0.60, 0, 0, 0, 100.0), Scoreboard({}), gb=50.0, now=NOW)
        self.assertAlmostEqual(ranked[0].bandwidth_usd, 0.0685)

    def test_an_unknown_machine_is_scored_at_the_slowest_ever_seen(self):
        ranked, _ = rank([offer(dph_total=0.36, internet_down_cost_per_tb=0.0)], CRIT,
                         Scoreboard({}), gb=50.0, now=NOW)
        self.assertEqual(ranked[0].ready_s, WORST_KNOWN_PULL_S)
        self.assertFalse(ranked[0].known)
        self.assertAlmostEqual(ranked[0].score, 0.36 * WORST_KNOWN_PULL_S / 3600.0)

    def test_a_known_fast_machine_beats_a_cheaper_unknown_one(self):
        # The point of measuring: 0.60 $/h with a 35 s warm start is cheaper to GET READY than
        # 0.40 $/h that might take 556 s.
        cheap_unknown = offer(id=1, machine_id=1, dph_total=0.40)
        dear_known = offer(id=2, machine_id=2, dph_total=0.60)
        fast = MachineRecord(2, 35.0, None, None, measured_at=NOW - 60, outcome="ok")
        ranked, _ = rank([cheap_unknown, dear_known], CRIT, board_with(fast), gb=50.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [2, 1])
        self.assertTrue(ranked[0].known)
        self.assertEqual(ranked[0].ready_s, 35.0)

    def test_a_tie_goes_to_the_cheaper_machine(self):
        a = offer(id=1, machine_id=1, dph_total=0.50, internet_down_cost_per_tb=0.0)
        b = offer(id=2, machine_id=2, dph_total=0.45, internet_down_cost_per_tb=0.0)
        fast = lambda mid: MachineRecord(mid, 100.0, None, None, NOW, "ok")
        ranked, _ = rank([a, b], CRIT, board_with(fast(1), fast(2)), gb=0.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [2, 1])

    def test_rejections_are_counted_by_reason(self):
        offers = [offer(id=1), offer(id=2, dph_total=0.90), offer(id=3, dph_total=0.95),
                  offer(id=4, disk_bw=100.0)]
        ranked, rejected = rank(offers, CRIT, Scoreboard({}), gb=50.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [1])
        self.assertEqual(rejected, Counter({"over price cap": 2, "disk too slow": 1}))

    def test_an_offer_without_a_machine_id_is_ranked_as_unknown(self):
        ranked, _ = rank([offer(machine_id=None)], CRIT, Scoreboard({}), gb=10.0, now=NOW)
        self.assertIsNone(ranked[0].machine_id)
        self.assertEqual(ranked[0].ready_s, WORST_KNOWN_PULL_S)


class TestHelpers(unittest.TestCase):
    def test_dedupe_keeps_the_first_row_per_offer_id(self):
        rows = [offer(id=1, dph_total=0.4), offer(id=2), offer(id=1, dph_total=0.9)]
        out = dedupe(rows)
        self.assertEqual([o["id"] for o in out], [1, 2])
        self.assertEqual(out[0]["dph_total"], 0.4)

    def test_explain_names_every_reason_and_the_knobs(self):
        text = explain_rejections(Counter({"over price cap": 12, "disk too slow": 3}), 40)
        self.assertIn("0 of 40", text)
        self.assertIn("12 over price cap", text)
        self.assertIn("MAX_DPH", text)
        self.assertIn("VAST_MIN_INET_MBPS", text)

    def test_explain_an_empty_search(self):
        self.assertIn("no offers matched", explain_rejections(Counter(), 0))

    def test_table_marks_measured_versus_unmeasured(self):
        fast = MachineRecord(58908, 35.0, None, None, NOW, "ok")
        ranked, _ = rank([offer(), offer(id=2, machine_id=2)], CRIT, board_with(fast),
                         gb=50.0, now=NOW)
        table = format_table(ranked)
        self.assertIn("measured", table)
        self.assertIn("unmeasured", table)
        self.assertIn("42230244", table)


if __name__ == "__main__":
    unittest.main()

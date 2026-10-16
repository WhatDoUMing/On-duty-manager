import unittest
from copy import deepcopy

from duty_app.engine import (RuleError, add_swap, empty_state, make_config,
                             mark_absence, mark_missed, schedule, swap_conflicts)


def fixture(start="2026-10-12", mop="2026-10-14", count=4):
    state = empty_state()
    state["configs"] = [make_config([{"id": str(i), "name": f"{i}号"} for i in range(1, count+1)],
                                    start, mop_interval=3, first_mop=mop, config_id="base")]
    return state


class EngineTests(unittest.TestCase):
    def test_normal_rotation_and_mopping(self):
        rows = schedule(fixture(), "2026-10-12", "2026-10-18")
        self.assertEqual([r["name"] for r in rows], ["1号", "2号", "3号", "4号", "1号", "2号", "3号"])
        self.assertEqual([r["date"] for r in rows if r["mop"]], ["2026-10-14", "2026-10-17"])

    def test_monday_missed_shifts_wednesday_mop_to_thursday(self):
        original = fixture()
        state = mark_missed(original, "2026-10-12", "2026-10-13")
        rows = schedule(state, "2026-10-12", "2026-10-16")
        self.assertEqual([r["name"] for r in rows], ["1号", "1号", "2号", "3号", "4号"])
        self.assertEqual([r["date"] for r in rows if r["mop"]], ["2026-10-15"])
        self.assertEqual(original, fixture())

    def test_repeated_mark_and_undo(self):
        state = fixture()
        once = mark_missed(state, "2026-10-12", "2026-10-13")
        self.assertEqual(once, mark_missed(once, "2026-10-12", "2026-10-13"))
        self.assertEqual(state, mark_missed(once, "2026-10-12", "2026-10-13", undo=True))

    def test_consecutive_misses_and_mop_task_repeats(self):
        state = fixture(mop="2026-10-12")
        state = mark_missed(state, "2026-10-12", "2026-10-13")
        state = mark_missed(state, "2026-10-13", "2026-10-14")
        rows = schedule(state, "2026-10-12", "2026-10-17")
        self.assertEqual([r["name"] for r in rows[:3]], ["1号"]*3)
        self.assertTrue(all(r["mop"] for r in rows[:3]))
        self.assertTrue(rows[-1]["mop"])

    def test_single_member_cross_year(self):
        state = fixture("2026-12-31", "2026-12-31", 1)
        state = mark_missed(state, "2026-12-31", "2027-01-01")
        self.assertEqual(schedule(state, "2027-01-01", "2027-01-01")[0]["name"], "1号")
        self.assertTrue(schedule(state, "2027-01-04", "2027-01-04")[0]["mop"])

    def test_away_is_date_scoped_and_return_has_priority_next_day(self):
        state = mark_absence(fixture(), "3", "2026-10-13", True)
        self.assertEqual(schedule(state, "2026-10-12", "2026-10-12")[0]["name"], "1号")
        state = mark_absence(state, "3", "2026-10-14", False)
        rows = schedule(state, "2026-10-14", "2026-10-16")
        self.assertEqual([r["name"] for r in rows], ["4号", "3号", "1号"])

    def test_return_order_and_carry_priority(self):
        state = fixture()
        for member in ("4", "3"):
            state = mark_absence(state, member, "2026-10-12", True)
        state = mark_missed(state, "2026-10-12", "2026-10-13")
        for member in ("4", "3"):
            state = mark_absence(state, member, "2026-10-12", False)
        rows = schedule(state, "2026-10-13", "2026-10-16")
        self.assertEqual([r["name"] for r in rows], ["1号", "4号", "3号", "2号"])

    def test_all_away_pauses_mop_and_ordinals(self):
        state = fixture()
        for member in ("1", "2", "3", "4"):
            state = mark_absence(state, member, "2026-10-12", True)
        self.assertIsNone(schedule(state, "2026-10-13", "2026-10-13")[0]["member_id"])
        state = mark_absence(state, "2", "2026-10-13", False)
        rows = schedule(state, "2026-10-14", "2026-10-16")
        self.assertEqual(rows[0]["slot_id"], "base:0")
        self.assertTrue(rows[2]["mop"])

    def test_swap_follows_held_slot_and_mop_stays_with_slot(self):
        state = add_swap(fixture(), "2026-10-14", "2026-10-15", "2026-10-12")
        state = mark_missed(state, "2026-10-12", "2026-10-13")
        rows = schedule(state, "2026-10-15", "2026-10-16")
        self.assertEqual([r["name"] for r in rows], ["4号", "3号"])
        self.assertTrue(rows[0]["mop"])
        self.assertFalse(rows[1]["mop"])
        self.assertEqual(swap_conflicts(state, "2026-10-13"), [])

    def test_swapped_owner_is_retained_when_missed(self):
        state = add_swap(fixture(), "2026-10-12", "2026-10-14", "2026-10-12")
        state = mark_missed(state, "2026-10-12", "2026-10-13")
        rows = schedule(state, "2026-10-12", "2026-10-15")
        self.assertEqual([r["name"] for r in rows], ["3号", "3号", "2号", "1号"])

    def test_new_config_preserves_past_and_reports_swaps(self):
        state = add_swap(fixture(), "2026-10-14", "2026-10-15", "2026-10-12")
        state["configs"].append(make_config([{"id":"1", "name":"1号"}], "2026-10-14", config_id="next"))
        self.assertEqual(schedule(state, "2026-10-13", "2026-10-13")[0]["name"], "2号")
        self.assertEqual(swap_conflicts(state, "2026-10-13"), [state["swaps"][0]["id"]])

    def test_pending_task_survives_config_boundary(self):
        state = mark_missed(fixture(), "2026-10-12", "2026-10-13")
        state["configs"].append(make_config([{"id":"4", "name":"4号"}], "2026-10-13", config_id="next"))
        self.assertEqual([r["name"] for r in schedule(state, "2026-10-13", "2026-10-14")], ["1号", "4号"])

    def test_validation_and_no_mutation(self):
        state = fixture()
        before = deepcopy(state)
        for args in (("2026-10-11", "2026-10-13"), ("bad", "2026-10-13")):
            with self.assertRaises(RuleError):
                mark_missed(state, *args)
        with self.assertRaises(RuleError):
            add_swap(state, "2026-10-12", "2026-10-12", "2026-10-12")
        with self.assertRaises(RuleError):
            make_config(["a", "a"], "2026-10-12")
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()

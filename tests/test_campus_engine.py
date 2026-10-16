from copy import deepcopy
import unittest

from campus_app.engine import (RuleError, absence, add_swap, assessments, cancel_mark,
                               conflicts, empty_state, make_config, mark, overlaps, schedule)


def config(mode="group", cycle="daily", policy="shift", count=2):
    students = [{"id": str(i), "name": f"学生{i}", "number": str(i)} for i in range(1, 7)]
    area = {"id": "room", "name": "教室", "cycle": cycle, "week_policy": policy}
    if mode == "group":
        area["groups"] = [{"id": name, "name": name, "student_ids": members}
                          for name, members in (("A", ["1", "2"]), ("B", ["3", "4"]), ("C", ["5", "6"]))]
    else:
        area.update(student_ids=[s["id"] for s in students], count=count)
    return make_config({"class_name": "高一（1）班", "mode": mode, "students": students, "areas": [area]}, "2026-10-12", "v1")


def state(**kwargs):
    value = empty_state()
    value["configs"] = [config(**kwargs)]
    return value


def penalize(value, kind="group", target="A", days=3, date="2026-10-12", today="2026-10-12", **kw):
    return mark(value, {"area_id": "room", "target_kind": kind, "target_id": target,
                        "reason": "missed", "penalty": "duty", "days": days, "date": date, **kw}, today)


def rows(value, first="2026-10-12", end="2026-10-18"):
    return [d["areas"][0] for d in schedule(value, first, end)]


class CampusEngineTests(unittest.TestCase):
    def test_group_multi_area_independent(self):
        s = state()
        second = deepcopy(s["configs"][0]["areas"][0])
        second.update(id="hall", name="走廊")
        for g in second["groups"]:
            g["id"] += "h"
        s["configs"][0]["areas"].append(second)
        changed = penalize(s)
        d = schedule(changed, "2026-10-13", "2026-10-13")[0]["areas"]
        self.assertEqual([r["group_id"] for r in d], ["A", "Bh"])
        self.assertEqual(s["marks"], [])

    def test_group_three_extra_days_then_resume(self):
        value = penalize(state())
        self.assertEqual([r["group_id"] for r in rows(value)], ["A", "A", "A", "A", "B", "C", "A"])

    def test_individual_batch_wrap_and_single_student(self):
        value = state(mode="individual")
        self.assertEqual([r["student_ids"] for r in rows(value)][:4], [["1", "2"], ["3", "4"], ["5", "6"], ["1", "2"]])
        value["configs"][0]["areas"][0]["student_ids"] = ["1", "2", "3", "4", "5"]
        self.assertEqual(rows(value)[2]["student_ids"], ["5", "1"])
        value["configs"][0]["areas"][0].update(student_ids=["1"], count=1)
        self.assertTrue(all(r["student_ids"] == ["1"] for r in rows(value)))

    def test_individual_penalty_adds_deduplicates_and_accumulates(self):
        value = penalize(state(mode="individual"), "student", "1", 2)
        value = penalize(value, "student", "1", 2)
        r = rows(value)
        self.assertEqual(r[1]["student_ids"], ["3", "4", "1"])
        self.assertEqual(r[3]["student_ids"], ["1", "2"])
        self.assertIn("1", r[4]["student_ids"])
        self.assertNotIn("1", r[5]["student_ids"])

    def test_individual_in_group_does_not_hold_rotation(self):
        value = penalize(state(), "student", "1", 2)
        r = rows(value)
        self.assertEqual([x["group_id"] for x in r[:3]], ["A", "B", "C"])
        self.assertEqual(r[1]["student_ids"], ["3", "4", "1"])

    def test_group_queue_fifo(self):
        value = penalize(state(), days=2)
        value = penalize(value, target="C", days=2)
        self.assertEqual([r["group_id"] for r in rows(value)], ["A", "A", "A", "C", "C", "B", "C"])

    def test_week_shift_full_seven_days_after_penalty(self):
        value = penalize(state(cycle="weekly"))
        r = rows(value, end="2026-11-01")
        self.assertEqual([x["group_id"] for x in r[:10]], ["A"]*10)
        self.assertEqual([x["group_id"] for x in r[10:17]], ["B"]*7)

    def test_calendar_week_penalty_keeps_monday_handoff(self):
        value = penalize(state(cycle="weekly", policy="calendar"), target="C", days=9)
        r = rows(value, end="2026-10-27")
        self.assertEqual(r[1]["group_id"], "C")
        self.assertEqual(r[10]["group_id"], "B")
        self.assertEqual(r[14]["group_id"], "C")

    def test_cancel_preserves_past_and_today(self):
        value = penalize(state(), days=5)
        old = rows(value)
        value = cancel_mark(value, value["marks"][0]["id"], "2026-10-14")
        new = rows(value)
        self.assertEqual(new[:3], old[:3])
        self.assertEqual(new[3]["group_id"], "B")
        self.assertEqual(assessments(value, "month", "2026-10-14")["details"], [])

    def test_record_only_and_separate_counts(self):
        value = penalize(state(), penalty="other")
        value = penalize(value, "student", "1", penalty="other", reason="poor")
        self.assertEqual([r["group_id"] for r in rows(value)][:3], ["A", "B", "C"])
        report = assessments(value, "week", "2026-10-14")
        self.assertEqual(report["start"], "2026-10-12")
        self.assertEqual([r["total"] for r in report["summary"]], [1, 1])

    def test_backdated_mark_only_changes_future(self):
        value = penalize(state(), date="2026-10-12", today="2026-10-15")
        self.assertEqual([r["group_id"] for r in rows(value)][:5], ["A", "B", "C", "A", "A"])

    def test_absence_group_keeps_scheduled_group(self):
        value = absence(state(), "1", True, "2026-10-12")
        value = absence(value, "2", True, "2026-10-12")
        r = rows(value)
        self.assertEqual(r[0]["group_id"], "A")
        self.assertEqual(r[0]["student_ids"], [])
        self.assertEqual(r[0]["absent_ids"], ["1", "2"])
        self.assertEqual(r[1]["group_id"], "B")

    def test_individual_absence_and_return_fifo(self):
        value = state(mode="individual", count=1)
        for sid in ("1", "2"):
            value = absence(value, sid, True, "2026-10-12")
        value = absence(value, "2", False, "2026-10-13")
        value = absence(value, "1", False, "2026-10-13")
        r = rows(value)
        self.assertEqual([r["student_ids"] for r in r[:4]], [["3"], ["4"], ["2"], ["1"]])

    def test_all_individuals_away_pause(self):
        value = state(mode="individual")
        for sid in ("1", "2", "3", "4", "5", "6"):
            value = absence(value, sid, True, "2026-10-12")
        self.assertEqual(rows(value)[0]["student_ids"], [])
        value = absence(value, "1", False, "2026-10-14")
        self.assertEqual(rows(value)[3]["student_ids"], ["1"])

    def test_overlap_acknowledgement_only_clears_known_pairs(self):
        cfg = config()
        cfg["areas"][0]["groups"][1]["student_ids"].append("1")
        warning = overlaps(cfg)
        self.assertEqual(len(warning), 1)
        self.assertEqual(overlaps(cfg, [warning[0]["key"]]), [])
        cfg["areas"][0]["groups"][2]["student_ids"].append("1")
        self.assertEqual(len(overlaps(cfg, [warning[0]["key"]])), 2)

    def test_swap_moves_with_logical_task(self):
        value = add_swap(state(), "room", "2026-10-13", "2026-10-14", "2026-10-12")
        value = penalize(value)
        r = rows(value)
        self.assertEqual([r["group_id"] for r in r[4:6]], ["C", "B"])
        self.assertEqual(conflicts(value, "2026-10-12"), [])

    def test_configuration_orphans_swap_and_unfinished_penalty(self):
        value = add_swap(state(), "room", "2026-10-13", "2026-10-14", "2026-10-12")
        value = penalize(value)
        cfg = config()
        cfg.update(id="v2", effective="2026-10-13")
        cfg["areas"][0]["groups"] = cfg["areas"][0]["groups"][1:]
        value["configs"].append(cfg)
        self.assertEqual({c["kind"] for c in conflicts(value, "2026-10-12")}, {"mark", "swap"})

    def test_rename_keeps_rotation_and_task_ids(self):
        value = state()
        before = rows(value)
        cfg = deepcopy(value["configs"][0])
        cfg.update(id="v2", effective="2026-10-14", class_name="新名称")
        cfg["areas"][0]["groups"][0]["name"] = "重新命名的组"
        value["configs"].append(cfg)
        self.assertEqual([r["task_id"] for r in rows(value)], [r["task_id"] for r in before])

    def test_queued_penalty_conflict_uses_actual_remaining_days(self):
        value = penalize(state(), days=10)
        value = penalize(value, target="C", days=2)
        cfg = config()
        cfg.update(id="v2", effective="2026-10-21")
        cfg["areas"][0]["groups"].pop()
        value["configs"].append(cfg)
        found = conflicts(value, "2026-10-20")
        self.assertEqual([c["id"] for c in found if c["kind"] == "mark"], [value["marks"][1]["id"]])

    def test_swap_remaining_future_after_original_dates_pass(self):
        value = add_swap(state(), "room", "2026-10-13", "2026-10-14", "2026-10-12")
        value = penalize(value, days=10)
        self.assertEqual(conflicts(value, "2026-10-20"), [])
        self.assertEqual(rows(value, "2026-10-23", "2026-10-24")[0]["group_id"], "C")

    def test_personal_week_and_specific_target_report(self):
        value = state(mode="individual", cycle="weekly")
        self.assertEqual(rows(value)[-1]["student_ids"], ["1", "2"])
        self.assertEqual(rows(value, "2026-10-19", "2026-10-19")[0]["student_ids"], ["3", "4"])
        value = penalize(value, "student", "1", penalty="other")
        value = penalize(value, "student", "2", penalty="other")
        report = assessments(value, "month", "2026-10-12", target_id="1")
        self.assertEqual(len(report["details"]), 1)

    def test_cross_year_month_statistics(self):
        value = state()
        value["configs"][0]["effective"] = "2026-12-31"
        value = penalize(value, date="2026-12-31", today="2026-12-31")
        self.assertEqual(assessments(value, "month", "2026-12-31")["end"], "2026-12-31")
        self.assertEqual(assessments(value, "month", "2027-01-01")["details"], [])
        self.assertEqual(rows(value, "2027-01-01", "2027-01-03")[0]["group_id"], "A")

    def test_validation(self):
        cfg = config(mode="individual")
        cfg["areas"][0]["count"] = 7
        with self.assertRaises(RuleError):
            make_config(cfg, "2026-10-12")
        with self.assertRaises(RuleError):
            penalize(state(), days=True)
        with self.assertRaises(RuleError):
            penalize(state(), date="2027-01-01")
        with self.assertRaises(RuleError):
            add_swap(state(), "room", "2026-10-12", "2026-10-13", "2026-10-12")


if __name__ == "__main__":
    unittest.main()

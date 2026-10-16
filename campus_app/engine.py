"""Pure, replayable multi-area schedules. Calendar dates never come from the clock.

Regular tasks have stable identities. Group penalties either hold the logical
clock or overlay calendar weeks; individual penalties add people, never slots.
Cancellation has a date boundary, so yesterday's assignments remain unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import re
from uuid import uuid4

from duty_app.engine import RuleError, day


def empty_state():
    return {"schema_version": 2, "configs": [], "attendance": [], "marks": [],
            "swaps": [], "overlap_confirmations": [],
            "settings": {"reminder_time": "22:00", "reminder_mode": "popup",
                         "paused": False, "widget_visible": True}}


def identifier(value=None):
    value = uuid4().hex if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"[\w-]{1,80}", value, re.ASCII):
        raise RuleError("标识需为 1 至 80 位字母、数字、下划线或连字符")
    return value


def text(value, label, limit=40):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise RuleError(f"{label}需为 1 至 {limit} 个字符")
    return value.strip()


def integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RuleError(f"{label}需为 {low} 至 {high} 的整数")
    return value


def _refs(values, valid, label):
    if (not isinstance(values, list) or not values
            or any(not isinstance(v, str) or v not in valid for v in values) or len(set(values)) != len(values)):
        raise RuleError(f"{label}需从学生名单选择，且组内人员应唯一")
    return list(values)


def make_config(body, effective, config_id=None):
    day(effective)
    if not isinstance(body, dict) or body.get("mode") not in ("group", "individual"):
        raise RuleError("请选择小组模式或个人模式")
    students = body.get("students")
    if not isinstance(students, list) or not 1 <= len(students) <= 300:
        raise RuleError("请设置 1 至 300 位学生")
    normalized = []
    for item in students:
        if not isinstance(item, dict):
            raise RuleError("学生格式有误")
        number = item.get("number", "")
        if not isinstance(number, str) or len(number.strip()) > 40:
            raise RuleError("学号长度需在 40 字以内")
        normalized.append({"id": identifier(item.get("id")),
                           "name": text(item.get("name"), "姓名"), "number": number.strip()})
    ids = {s["id"] for s in normalized}
    numbers = [s["number"] for s in normalized if s["number"]]
    if len(ids) != len(normalized) or len(numbers) != len(set(numbers)):
        raise RuleError("学生标识或学号重复")
    areas, area_ids, group_ids = [], set(), set()
    if not isinstance(body.get("areas"), list) or not 1 <= len(body["areas"]) <= 30:
        raise RuleError("请设置 1 至 30 个值日区域")
    for item in body["areas"]:
        if not isinstance(item, dict):
            raise RuleError("区域格式有误")
        aid = identifier(item.get("id"))
        if aid in area_ids:
            raise RuleError("区域标识重复")
        area_ids.add(aid)
        cycle, policy = item.get("cycle", "daily"), item.get("week_policy", "shift")
        if cycle not in ("daily", "weekly") or policy not in ("shift", "calendar"):
            raise RuleError("轮换周期或按周处罚策略有误")
        area = {"id": aid, "name": text(item.get("name"), "区域名称"),
                "cycle": cycle, "week_policy": policy}
        if body["mode"] == "group":
            groups = item.get("groups")
            if not isinstance(groups, list) or not 1 <= len(groups) <= 100:
                raise RuleError("每个区域请设置 1 至 100 个小组")
            area["groups"] = []
            for group in groups:
                if not isinstance(group, dict):
                    raise RuleError("小组格式有误")
                gid = identifier(group.get("id"))
                if gid in group_ids:
                    raise RuleError("小组标识需在所有区域中唯一")
                group_ids.add(gid)
                area["groups"].append({"id": gid, "name": text(group.get("name"), "小组名称"),
                                       "student_ids": _refs(group.get("student_ids"), ids, "组员")})
        else:
            area["student_ids"] = _refs(item.get("student_ids"), ids, "区域人员")
            area["count"] = integer(item.get("count", 1), "每日人数", 1, len(area["student_ids"]))
        areas.append(area)
    return {"id": identifier(config_id), "effective": effective,
            "class_name": text(body.get("class_name", "我的班级"), "班级名称"),
            "mode": body["mode"], "students": normalized, "areas": areas}


def current_config(state, ds):
    return next(iter(sorted((c for c in state["configs"] if c["effective"] <= ds),
                            key=lambda c: c["effective"], reverse=True)), None)


def overlaps(config, confirmations=()):
    memberships = defaultdict(list)
    for area in config["areas"]:
        for group in area.get("groups", []):
            for sid in group["student_ids"]:
                memberships[sid].append({"id": group["id"], "name": group["name"], "area": area["name"]})
    names = {s["id"]: s["name"] for s in config["students"]}
    result = []
    for sid, groups in memberships.items():
        for i, first in enumerate(groups):
            for second in groups[i+1:]:
                key = hashlib.sha256(json.dumps([sid, *sorted([first["id"], second["id"]])]).encode()).hexdigest()
                if key not in confirmations:
                    result.append({"key": key, "student_id": sid, "name": names[sid], "groups": [first, second]})
    return result


def _signature(config, area):
    content = {k: v for k, v in area.items() if k != "name"}
    if "groups" in content:
        content["groups"] = [{k: v for k, v in g.items() if k != "name"} for g in content["groups"]]
    return json.dumps([config["mode"], content], sort_keys=True)


def _cancelled(item, ds):
    return bool(item.get("cancelled_from") and ds >= item["cancelled_from"])


def schedule(state, start, end, _progress=None):
    first, last = day(start), day(end)
    if first > last or (last-first).days > 730:
        raise RuleError("请查询不超过两年的日期范围")
    configs = sorted(state["configs"], key=lambda c: c["effective"])
    if not configs:
        return [{"date": (first+timedelta(days=i)).isoformat(), "areas": []} for i in range((last-first).days+1)]
    origin = day(configs[0]["effective"])
    if (last-origin).days > 36525:
        raise RuleError("排班跨度超过一百年")
    attendance = defaultdict(list)
    for event in state["attendance"]:
        attendance[event["date"]].append(event)
    overrides = defaultdict(list)
    for swap in state["swaps"]:
        overrides[swap["first_task"]].append((swap, swap["second_owner"]))
        overrides[swap["second_task"]].append((swap, swap["first_owner"]))
    area_marks = defaultdict(list)
    for record in state["marks"]:
        area_marks[record["area_id"]].append(record)
    consumed = defaultdict(int)
    away, contexts, result = set(), {}, []
    cfg_index, cfg = -1, None
    current = min(first, origin)
    while current <= last:
        ds = current.isoformat()
        while cfg_index+1 < len(configs) and configs[cfg_index+1]["effective"] <= ds:
            cfg_index += 1
            cfg = configs[cfg_index]
        returning = []
        for event in attendance[ds]:
            if event["away"]:
                away.add(event["student_id"])
                for context in contexts.values():
                    context["priority"] = [s for s in context["priority"] if s != event["student_id"]]
            else:
                away.discard(event["student_id"])
                returning.append(event["student_id"])
        rows = []
        for area in cfg["areas"] if cfg else []:
            aid = area["id"]
            signature = _signature(cfg, area)
            context = contexts.get(aid)
            if context is None or context["signature"] != signature:
                context = contexts[aid] = {"signature": signature, "epoch": cfg["id"], "start": cfg["effective"],
                                           "ordinal": 0, "rotation": list(area.get("student_ids", [])),
                                           "priority": [], "round": None, "batch": []}
            for sid in returning:
                if sid in context["rotation"] and sid not in context["priority"]:
                    context["priority"].append(sid)
            groups = {g["id"]: g for g in area.get("groups", [])}
            duration = 7 if area["cycle"] == "weekly" else 1
            calendar = area["cycle"] == "weekly" and area["week_policy"] == "calendar"
            ordinal = ((current-day(context["start"])).days + day(context["start"]).weekday()
                       if calendar else context["ordinal"])
            normal_task = f'{context["epoch"]}:{aid}:{ordinal}'
            eligible = [m for m in area_marks[aid] if m["penalty"] == "duty"
                        and m["starts"] <= ds and not _cancelled(m, ds) and consumed[m["id"]] < m["days"]]
            group_penalty = next((m for m in eligible if m["target_kind"] == "group" and m["target_id"] in groups), None)
            group_id, group_name, swap_id, conflict = None, None, None, False
            normal_students = []
            if cfg["mode"] == "group":
                group = area["groups"][(ordinal//duration) % len(area["groups"])]
                if group_penalty:
                    group = groups[group_penalty["target_id"]]
                group_id, group_name = group["id"], group["name"]
                normal_students = list(group["student_ids"])
            else:
                available = [s for s in context["rotation"] if s not in away]
                round_index = ordinal//duration
                if context["round"] != round_index:
                    context["round"], context["batch"] = round_index, []
                ordered = list(dict.fromkeys(context["priority"] + context["batch"] + available))
                normal_students = [s for s in ordered if s in available][:area["count"]]
                context["batch"] = list(normal_students)
                context["priority"] = [s for s in context["priority"] if s not in normal_students]
            task_id = (f'penalty:{group_penalty["id"]}:{consumed[group_penalty["id"]]}'
                       if group_penalty else normal_task)
            if not group_penalty:
                for swap, owner in overrides.get(task_id, []):
                    if swap["starts"] > ds or _cancelled(swap, ds):
                        continue
                    swap_id = swap["id"]
                    valid_students = set(s["id"] for s in cfg["students"])
                    if cfg["mode"] == "group":
                        proposed = groups.get(owner.get("group_id"))
                        conflict = proposed is None
                        if proposed:
                            group_id, group_name, normal_students = proposed["id"], proposed["name"], proposed["student_ids"][:]
                    else:
                        proposed = owner.get("student_ids", [])
                        conflict = (not proposed or len(proposed) != area["count"] or
                                    not set(proposed) <= set(context["rotation"]) or bool(set(proposed) & away))
                        if not conflict and set(proposed) <= valid_students:
                            normal_students = list(proposed)
            individual = {}
            for mark in eligible:
                if mark["target_kind"] == "student" and mark["target_id"] not in individual:
                    individual[mark["target_id"]] = mark
            for mark in ([group_penalty] if group_penalty else []) + list(individual.values()):
                consumed[mark["id"]] += 1
            planned = list(dict.fromkeys(normal_students + list(individual)))
            names = {s["id"]: s for s in cfg["students"]}
            planned = [s for s in planned if s in names]
            row = {"area_id": aid, "area_name": area["name"], "task_id": task_id,
                   "group_id": group_id, "group_name": group_name, "normal_student_ids": normal_students,
                   "student_ids": [s for s in planned if s not in away],
                   "absent_ids": [s for s in planned if s in away], "planned_ids": planned,
                   "students": [dict(names[s], absent=s in away) for s in planned],
                   "penalty_ids": ([group_penalty["id"]] if group_penalty else []) + [m["id"] for m in individual.values()],
                   "extra_ids": list(individual), "swap_id": swap_id, "conflict": conflict,
                   "is_group_penalty": bool(group_penalty), "mode": cfg["mode"]}
            rows.append(row)
            if _progress is not None and task_id in overrides:
                _progress.setdefault("executed", set()).add(task_id)
            if not group_penalty and (cfg["mode"] == "group" or normal_students):
                if cfg["mode"] == "individual" and (ordinal+1) % duration == 0:
                    for sid in context["batch"]:
                        context["rotation"].remove(sid)
                        context["rotation"].append(sid)
                    context["batch"] = []
                context["ordinal"] += 1
        if current >= first:
            result.append({"date": ds, "areas": rows})
        current += timedelta(days=1)
    if _progress is not None:
        _progress["consumed"] = dict(consumed)
    return result


def mark(state, body, today):
    incident = body.get("date", today)
    if day(incident) > day(today):
        raise RuleError("考核发生日期请设置为今天或过去")
    cfg = current_config(state, incident)
    if not cfg:
        raise RuleError("该日期尚未开始排班")
    area = next((a for a in cfg["areas"] if a["id"] == body.get("area_id")), None)
    if not area:
        raise RuleError("该日期的区域不存在")
    kind, target = body.get("target_kind"), body.get("target_id")
    members = {s["id"]: s for s in cfg["students"]}
    targets = {g["id"]: g for g in area.get("groups", [])} if kind == "group" else members
    if kind not in ("group", "student") or target not in targets:
        raise RuleError("请选择有效的小组或个人")
    if kind == "student":
        roster = set(area.get("student_ids", [])) | {s for g in area.get("groups", []) for s in g["student_ids"]}
        if target not in roster:
            raise RuleError("该学生不在所选区域名单中")
    if body.get("reason") not in ("missed", "poor") or body.get("penalty") not in ("duty", "other"):
        raise RuleError("请选择忘做或值日较差，以及处罚方式")
    note = body.get("note", "")
    if not isinstance(note, str) or len(note) > 1000:
        raise RuleError("备注请控制在 1000 字以内")
    record = {"id": identifier(body.get("id")), "date": incident, "created": today,
              "starts": (day(today)+timedelta(days=1)).isoformat(), "area_id": area["id"], "area_name": area["name"],
              "target_kind": kind, "target_id": target, "target_name": targets[target]["name"],
              "reason": body["reason"], "penalty": body["penalty"], "note": note,
              "days": integer(body.get("days", 1), "连续值日天数", 1, 365) if body["penalty"] == "duty" else 0}
    if any(m["id"] == record["id"] for m in state["marks"]):
        raise RuleError("标记标识重复，请刷新后重试")
    updated = deepcopy(state)
    updated["marks"].append(record)
    return updated


def cancel_mark(state, mark_id, today):
    updated = deepcopy(state)
    record = next((m for m in updated["marks"] if m["id"] == mark_id), None)
    if not record:
        raise RuleError("标记记录不存在")
    if not record.get("cancelled_from"):
        record["cancelled_from"] = (day(today)+timedelta(days=1)).isoformat()
        record["cancelled_on"] = today
    return updated


def absence(state, student_id, away, today):
    cfg = current_config(state, today)
    if not cfg or student_id not in {s["id"] for s in cfg["students"]} or not isinstance(away, bool):
        raise RuleError("请选择有效学生及请假状态")
    updated = deepcopy(state)
    effective = today if away else (day(today)+timedelta(days=1)).isoformat()
    events = [e for e in updated["attendance"] if e["student_id"] == student_id]
    latest = sorted(events, key=lambda e: e["date"])[-1] if events else None
    if latest and latest["away"] == away:
        return updated
    updated["attendance"] = [e for e in updated["attendance"] if not (e["student_id"] == student_id and e["date"] > today)]
    updated["attendance"].append({"id": identifier(), "student_id": student_id, "away": away, "date": effective})
    return updated


def add_swap(state, aid, first, second, today):
    if day(first) <= day(today) or day(second) <= day(today) or first == second:
        raise RuleError("请选择两个不同的未来日期")
    if abs((day(first)-day(today)).days) > 730 or abs((day(second)-day(today)).days) > 730:
        raise RuleError("调班日期请设置在两年内")
    rows = []
    for ds in (first, second):
        row = next((r for r in schedule(state, ds, ds)[0]["areas"] if r["area_id"] == aid), None)
        if not row or row["is_group_penalty"] or row["swap_id"] or not row["normal_student_ids"] or row["conflict"]:
            raise RuleError("请选择有正常排班、尚未调班且没有冲突的任务")
        rows.append(row)
    if rows[0]["mode"] != rows[1]["mode"]:
        raise RuleError("两天的排班模式不同，请调整配置后再调班")
    updated = deepcopy(state)
    updated["swaps"].append({"id": identifier(), "area_id": aid,
                             "first_task": rows[0]["task_id"], "second_task": rows[1]["task_id"],
                             "first_owner": {"group_id": rows[0]["group_id"], "student_ids": rows[0]["normal_student_ids"]},
                             "second_owner": {"group_id": rows[1]["group_id"], "student_ids": rows[1]["normal_student_ids"]},
                             "first_date": first, "second_date": second,
                             "starts": (day(today)+timedelta(days=1)).isoformat()})
    return updated


def conflicts(state, today):
    """Find orphaned future swaps and penalties under the pending configuration."""
    tomorrow = (day(today)+timedelta(days=1)).isoformat()
    cfg = current_config(state, tomorrow)
    result = []
    if not cfg:
        return result
    areas = {a["id"]: a for a in cfg["areas"]}
    progress = {}
    schedule(state, today, today, progress)
    for m in state["marks"]:
        if m["penalty"] != "duty" or _cancelled(m, tomorrow):
            continue
        area = areas.get(m["area_id"])
        valid = area and (m["target_id"] in {g["id"] for g in area.get("groups", [])} if m["target_kind"] == "group"
                         else m["target_id"] in set(area.get("student_ids", [])) | {s for g in area.get("groups", []) for s in g["student_ids"]})
        if not valid and progress.get("consumed", {}).get(m["id"], 0) < m["days"]:
            result.append({"kind": "mark", "id": m["id"], "label": m["target_name"]})
    active = [s for s in state["swaps"] if not _cancelled(s, tomorrow)
              and any(s[k] not in progress.get("executed", set()) for k in ("first_task", "second_task"))]
    if active:
        # Logical tasks may move. Search beyond original dates by all added days.
        extra = min(730, sum(m["days"] for m in state["marks"] if m["penalty"] == "duty" and m["target_kind"] == "group"))
        end = max(day(tomorrow), min(day(tomorrow)+timedelta(days=730), max(day(max(s["first_date"], s["second_date"])) for s in active)+timedelta(days=extra+7)))
        seen = {r["task_id"]: r for d in schedule(state, tomorrow, end.isoformat()) for r in d["areas"]}
        for s in active:
            needed = [s[k] for k in ("first_task", "second_task") if s[k] not in progress.get("executed", set())]
            if any(k not in seen or seen[k]["conflict"] for k in needed):
                result.append({"kind": "swap", "id": s["id"], "label": f'{s["first_date"]} ↔ {s["second_date"]}'})
    return result


def assessments(state, period, anchor, area_id=None, target_kind=None, reason=None, target_id=None):
    anchor = day(anchor)
    if period == "week":
        start = anchor-timedelta(days=anchor.weekday())
        end = start+timedelta(days=6)
    elif period == "month":
        start = anchor.replace(day=1)
        end = (start.replace(year=start.year+1, month=1) if start.month == 12 else start.replace(month=start.month+1))-timedelta(days=1)
    else:
        raise RuleError("统计周期请选择 week 或 month")
    detail = [deepcopy(m) for m in state["marks"] if not m.get("cancelled_on")
              and start.isoformat() <= m["date"] <= end.isoformat()
              and (not area_id or m["area_id"] == area_id) and (not target_kind or m["target_kind"] == target_kind)
              and (not target_id or m["target_id"] == target_id)
              and (not reason or m["reason"] == reason)]
    summary = {}
    for m in detail:
        key = (m["area_id"], m["target_kind"], m["target_id"])
        row = summary.setdefault(key, {k: m[k] for k in ("area_id", "area_name", "target_kind", "target_id", "target_name")} | {"total": 0, "missed": 0, "poor": 0})
        row["total"] += 1
        row[m["reason"]] += 1
    return {"start": start.isoformat(), "end": end.isoformat(), "summary": list(summary.values()), "details": detail}

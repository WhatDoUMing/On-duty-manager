"""Deterministic day-by-day scheduling. No IO, system clock or GUI dependencies.

An ordinal counts *completed logical duty days*, rather than calendar days.
A missed day (or an all-away day) does not advance it. Swaps address these
stable slots, so neither swaps nor the mop cycle drift when a day is held.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from uuid import uuid4


class RuleError(ValueError):
    pass


def day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise RuleError("日期格式应为 YYYY-MM-DD") from None


def empty_state() -> dict:
    return {"configs": [], "events": [], "swaps": [],
            "settings": {"reminder_time": "22:00", "paused": False}}


def make_config(members: list, effective: str, first_member: str | None = None,
                mop_interval: int = 3, first_mop: str | None = None,
                config_id: str | None = None) -> dict:
    day(effective)
    day(first_mop or effective)
    if not isinstance(members, list) or not 1 <= len(members) <= 100:
        raise RuleError("请设置 1 至 100 位成员")
    normalized = []
    for member in members:
        if isinstance(member, str):
            member = {"id": uuid4().hex, "name": member}
        if not isinstance(member, dict):
            raise RuleError("成员格式有误")
        name = str(member.get("name", "")).strip()
        member_id = member.get("id") or uuid4().hex
        if not name or len(name) > 40 or not isinstance(member_id, str) or len(member_id) > 80:
            raise RuleError("成员姓名需为 1 至 40 个字符")
        normalized.append({"id": member_id, "name": name})
    if len({m["id"] for m in normalized}) != len(normalized):
        raise RuleError("成员标识重复")
    if len({m["name"] for m in normalized}) != len(normalized):
        raise RuleError("请用不同姓名区分成员")
    if isinstance(mop_interval, bool) or not isinstance(mop_interval, int) or not 1 <= mop_interval <= 30:
        raise RuleError("拖地间隔需为 1 至 30 天")
    first_member = first_member or normalized[0]["id"]
    if first_member not in {m["id"] for m in normalized}:
        raise RuleError("起始值日生不在名单中")
    return {"id": config_id or uuid4().hex, "effective": effective,
            "members": normalized, "first_member": first_member,
            "mop_interval": mop_interval, "first_mop": first_mop or effective}


def schedule(state: dict, start: str, end: str) -> list[dict]:
    start_day, end_day = day(start), day(end)
    if start_day > end_day or (end_day - start_day).days > 730:
        raise RuleError("请查询不超过两年的日期范围")
    configs = sorted(state["configs"], key=lambda c: c["effective"])
    if not configs:
        return [_blank(d) for d in _days(start_day, end_day)]
    first_day = day(configs[0]["effective"])
    if (end_day - first_day).days > 36525:
        raise RuleError("排班跨度超过一百年，请调整起始日期")
    events = defaultdict(list)
    missed = set()
    for event in state["events"]:
        if event["kind"] == "missed":
            missed.add(event["date"])
        else:
            events[event["date"]].append(event)
    overrides = {}
    for swap in state["swaps"]:
        if not swap.get("cancelled"):
            overrides.update({swap["first_slot"]: (swap["second_member"], swap["id"]),
                              swap["second_slot"]: (swap["first_member"], swap["id"])})
    away, priority, rotation = set(), [], []
    pending = None
    active_cfg = None
    ordinal = 0
    result = [_blank(d) for d in _days(start_day, min(end_day, first_day - timedelta(days=1)))]
    config_index = -1
    for current in _days(first_day, end_day):
        ds = current.isoformat()
        while config_index + 1 < len(configs) and configs[config_index + 1]["effective"] <= ds:
            config_index += 1
        wanted = configs[config_index]
        # An unfinished task survives a settings version boundary.
        if (active_cfg is None or active_cfg["id"] != wanted["id"]) and pending is None:
            active_cfg = wanted
            rotation = [m["id"] for m in active_cfg["members"]]
            anchor = rotation.index(active_cfg["first_member"])
            rotation = rotation[anchor:] + rotation[:anchor]
            ordinal = 0
        names = {m["id"]: m["name"] for m in active_cfg["members"]}
        for event in events[ds]:
            mid = event["member_id"]
            if event["kind"] == "away":
                away.add(mid)
                priority = [p for p in priority if p != mid]
            elif event["kind"] == "return":
                away.discard(mid)
                if mid not in priority:
                    priority.append(mid)
        available = set(names) - away
        if not available:
            row = _blank(current, "全员回家")
            row["away_ids"] = sorted(away & set(names))
        else:
            priority = [p for p in priority if p in names]
            if pending and pending["member"] in available:
                base, owner = pending["base"], pending["member"]
            else:
                base = next((p for p in priority if p in available), None)
                base = base or next(p for p in rotation if p in available)
                owner = base
                priority = [p for p in priority if p != base]
            slot = f'{active_cfg["id"]}:{ordinal}'
            conflict, swap_id = False, None
            if slot in overrides:
                proposed, swap_id = overrides[slot]
                conflict = proposed not in available
                if not conflict:
                    owner = proposed
            mop_offset = (day(active_cfg["first_mop"]) - day(active_cfg["effective"])).days
            mop = ordinal >= mop_offset and (ordinal - mop_offset) % active_cfg["mop_interval"] == 0
            row = {"date": ds, "slot_id": slot, "member_id": owner,
                   "name": names[owner], "mop": mop, "missed": ds in missed,
                   "carried": pending is not None, "swap_id": swap_id,
                   "conflict": conflict, "away_ids": sorted(away & set(names))}
            if ds in missed:
                pending = {"base": base, "member": owner}
            else:
                if base in rotation:
                    rotation.remove(base)
                    rotation.append(base)
                ordinal += 1
                pending = None
        if current >= start_day:
            result.append(row)
    return result


def _days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _blank(current: date, name: str = "未开始") -> dict:
    return {"date": current.isoformat(), "slot_id": None, "member_id": None,
            "name": name, "mop": False, "missed": False, "carried": False,
            "swap_id": None, "conflict": False, "away_ids": []}


def mark_missed(state: dict, target: str, today: str, undo: bool = False) -> dict:
    if day(target) != day(today) - timedelta(days=1):
        raise RuleError("请使用昨日忘做操作；历史记录保留用于核对")
    updated = deepcopy(state)
    existing = [e for e in updated["events"] if e["kind"] == "missed" and e["date"] == target]
    if undo:
        updated["events"] = [e for e in updated["events"] if e not in existing]
    elif not existing:
        if not schedule(state, target, target)[0]["member_id"]:
            raise RuleError("昨日没有值日任务")
        updated["events"].append({"id": uuid4().hex, "kind": "missed", "date": target})
    return updated


def mark_absence(state: dict, member_id: str, today: str, away: bool) -> dict:
    day(today)
    cfg = current_config(state, today)
    if not cfg or member_id not in {m["id"] for m in cfg["members"]}:
        raise RuleError("请选择名单中的成员")
    updated = deepcopy(state)
    effective = today if away else (day(today) + timedelta(days=1)).isoformat()
    kind = "away" if away else "return"
    # Latest event (including scheduled returns) determines whether this is a no-op.
    related = [e for e in updated["events"] if e.get("member_id") == member_id]
    related.sort(key=lambda e: e["date"])
    last = related[-1] if related else None
    if (away and last and last["kind"] == "away") or (not away and (last is None or last["kind"] == "return")):
        return updated
    if away:
        updated["events"] = [e for e in updated["events"]
                             if not (e.get("member_id") == member_id and e["date"] > today)]
    updated["events"].append({"id": uuid4().hex, "kind": kind,
                              "date": effective, "member_id": member_id})
    return updated


def current_config(state: dict, today: str) -> dict | None:
    eligible = [c for c in state["configs"] if c["effective"] <= today]
    return max(eligible, key=lambda c: c["effective"]) if eligible else None


def add_swap(state: dict, first: str, second: str, today: str) -> dict:
    if day(first) < day(today) or day(second) < day(today) or first == second:
        raise RuleError("请选择两个不同的当日或未来日期")
    if abs((day(second) - day(first)).days) > 730:
        raise RuleError("换班日期跨度请控制在两年内")
    a, b = (schedule(state, d, d)[0] for d in (first, second))
    if not a["member_id"] or not b["member_id"] or a["carried"] or b["carried"]:
        raise RuleError("请选择有值日安排、且未处于补做中的任务")
    if a["swap_id"] or b["swap_id"] or a["slot_id"] == b["slot_id"]:
        raise RuleError("该任务已有换班，请先撤销原换班")
    if a["member_id"] == b["member_id"]:
        raise RuleError("这两天由同一位成员负责，无需交换")
    if b["member_id"] in a["away_ids"] or a["member_id"] in b["away_ids"]:
        raise RuleError("换班成员在目标日期处于回家状态")
    updated = deepcopy(state)
    updated["swaps"].append({"id": uuid4().hex, "first_slot": a["slot_id"],
                             "second_slot": b["slot_id"], "first_member": a["member_id"],
                             "second_member": b["member_id"], "first_date": first,
                             "second_date": second, "cancelled": False})
    return updated


def swap_conflicts(state: dict, today: str) -> list[str]:
    active = [s for s in state["swaps"] if not s.get("cancelled")]
    if not active:
        return []
    # Search beyond original dates to include delayed slots. Unreachable slots
    # after a new configuration version are conflicts as well.
    horizon = max(day(today), *(day(s["second_date"]) for s in active),
                  *(day(s["first_date"]) for s in active)) + timedelta(days=366)
    found = {}
    start = min(day(c["effective"]) for c in state["configs"])
    cursor = start
    while cursor <= horizon:
        stop = min(cursor + timedelta(days=730), horizon)
        for row in schedule(state, cursor.isoformat(), stop.isoformat()):
            if row["slot_id"]:
                found[row["slot_id"]] = row
        cursor = stop + timedelta(days=1)
    return [s["id"] for s in active
            if any(slot not in found or found[slot]["conflict"]
                   for slot in (s["first_slot"], s["second_slot"]))]

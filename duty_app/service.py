from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import base64
import json
import re

from .engine import (RuleError, add_swap, current_config, day, make_config,
                     mark_absence, mark_missed, schedule, swap_conflicts)


class SwapConflict(RuleError):
    def __init__(self, conflicts):
        super().__init__("此次调整影响已有换班，确认后将撤销受影响的未来换班")
        self.conflicts = conflicts


class DutyService:
    def __init__(self, repository, today=date.today, startup=None):
        self.repository = repository
        self.today = today
        self.startup = startup

    def view(self):
        revision, state = self.repository.read()
        today = self.today()
        ds = today.isoformat()
        cfg = current_config(state, ds)
        future = sorted((c for c in state["configs"] if c["effective"] > ds), key=lambda c: c["effective"])
        display_cfg = cfg or (future[0] if future else None)
        pending_returns = {e["member_id"]: e["date"] for e in state["events"]
                           if e["kind"] == "return" and e["date"] > ds}
        return {"revision": revision, "state": state, "today": ds,
                "yesterday": (today-timedelta(days=1)).isoformat(), "config": display_cfg,
                "next_config": future[0] if future else None,
                "today_duty": schedule(state, ds, ds)[0],
                "yesterday_duty": schedule(state, (today-timedelta(days=1)).isoformat(),
                                           (today-timedelta(days=1)).isoformat())[0],
                "pending_returns": pending_returns,
                "autostart": self.startup.enabled() if self.startup else False}

    def edit(self, operation: str, body: dict):
        today = self.today()
        ds = today.isoformat()
        tomorrow = (today+timedelta(days=1)).isoformat()

        def transform(state):
            if operation == "config":
                effective = tomorrow if state["configs"] else body.get("effective", ds)
                if not state["configs"] and abs((day(effective)-today).days) > 3650:
                    raise RuleError("首次起始日期请设置在十年内")
                cfg = make_config(body.get("members"), effective, body.get("first_member"),
                                  body.get("mop_interval", 3), body.get("first_mop", effective))
                updated = deepcopy(state)
                updated["configs"] = [c for c in updated["configs"] if c["effective"] < effective] + [cfg]
            elif operation == "missed":
                updated = mark_missed(state, body.get("date", ""), ds, bool(body.get("undo")))
            elif operation == "absence":
                if not isinstance(body.get("away"), bool):
                    raise RuleError("回家状态格式有误")
                updated = mark_absence(state, body.get("member_id"), ds, body["away"])
            elif operation == "swaps":
                updated = add_swap(state, body.get("first", ""), body.get("second", ""), ds)
            elif operation == "swaps/cancel":
                updated = deepcopy(state)
                matches = [s for s in updated["swaps"] if s["id"] == body.get("id")]
                if not matches:
                    raise RuleError("换班记录不存在")
                matches[0].update(cancelled=True, cancelled_from=ds)
            elif operation == "settings":
                updated = deepcopy(state)
                value = body.get("reminder_time", state["settings"]["reminder_time"])
                if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise RuleError("提醒时间格式应为 HH:MM")
                if "paused" in body and not isinstance(body["paused"], bool):
                    raise RuleError("暂停设置格式有误")
                updated["settings"].update(reminder_time=value,
                                            paused=body.get("paused", state["settings"]["paused"]))
                if "autostart" in body:
                    if not isinstance(body["autostart"], bool) or not self.startup:
                        raise RuleError("开机启动设置仅适用于 Windows 托盘程序")
                    self.startup.set_enabled(body["autostart"])
            elif operation == "import":
                payload = self.parse_legacy(body.get("key", ""))
                members = payload["members"]
                cfg = make_config(members, tomorrow if state["configs"] else payload["mopAnchorDate"],
                                  mop_interval=payload["mopInterval"], first_mop=payload["mopAnchorDate"])
                cfg["first_member"] = cfg["members"][payload["mopAnchorIdx"]]["id"]
                if state["configs"]:
                    # Original anchor identifies the owner on that old date.
                    offset = (day(tomorrow)-day(payload["mopAnchorDate"])).days
                    cfg["first_member"] = cfg["members"][(payload["mopAnchorIdx"]+offset) % len(members)]["id"]
                updated = deepcopy(state)
                updated["configs"] = [c for c in updated["configs"] if c["effective"] < cfg["effective"]] + [cfg]
            else:
                raise RuleError("操作不存在")
            # Validate before commit, including legacy anchors that predate the
            # supported calculation window. A rejected import never poisons DB.
            schedule(updated, ds, tomorrow)
            conflicts = swap_conflicts(updated, ds)
            if conflicts:
                if not body.get("cancel_conflicts"):
                    raise SwapConflict(conflicts)
                for swap in updated["swaps"]:
                    if swap["id"] in conflicts:
                        swap.update(cancelled=True, cancelled_from=tomorrow if operation in ("config", "import") else ds)
            return updated

        self.repository.mutate(body.get("revision"), transform)
        return self.view()

    @staticmethod
    def parse_legacy(raw):
        try:
            if not isinstance(raw, str) or len(raw) > 100000:
                raise ValueError
            raw = raw.strip()
            if raw.startswith("DUTY:"):
                raw = base64.b64decode(raw[5:], validate=True).decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            members = payload["members"]
            interval = payload.get("mopInterval", 3)
            anchor = payload["mopAnchorDate"]
            index = payload.get("mopAnchorIdx", 0)
            if (not isinstance(members, list) or not members or not all(isinstance(n, str) for n in members)
                    or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(members)):
                raise ValueError
            make_config(members, anchor, mop_interval=interval)
            return {"members": members, "mopInterval": interval, "mopAnchorDate": anchor, "mopAnchorIdx": index}
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise RuleError("导入内容有误，请粘贴完整的 DUTY: 配置或原格式 JSON") from None

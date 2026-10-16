from copy import deepcopy
from datetime import date, timedelta
import csv
import io
import json
import re
import threading

from . import engine
from .engine import RuleError, day


class ScheduleConflict(RuleError):
    def __init__(self, conflicts):
        super().__init__("此次调整影响未来调班或未完成处罚，请确认是否同时撤销冲突项")
        self.conflicts = conflicts


class DutyService:
    def __init__(self, repository, today=date.today, startup=None):
        self.repository, self.today, self.startup = repository, today, startup
        self.on_change = lambda: None
        self._cache, self._lock = {}, threading.Lock()

    def days(self, start, end):
        revision, state = self.repository.read()
        return revision, self._schedule(revision, state, start, end)

    def _schedule(self, revision, state, start, end):
        key = (revision, start, end)
        with self._lock:
            cached = self._cache.get(key)
        if cached is None:
            cached = engine.schedule(state, start, end)
            with self._lock:
                if len(self._cache) > 16:
                    self._cache.clear()
                self._cache[key] = cached
        return deepcopy(cached)

    def view(self):
        revision, state = self.repository.read()
        today = self.today()
        ds, tomorrow = today.isoformat(), (today+timedelta(days=1)).isoformat()
        cfg = engine.current_config(state, ds)
        future = next(iter(sorted([c for c in state["configs"] if c["effective"] > ds], key=lambda c: c["effective"])), None)
        display_cfg = future or cfg
        attendance = {}
        for event in sorted(state["attendance"], key=lambda e: e["date"]):
            if event["date"] <= ds:
                attendance[event["student_id"]] = event["away"]
        days = engine.schedule(state, ds, tomorrow)
        return {"revision": revision, "state": state, "config": cfg, "next_config": future,
                "today": ds, "tomorrow": tomorrow, "today_duty": days[0], "tomorrow_duty": days[1],
                "away_ids": [sid for sid, away in attendance.items() if away],
                "pending_returns": [e["student_id"] for e in state["attendance"] if not e["away"] and e["date"] > ds],
                "overlaps": engine.overlaps(display_cfg, state["overlap_confirmations"]) if display_cfg else [],
                "autostart": self.startup.enabled() if self.startup else False}

    def display(self):
        today = self.today()
        start, end = today.isoformat(), (today+timedelta(days=1)).isoformat()
        revision, state = self.repository.read()
        cfg = engine.current_config(state, start) or engine.current_config(state, end)
        days = self._schedule(revision, state, start, end)
        # Deliberately whitelist fields: assessment and penalty reasons stay private.
        for d in days:
            d["areas"] = [{k: r[k] for k in ("area_id", "area_name", "group_name", "students", "absent_ids", "student_ids")}
                          for r in d["areas"]]
        return {"revision": revision, "class_name": cfg["class_name"] if cfg else "校园值日管家",
                "today": days[0], "tomorrow": days[1]}

    def initial_state(self, body):
        today = self.today()
        effective = body.get("effective", today.isoformat())
        if abs((day(effective)-today).days) > 3650:
            raise RuleError("首次起始日期请设置在十年内")
        state = engine.empty_state()
        state["configs"] = [engine.make_config(body.get("config", {}), effective)]
        state["settings"] = self.settings(state["settings"], body.get("settings", {}))
        return state

    @staticmethod
    def settings(old, body):
        updated = dict(old)
        time = body.get("reminder_time", old["reminder_time"])
        if not isinstance(time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            raise RuleError("提醒时间格式应为 HH:MM")
        mode = body.get("reminder_mode", old["reminder_mode"])
        if mode not in ("popup", "display"):
            raise RuleError("提醒方式请选择静音弹窗或浏览器展示页")
        for key in ("paused", "widget_visible"):
            if key in body and not isinstance(body[key], bool):
                raise RuleError("开关设置格式有误")
            updated[key] = body.get(key, old[key])
        updated.update(reminder_time=time, reminder_mode=mode)
        return updated

    def _configuration(self, state, body):
        tomorrow = (self.today()+timedelta(days=1)).isoformat()
        source = body.get("config")
        if isinstance(source, str):
            if len(source) > 500000:
                raise RuleError("配置文件过大")
            try:
                source = json.loads(source)
            except (ValueError, UnicodeError):
                raise RuleError("配置 JSON 格式有误") from None
        if isinstance(source, dict) and "config" in source:
            source = source["config"]
        cfg = engine.make_config(source, tomorrow)
        updated = deepcopy(state)
        updated["configs"] = [c for c in updated["configs"] if c["effective"] < tomorrow] + [cfg]
        keys = {warning["key"] for warning in engine.overlaps(cfg)}
        updated["overlap_confirmations"] = [key for key in updated["overlap_confirmations"] if key in keys]
        return updated, cfg

    def preview(self, body):
        revision, state = self.repository.read()
        if type(body.get("revision")) is not int or body["revision"] != revision:
            from .storage import RevisionConflict
            raise RevisionConflict("页面版本已更新，请刷新后预览")
        updated, cfg = self._configuration(state, body)
        return {"revision": revision, "config": cfg,
                "overlaps": engine.overlaps(cfg, updated["overlap_confirmations"]),
                "conflicts": engine.conflicts(updated, self.today().isoformat()),
                "tomorrow": engine.schedule(updated, cfg["effective"], cfg["effective"])[0]}

    def edit(self, operation, body):
        ds = self.today().isoformat()
        tomorrow = (self.today()+timedelta(days=1)).isoformat()

        def transform(state):
            if operation in ("config", "import"):
                updated, _ = self._configuration(state, body)
            elif operation == "marks":
                updated = engine.mark(state, body, ds)
            elif operation == "marks/cancel":
                updated = engine.cancel_mark(state, body.get("id"), ds)
            elif operation == "absence":
                updated = engine.absence(state, body.get("student_id"), body.get("away"), ds)
            elif operation == "swaps":
                updated = engine.add_swap(state, body.get("area_id"), body.get("first", ""), body.get("second", ""), ds)
            elif operation == "swaps/cancel":
                updated = deepcopy(state)
                swap = next((s for s in updated["swaps"] if s["id"] == body.get("id")), None)
                if not swap:
                    raise RuleError("调班记录不存在")
                swap.setdefault("cancelled_from", tomorrow)
            elif operation == "overlaps/confirm":
                updated = deepcopy(state)
                cfg = engine.current_config(state, tomorrow) or engine.current_config(state, ds)
                valid = {w["key"] for w in engine.overlaps(cfg)} if cfg else set()
                keys = body.get("keys", [])
                if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or k not in valid for k in keys):
                    raise RuleError("人员交叉记录已变化，请刷新后核对")
                updated["overlap_confirmations"] = list(dict.fromkeys(updated["overlap_confirmations"]+keys))
            elif operation == "settings":
                updated = deepcopy(state)
                updated["settings"] = self.settings(state["settings"], body)
                if "autostart" in body and (not isinstance(body["autostart"], bool) or not self.startup):
                    raise RuleError("开机启动设置仅适用于 Windows 托盘程序")
            else:
                raise RuleError("操作不存在")
            if operation != "settings":
                found = engine.conflicts(updated, ds)
                if found:
                    if body.get("cancel_conflicts") is not True:
                        raise ScheduleConflict(found)
                    for item in found:
                        records = updated["marks"] if item["kind"] == "mark" else updated["swaps"]
                        record = next(r for r in records if r["id"] == item["id"])
                        record["cancelled_from"] = tomorrow
                        if item["kind"] == "mark":
                            # Configuration resolution cancels remaining duty, not the assessment.
                            record["duty_cancelled"] = True
            engine.schedule(updated, ds, tomorrow)
            if operation == "settings" and "autostart" in body:
                self.startup.set_enabled(body["autostart"])
            return updated

        self.repository.mutate_request(operation, body, transform)
        self.on_change()
        return self.view()

    def report(self, period="week", anchor=None, area_id=None, target_kind=None, reason=None, target_id=None):
        return engine.assessments(self.repository.read()[1], period, anchor or self.today().isoformat(), area_id, target_kind, reason, target_id)

    def report_csv(self, **kwargs):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["日期", "区域", "对象类型", "对象", "原因", "处罚", "天数", "备注"])
        for record in self.report(**kwargs)["details"]:
            values = [record["date"], record["area_name"], "小组" if record["target_kind"] == "group" else "个人",
                      record["target_name"], "忘做" if record["reason"] == "missed" else "值日较差",
                      "连续值日" if record["penalty"] == "duty" else "值日以外", record["days"], record["note"]]
            writer.writerow(["'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
        return ("\ufeff"+output.getvalue()).encode("utf-8")

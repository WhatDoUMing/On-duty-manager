# 本地 API

所有地址均相对于托盘程序提供的 HTTP 地址。除状态与登录接口，API 需要 `duty_session` Cookie。静态页面只包含登录与界面外壳，不内嵌排班数据。

## 读取

| 方法与路径 | 返回 |
| --- | --- |
| `GET /api/status` | 是否设置口令、当前是否登录、版本 |
| `GET /api/state` | `revision`、配置与事件、今日/昨日任务、待生效规则、手机管理地址 |
| `GET /api/schedule?start=YYYY-MM-DD&end=YYYY-MM-DD` | `revision` 和 `days`，最多两年 |

任务字段为 `date`、`slot_id`、`member_id`、`name`、`mop`、`missed`、`carried`、`swap_id`、`away_ids`。日期按 Windows 本地日期解释；没有任务时 `slot_id` 和 `member_id` 为 `null`。

## 身份

- `POST /api/setup-auth`：`password`、`token`。仅本机、仅首次；token 来自托盘启动的首次入口。
- `POST /api/login`：`password`。设置 12 小时会话 Cookie。
- `POST /api/logout`：空 JSON 对象，注销当前会话。
- `POST /api/local-login`：`token`。仅本机，托盘生成的单次入口，有效期 60 秒。

## 排班与设置

请求使用 `Content-Type: application/json`，均携带从读取接口获取的整数 `revision`。成功返回更新后的状态；过期版本返回 `409` 和 `stale: true`。与未来换班发生冲突时，返回 `409`、`conflicts`；用户确认后携带 `cancel_conflicts: true` 重试。

| 路径（均为 POST） | 主要字段 |
| --- | --- |
| `/api/config` | `members: [{id,name}]`、`first_member`、`mop_interval`、`first_mop`；首次可设 `effective`，之后强制次日生效 |
| `/api/missed` | `date` 必须为昨日；`undo: true` 撤销误标 |
| `/api/absence` | `member_id`、`away: true/false`；回家当天生效，返校次日生效 |
| `/api/swaps` | `first`、`second` 两个当日或未来日期 |
| `/api/swaps/cancel` | 换班记录 `id` |
| `/api/settings` | `reminder_time: HH:MM`、`paused`、`autostart`（可选，Windows） |
| `/api/import` | `key` 为原 HTML 的 `DUTY:` 或 JSON 配置 |

错误使用 `{"error":"中文原因"}`；输入校验为 400，未登录为 401，来源校验为 403。请求体上限 1 MiB。不提供跨域访问。

SQLite 事务包含版本读取、规则计算与写入。提醒确认状态按日期独立保存，不改变排班版本。认证哈希与本机实例密钥不出现在状态 API 中。

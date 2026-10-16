# 校园版接口

默认端口 8766。日期按 Windows 本地日期解释。管理会话 Cookie 为
`campus_session`，展示会话为 `campus_display_session`，均为 HttpOnly、
SameSite=Strict。静态页面仅包含外壳，排班、考核等数据接口要求认证。

## 初始化与身份

本机托盘入口携带一次性启动令牌；首次页面第一步选模式，最后设口令。
`POST /api/setup` 接收 `token`、`password`、`effective`、`config`、`settings`，
配置和口令在一个事务中保存。之后使用 `/api/login`、`/api/logout`。
`/api/local-login` 兑换本机管理票据，`/api/display-login` 兑换独立展示票据。
票据仅限本机兑换、60 秒有效、一次使用，通过 URL fragment 传递并清除。

## 读取

- `GET /api/status`：初始化状态、身份状态、本机状态与版本，不含班级数据。
- `GET /api/state`：revision、版本化配置、事件、今日/明日任务及交叉提醒。
- `GET /api/schedule?start=YYYY-MM-DD&end=YYYY-MM-DD`：最多两年的日期数组。
- `GET /api/assessments`、`/api/assessments.csv`：`period=week|month`、`anchor`，
  可用 `area_id`、`target_kind=group|student`、`target_id`、`reason=missed|poor` 筛选。
- `GET /api/export`：校园配置，包含稳定人员及区域标识，导入后次日生效。
- `GET /api/display`：仅今日/明日区域、小组、名单和缺席状态。展示身份无权
  读取完整 state、考核或调用管理写入接口。

每天为 `{date, areas: [...]}`；区域任务含 `area_id`、`task_id`、`group_id`、
`student_ids`、`absent_ids`、`normal_student_ids`、`swap_id`、`penalty_ids`。

## 管理写入

所有写入携带整数 `revision`，建议携带唯一 `request_id`。重复请求先去重，
即使旧 revision 也不重复执行；同一标识配不同内容会被拒绝。
旧版本返回 409/stale，不写入。冲突返回 409/conflicts，核对后添加
`cancel_conflicts: true` 重试。输入错误 400，未认证 401，来源错误 403。

| POST 路径 | 内容 |
| --- | --- |
| `/api/config/preview` | `config`，只预览次日配置、交叉、调班及处罚冲突 |
| `/api/config`、`/api/import` | `config` 对象或配置 JSON；次日生效 |
| `/api/marks` | `area_id,target_kind,target_id,date,reason,penalty=duty|other,days,note` |
| `/api/marks/cancel` | `id`；撤销考核，剩余处罚次日起取消 |
| `/api/absence` | `student_id,away`；请假当天、返校次日生效 |
| `/api/swaps` | `area_id,first,second` 两个未来日期 |
| `/api/swaps/cancel` | `id`；次日起撤销，历史保留 |
| `/api/overlaps/confirm` | `keys` 交叉关系标识数组 |
| `/api/settings` | `reminder_time,reminder_mode=popup|display,paused,widget_visible,autostart` |

人员含 `id,name,number`；区域含 `id,name,cycle=daily|weekly,week_policy=shift|calendar`。
小组模式的区域有 `groups:[{id,name,student_ids}]`，个人模式有 `student_ids,count`。
配置顶层含 `class_name,mode=group|individual,students,areas`。

## 提醒

提醒以发送日期去重，内容目标为次日。弹窗和浏览器模式都无声音。
`POST /api/reminder-action` 限管理/展示身份，接收 `day,token,action=shown|snooze|dismiss`。
day 必须为当天，token 需与当前提醒相符。跨午夜旧动作失效。
本机实例控制接口额外要求 loopback 地址与实例密钥，不向网页暴露实例密钥。

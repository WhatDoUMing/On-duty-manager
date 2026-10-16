# 值日管家

Windows 托盘提醒 + 浏览器管理，同 Wi-Fi 手机和电脑共用一份值日安排。默认每天 **22:00** 发送右下角 Windows 系统通知；关闭管理网页后，后台继续提醒。

已生成 Windows 64 位安装包和便携版，位于打包机器的 `output/` 目录。构建产物不提交到 Git。

## 安装版与便携版

- 安装版：`output/DutyManager-Setup-0.1.0.exe`，安装时可选择开机自启动，默认不勾选。
- 便携版：完整解压 `output/DutyManager-Portable-0.1.0.zip`，运行文件夹中的 `DutyManager.exe`，保留旁边的 `_internal` 目录。
- 两种方式均无需另装 Python。使用说明和 SHA-256 校验文件也在 `output/`。

## 从源码运行

需要 Windows 10/11、Python 3.11+。在项目目录运行：

```powershell
python -m pip install -r requirements.txt
pythonw run_duty.pyw
```

首次会打开本机浏览器，先设置至少 8 位的管理口令，再设置名单、开始日期、起始成员和拖地周期。之后可从任务栏右下角托盘打开管理端、暂停提醒或退出程序。重复启动会打开已有实例。

需要在终端查看错误时使用 `python -m duty_app`。更多参数：

```powershell
python -m duty_app --help
pythonw run_duty.pyw --background
python -m duty_app --data-dir .runtime/dev --port 8765
```

- 默认端口为 `8765`；占用时自动选择可用端口，以托盘打开的地址为准。
- 数据保存在 `%LOCALAPPDATA%\DutyManager\duty.sqlite3`，升级保留数据。关闭程序后可备份整个数据目录。
- `--background` 静默进入托盘；`--data-dir` 用于隔离开发数据；`--headless` 仅运行服务供诊断。
- 网页需要托盘服务运行，通过 HTTP 地址访问；双击 HTML 文件仅作为静态源码查看。
- “设置 → 每日提醒”可修改时间、暂停提醒和调整当前用户的开机启动。启动后静默进入托盘，不要求管理员权限。

## 手机访问

电脑与手机连接同一 Wi-Fi，在托盘或网页底部查看“手机管理地址”，用手机浏览器打开，再输入管理口令。所有终端操作同一份数据库，不再使用各自独立的浏览器本地排班。

若手机访问未打开，检查 Windows 防火墙是否允许 Python（之后是值日管家 EXE）在**专用网络**通信。应用不会自行修改防火墙。电脑需要保持开机、联网，提醒仅在这台电脑上弹出。

## 值日与调整规则

- 每天轮流倒垃圾，默认每三个有效值日天拖地，拖地由当天值日生负责。周末也排班。
- 未标记忘做时按正常完成轮转；仅手动标记“昨日忘做”才暂停一天。当天任务保留到次日，后续轮值、拖地日期与换班一起后移。
- 例：周一 1 号忘做，周二仍为 1 号；原周三的拖地任务顺延到周四。若忘做当天含拖地，次日继续包含拖地。
- 同一天重复标记只生效一次；可撤销昨日误标。连续多天忘做，需要每天标记前一天。历史早于昨日的记录保留核对。
- 回家从当天生效，期间跳过该成员；返校从次日起优先值日，多人按操作顺序排队，补做任务优先。全员回家时，轮值和拖地周期暂停。
- 换班交换两个未执行任务的负责人，任务的拖地标记保持不变；补做中的任务保留负责人。每个任务同时最多参与一次换班。撤销只影响当日和未来，已发生的历史保留。
- 名单、顺序、起始人和周期的修改从次日起生效；正在补做的任务先完成。若调整影响已有换班，界面先提示，确认后撤销受影响的未来换班。
- 设置页面支持导入原 HTML 生成的 `DUTY:` 配置或原格式 JSON；导入包含名单和拖地锚点，不包含旧页面缺少日期的回家状态。

## 提醒行为

每日提醒使用 Windows 10/11 原生系统通知，显示在右下角并留在通知中心，提供“打开管理”“十分钟后提醒”“关闭本次”。点击通知正文也能打开管理端。关闭只确认本次提醒，不改变完成状态。睡眠恢复或过了提醒时间才启动时，仅补提醒当天尚未发送的任务。旧日期不逐日弹出。暂停后恢复也保留当天已发送状态。

程序每 15 秒检查一次时间，提醒在设置时刻后的首个检查触发。通知发送完成后辅助进程立即退出，由 Windows 管理显示，不保留独立提醒窗口或网页渲染引擎。退出托盘会停止本地服务和后续提醒。已送达的通知保留至当日午夜，之后自动过期。

首次发送通知时仅为当前用户注册通知名称和按钮回调协议，不修改开机启动。通知横幅及声音遵循 Windows 的通知、勿扰和专注设置；若只在通知中心出现，请在系统通知设置中允许“值日管家”的横幅。

## 测试与验证

```powershell
python -m unittest discover -s tests -v
node --check assets/app.js
```

真实界面验证使用本机已安装的 Chrome / Edge，以及开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
python tools/browser_smoke.py
python tools/desktop_smoke.py
python tools/notification_smoke.py
```

这些验证使用临时数据与独立浏览器配置，不修改正常用户的排班或开机启动。桌面与通知验证会显示测试用的系统通知，并在结束后清除测试通知和对应的临时注册项。截图与资源数据位于 `.runtime/qa`，不会提交。

已通过 **52 个自动测试**，验证桌面 1440×1100、手机 390×844 的完整操作流程，以及真实 Windows 托盘、单实例、通知中心记录和原生按钮协议。系统通知版本的一次 20 秒源码后台采样：私有内存 **25.48 MB**、工作集 **41.22 MB**、平均 CPU **0.2%**，无常驻子进程。数值随系统和 Python 环境变化，打包后需复测。手机验证为浏览器尺寸模拟，真实手机 Wi-Fi 连接需在实际网络验收。

## 重新打包

已准备 `packaging/DutyManager.spec` 和 `packaging/installer.iss`。采用 PyInstaller **目录模式**，减少单文件解压和额外常驻进程；安装程序将目录整体安装，不要求目标电脑安装 Python。

安装采用当前用户目录，开机启动默认不勾选；卸载清理应用自启动入口，保留用户排班数据。构建需要 PyInstaller 和 Inno Setup 6.3+。用户明确要求“打包”后运行：

```powershell
.\tools\build_release.ps1 -InnoCompiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

本机已在 `.runtime/inno` 部署便携编译器，可省略参数。成品验证：

```powershell
python tools/package_smoke.py
python tools/installer_smoke.py
```

打包后的程序已在不含 Python 的运行 PATH 下验证启动、认证、网页资源、Windows 系统通知及稍后提醒，并从便携压缩包解压到含中文和空格的路径中验证；20 秒采样私有内存 **27.32 MB**、工作集 **43.71 MB**、平均 CPU **0%**，无常驻子进程。安装测试使用独立应用身份、独立自启动名称和项目内目录，验证默认自启动关闭、取消勾选清除旧入口、勾选后启用及卸载清理，保留正常用户设置。

## 代码结构

- `duty_app/engine.py`：无 IO 的排班引擎，逻辑任务使用稳定的 `配置 ID:序号` 标识。
- `duty_app/storage.py`、`service.py`：SQLite、原子事务、版本冲突和操作规则。
- `duty_app/server.py`、`auth.py`：认证 API、同源检查、局域网共享。
- `duty_app/desktop.py`、`notifications.py`、`reminders.py`、`startup.py`：托盘、Windows 原生通知、提醒去重、自启动。
- `duty_manager.html`、`assets/`：无前端依赖的管理界面，按指定 Animation 指南实现原生弹层与简短反馈。

认证会话使用 HttpOnly Cookie，12 小时过期；口令使用 PBKDF2 加盐哈希。排班和设置提交携带版本号，过期提交返回 409，避免多端覆盖。API 概要见 [docs/API.md](docs/API.md)。

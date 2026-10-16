# 值日管家

Windows 托盘值日提醒工具，浏览器与同 Wi-Fi 手机共用排班。默认每日 22:00 提醒。

开发阶段：值日引擎、本地服务、托盘与提醒已实现，接下来接入管理界面。

本轮仅交付源码与测试，收到明确的“打包”指令后才生成 EXE 和安装程序。

## 排班规则

- 值日每日轮流，拖地默认每三个有效值日天一次。
- 昨日忘做后该任务次日继续，后续轮值、拖地与换班一起后移。
- 回家期间跳过；返校从次日起优先，多人按操作顺序排队，补做优先。
- 全员回家时暂停。换班交换任务负责人，拖地标记属于任务。
- 新配置从次日起生效，历史保留，未完成任务先补做。

## 测试

Python 3.11+：`python -m unittest discover -s tests -v`

## 从源码运行

```powershell
python -m pip install -r requirements.txt
python -m duty_app
```

首次从托盘打开后设置管理口令。默认端口 8765；`--background` 静默进入托盘。
数据默认位于 `%LOCALAPPDATA%\DutyManager`，开发隔离可使用 `--data-dir .runtime`。
`--headless` 仅运行本地服务，用于诊断。当前管理 HTML 将在第三阶段接入 API。

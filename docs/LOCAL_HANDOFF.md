# 小米音箱 × DeepSeek Harness：本机交接

更新：2026-11-15，中国标准时间。此文件是状态快照，运行情况以健康接口和服务日志为准。

## 路径与当前状态

| 项目 | 实际位置/状态 |
| --- | --- |
| 唯一有效源码 | `/Users/zhangyichi/codex/open-xiaoai-lx06` |
| 运行模块 | `examples/harness/` |
| 用户 Harness 工作区 | `/Volumes/PHILIPS/小米音箱`，名称“小米音箱” |
| 外置盘旧副本 | `/Volumes/PHILIPS/小米音箱/open-xiaoai-lx06`，仅早期刷机/上游副本 |
| Git 分支 | `feat/harness-voice-bridge` |
| 上游 | `idootop/open-xiaoai`，已归档；尚无用户指定的可写远端 |
| 设备 | 实际核验一台 LX06，固件 1.94.13，system0 + boot0，bootdelay 15 |
| 网络快照 | 手机热点；音箱 `192.168.228.16`，Mac `192.168.228.248`，MAC `50:88:11:2d:38:c2` |

IP 可随 DHCP 改变，换网络时更新 server.txt 和 LaunchAgent 参数，不需要重刷固件。

## 已实现并验证

1. macOS Amlogic 刷入官方项目对应版本补丁，SHA-256 与发布记录一致，工具返回 `mwrite success`。
2. SSH 登录及远程 TTS 实测；用户听到测试声音。
3. 官方 ARMv7 客户端安装 `/data/open-xiaoai/client`，版本 RPC 返回 1.0.0；音箱启动自动连接 Mac。
4. Mac 接收音箱 16 kHz 单声道 PCM。轻量 Sherpa KWS 在 Mac 上识别“大爱同学”，不使用 Mac 麦克风。
5. 唤醒后提示“我在”，通过 ubus 打开原生小爱 ASR，下一条最终识别结果进入 Agent。
6. 官方 `dsh headless --json` 已实测复用本机模型配置，工具事件证明 bash/pwd 成功。
7. 回复只取成功 final 文本；不朗读 reasoning/tool 内容。原生 TTS 完成事件可在 stderr，已兼容。
8. 用户现场确认“大爱同学”唤醒、提问、思考后回复成功；后续追问沿用 headless 持久会话。
9. 已实现桌面插件 `examples/harness/desktop-plugin`，使用公开 sessionController 和 workspaceRegistry。
   插件安装后创建“大爱同学 · 语音对话”，桌面可实时展示问题、推理/工具和回复。
   桌面插件已由用户添加并启用，真实桌面调用及音箱播报成功，用户确认列表中显示消息和回复。

## 当前对话为什么不在桌面

最早的 headless 会话工作目录是桥接 `.runtime/workspace`，Session ID 保存于
`examples/harness/.runtime/session.json`。CLI 是独立 Host，桌面运行实例未实时纳入这些事件。
历史没有丢失，也没有改写；不手工移动/拼接 JSONL，不把 CLI 存储文件当作 UI 会话。

另有一次目录归属测试 CLI 会话在外置盘根创建，仍不构成桌面实时会话验证。
正式桌面插件为新语音会话，旧 headless 历史保留，不自动混入。

## 桌面接入

Harness 添加本地文件夹：
`/Users/zhangyichi/codex/open-xiaoai-lx06/examples/harness/desktop-plugin`

插件不修改研究会话，不改变权限预设，不导出凭据。它在已有桌面 Host 内通过
`workspaceRegistry.create` 复用“小米音箱”，通过 `sessionController.create/rename/prompt`
管理一个专用会话。仅监听随机 loopback 端口，并生成权限 0600 的本地临时令牌描述文件。
不向局域网公开 Harness API，不发送模型 Token 给音箱。

插件就绪时 `.runtime/desktop.json` 存在。桥接在下一轮优先选 desktop；未安装时保持
headless。桌面调用失败不会把同一问题自动重发到 headless，避免重复执行工具。
健康字段 `backend=desktop` 加上真实问答成功才能算切换完成；仅文件存在不足以证明。

## 启停与验证

```sh
cd /Users/zhangyichi/codex/open-xiaoai-lx06/examples/harness
.venv/bin/python bridge.py --status
.venv/bin/python service.py stop
.venv/bin/python service.py start
# 修改代码后重载本项目服务
.venv/bin/python service.py install --speaker-ip 192.168.228.16
# 等价的文字测试，也会真实调用 Agent 和音箱播报
.venv/bin/python bridge.py --ask '请用一句话说明你是谁'
.venv/bin/python -m unittest discover -s tests -v
node --test desktop-plugin/test.mjs
```

Mac 启动配置：`~/Library/LaunchAgents/com.zhangyichi.xiaoai-harness.plist`。
音箱：`/data/init.sh` 调用 `/data/open-xiaoai/boot.sh`；`server.txt` 指向 Mac 4399。
本机 4400 是桥接诊断接口，仅 localhost；4399 只接收配置的音箱地址。
日志：`examples/harness/.runtime/service.log`；不要把它或原始对话提交到 Git。

## 局限与后续

- 当前逐轮唤醒，不支持唤醒后免唤醒连续聊天；平均一轮可能需几十秒。
- “大爱同学”与原生“小爱同学”发音接近，可能两边同时触发；已处理提示音与 ASR 的竞争。
- 问题识别/播报依赖小米原生在线服务；Harness 的网络与模型取决于本机现有配置。
- headless 与桌面模型路由不必完全相同，桌面插件使用桌面端当时默认模型和权限。
- 桥接不会自动批准工具；Agent 需要用户审批时，桌面窗口应由用户处理。
- 不要重启整个 Harness：另有论文工作正在运行。只重启本桥接或本插件。
- Mac 需在线并保持服务；断开外置盘会影响工作区和桌面会话调用，运行源码仍在内置盘。

## 验证范围与源码同步

Python 回归测试覆盖 ASR 路由、重复事件、唤醒竞争、TTS 完成判断、shell 参数转义、
Harness 失败/超时；插件测试覆盖认证、final 过滤及 mock Host 会话调用。
插件 mock 测试不是实际桌面加载验证；此外已完成独立实机验证：桌面会话
`session-82efb81a-7bb9-46ac-ba1b-f1d5d9bff7f5` 在目标工作区注册，真实调用
生成回复并由音箱播报，健康接口 `backend=desktop`，用户确认消息和回复可见。
真实语音 headless 闭环此前也已由用户确认。

本地交付必须白名单 Git 提交；上游归档且 CLI 未登录，推送失败时保留本地提交，明确报告
未同步。不要用论文项目远端或 Obsidian 远端存放本项目代码。


## 17:24 交付状态

桌面插件已启用并实测，未来语音消息沿用“大爱同学 · 语音对话”。不需要重刷、
不需要重新配置模型 Token。会话指针持久保存，重启后复用；禁用插件时 CLI 兼容入口
可能产生不在桌面展示的独立会话，先查 `backend`，不要把两种历史混为一谈。

后续可直接在“小米音箱”工作区的新开发对话要求读取本文件，并到有效源码目录开发；
语音专用会话用于日常对话，与开发任务分开可避免长开发输出被朗读。


## 2026-11-15 收音失效修复

症状：唤醒与“我在”正常，但之后无 final ASR，未向 Harness 提交任务。
实机核查排除了客户端日志文件 inode 失配：客户端 FD 与当前 instruction.log inode
相同、读取偏移也已跟进。pnshelper event_notify 返回 code 0，但 event.log 中
SpeechRecognizer/Recognize 请求计数没有增加。仅重启 AIVS 中断回答后，收音前端
仍处于旧状态；手工重启 /etc/init.d/pns 后，下一次开麦请求计数立即增加。

修复：每次中断原生回答，配套重启 AIVS 和 PNS，检查退出状态并等待前端恢复。
新增 18 秒识别超时提示，收到有效 final 结果、断线或重新唤醒时取消定时器。
超时不提交空任务，不再静默等待。无需刷机、不重启 Harness，不改变桌面语音会话。
新增回归测试覆盖配套恢复、缺失识别时语音提示以及成功识别取消超时。

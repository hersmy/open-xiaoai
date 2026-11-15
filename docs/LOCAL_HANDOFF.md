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
| 上游 | `idootop/open-xiaoai`，已归档；用户提供的分叉为 `hersmy/open-xiaoai`，推送状态见交付记录 |
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

本地交付必须白名单 Git 提交；优先向用户分叉推送功能分支，推送失败时保留本地提交，明确报告
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


## 2026-11-15 长工具任务被三分钟定时器取消

用户先确认两轮语音均正常；后续多次报“助手遇到问题”。日志显示 final ASR
和桌面提交正常，18:47、18:54、19:05、19:09 等轮在工具执行中达到约 180 秒。
桌面会话事件显示 bash/read_image 等工具调用和 aborted/user，来源是插件自己的
setTimeout(cancel, 180000)，不是用户主动取消。不能归因为语音识别或网络断线。

修复：桌面插件 0.2.0 上限为 30 分钟；显式 VOICE_TIMEOUT 与 VOICE_CANCELLED，
HTTP 连接和本地 --ask 的等待时间配套延长。桥接只启动一次 Agent 调用，等待
45 秒后语音提示，再每两分钟提示；失败不自动重试，避免重复执行副作用工具。
新增慢任务只提交一次、取消传播、错误分流测试。插件仅需关闭再启用加载更新，
不可重启整个 Harness 影响论文任务。实际加载状态以插件 health 的 version 和
turn_timeout_ms 为准；未加载前不能宣称三分钟限制已在桌面消除。

部署验证：用户单独重新启用插件后，health 返回 version=0.2.0、
turn_timeout_ms=1800000，专用桌面 Session ID 保持不变。真实 Agent 简短测试
返回“语音助手更新成功。”，音箱 TTS 完成。超过三分钟的实际工具任务尚待后续使用观察。

## 2026-11-15 Mac 网易云本地点歌

用户接受 Mac 直接播放，后续可自行用蓝牙将系统输出切到小爱音箱。
`/Volumes/PHILIPS/小米音箱/music` 已复制 11 音频、11 歌词、10 封面，SHA-256 校验一致；
源目录 `/Users/zhangyichi/Music/网易云音乐` 保留。9 NCM 与 2 MP3 均不转换，不进入 Git。

新增 `examples/harness/netease_music.py`，按文件名匹配歌手/歌名，使用无 shell 的
`/usr/bin/open -a /Applications/NeteaseMusic.app <文件路径>` 交给网易云。
插件 0.3.0 在提交语音给 Agent 前执行匹配，把结果附入同一桌面会话；Agent 只复述结果，
不再次调用播放器。普通非音乐请求继续原流程。仅支持单首打开、歌手随机、曲库随机和查询；
暂停、自动下一首和播放器状态反馈尚未实现。

验证：19 个 Python 测试、5 个 Node 测试通过，覆盖路径限制、NCM 原格式交给应用、
错误如实反馈及插件结果注入。实际打开项目目录中《如约而至》后，用户确认电脑能听到音乐。
用户单独重新启用插件；health 实查 version=0.3.0、workspace 为目标外置盘目录，
session ID 沿用原语音会话。通过桥接 `--ask '本地曲库有多少首歌'` 的真实桌面 Agent
调用返回“本地曲库共有11首歌。”并完成音箱 TTS。完整新增语音点歌的现场结果仍需
与文件打开和文字入口验证分别记录。

源码同步：新增远端 `fork=https://github.com/hersmy/open-xiaoai.git`，保留归档上游 origin。
尝试向 `fork` 推送 `feat/harness-voice-bridge`，服务端返回
`Permission to hersmy/open-xiaoai.git denied to hersmy`（HTTP 403）；远端查询没有该分支。
代码已本地提交，尚未同步；后续取得可写凭据后再推送并核对 SHA。不要把音乐文件提交进去。

## 2026-11-15 华为耳机自动切换 Mac 输出

用户确认安装独立服务 `com.zhangyichi.headphone-output`，登录后自动运行。
源码/管理入口：`examples/macos-audio-switch/`；程序和状态在内置盘
`~/Library/Application Support/XiaoaiAudioSwitch/`，不依赖外置盘或 Harness 运行。
LaunchAgent：`~/Library/LaunchAgents/com.zhangyichi.headphone-output.plist`。

目标精确名称 `HUAWEI FreeBuds Pro 5`。CoreAudio 监听连接并切换默认媒体输出，
断开时恢复原来仍可用的设备。连接期间手动改选则让出控制，直到下次重连。
只改变媒体输出，不改输入、音量或播放状态。使用系统默认输出的应用随之切换；
应用自己固定设备时需另行在应用内选择系统默认。

验证：Swift 编译无警告，17 项状态检查通过；LaunchAgent plist 验证通过，进程常驻。
真实日志记录从 DELL S2725DSM 切到 HUAWEI FreeBuds Pro 5，`phase=active`；
CoreAudio 默认输出 UID 为 `C0-DA-5E-48-0E-8A:output`，用户确认音乐自动从耳机出声。
断开恢复与人工改选保留已通过状态测试，尚未分别要求用户进行实际硬件测试。
安装未重启 Harness 或音箱桥接。管理与卸载命令见该模块 README。

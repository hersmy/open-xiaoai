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
4. Mac 接收音箱 16 kHz 单声道 PCM。轻量 Sherpa KWS 在 Mac 上识别“你好小七”（已替代旧“大爱同学”），不使用 Mac 麦克风。
5. 确认原生 ASR 开麦后回应“哎”；有效命令先“收到”，明确控制走代码，复杂需求进入 Agent。代码成功说“已完成”，复杂任务播报 Agent 回复；全部用小爱原音色。
6. 官方 `dsh headless --json` 已实测复用本机模型配置，工具事件证明 bash/pwd 成功。
7. 回复只取成功 final 文本；不朗读 reasoning/tool 内容。原生 TTS 完成事件可在 stderr，已兼容。
8. 用户现场确认“大爱同学”唤醒、提问、思考后回复成功；后续追问沿用 headless 持久会话。
9. 已实现桌面插件 `examples/harness/desktop-plugin`，使用公开 sessionController 和 workspaceRegistry。
   插件安装后创建“大爱同学 · 语音对话”，桌面可实时展示问题、推理/工具和回复。
   桌面插件已由用户添加并启用，真实桌面调用及音箱播报成功，用户确认列表中显示消息和回复。

## 早期 headless 对话为什么不在桌面

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
- 原自定义“大爱同学”发音接近“小爱同学”，现已改用“你好小七”；原生“小爱同学”保留。
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

## 2026-11-15 明确点歌绕过 Agent

用户授权实测并优化文字到达 Mac 后的处理。`bridge.py` 先用纯函数保守分流，
本地点歌在线程执行，不调用 Harness，不播报“我想一想”或成功提示；曲库查询和失败
使用代码固定答复。推荐、条件、复合操作等继续走原桌面 Agent。常用控制扩展见下段。
插件保持 0.3.0，不需要用户重新启用；仅重载 `com.zhangyichi.xiaoai-harness`。

小爱原生回答的中断与 AIVS/PNS 恢复仍保留，与本地音乐打开并行。下一次唤醒会等待尚未
结束的恢复，再打开 ASR，避免以速度换回收音失效。子任务中断会清理本轮拥有的恢复任务；
已经交给外部应用的打开操作不会撤销，也不会因 Agent 失败而重复执行。

新增 health.last_turn 逐阶段计时，含 route、agent_called、spoken、查找、打开、恢复和总耗时。
快速操作记录在桥接自己 `.runtime/music-actions.jsonl`，不进入桌面 Agent 思考历史；
复杂问答会话仍复用。AutoRunner 启动时读取本桥接持久指针，避免仅执行本地操作后
健康接口错误显示旧 headless 会话。没有读写 Harness 内部存储。

实测同一指令“播放许嵩的如约而至”：优化前 Mac 文字入口整轮 20410.8 ms。
优化后两次：打开请求返回 2295.3/199.9 ms；整轮 3650.9/3491.9 ms；
曲库查找 65.2/18.8 ms；网易云 open 调用 2208.9/179.2 ms；原生恢复 3647.3/3490.8 ms。
两次 agent_called=false、spoken=false。另一个早期优化样本整轮 11305.0 ms，未细分其打开阶段，
保留此异常样本，不能声称每次延迟都低于一秒。以上均不含云端 ASR，不等同实际出声计时。
27 个 Python 测试及 5 个 Node 测试通过，包括本地无 Agent/成功无 TTS、失败只提示一次、
复杂回退、并行打开、立即再次唤醒等待恢复、取消传播和重启后会话指针。

用户确认两轮真实语音“播放许嵩的如约而至”“播放王菲的笑忘书”均成功且无额外播报。
对应 ASR final 后的打开请求返回 1920.7/1496.8 ms，整轮 3535.8/3535.9 ms，
均 agent_called=false、spoken=false。

用户接着反馈“暂停播放”仍调用 Agent，授权常用控制代码化。新增暂停/停止/关掉音乐、
继续/恢复播放、上一首/下一首等明确说法。暂停/继续由 Swift 辅助程序操作网易云动态
“控制”菜单，读状态、AXPress、再核对状态；重复命令不反转，不操作其他应用。
源码 `examples/harness/music_control.swift`，安装器 `install_music_control.py`，
App 位于 `~/Applications/小米音箱音乐控制.app`；用户已在辅助功能添加并开启权限。
暂停/继续四次独立实测状态为 paused/paused/playing/playing，约 421/143/267/183 ms。

下一首/上一首只在项目 music 曲库中按文件名排序前后移动，首尾环绕，使用网易云原样打开
NCM。当前曲目指针在自有 `.runtime/netease-state.json`，不是 Harness 存储；手动在网易云
换歌后指针可能不一致，先语音点播即可同步。尚未实现音量、循环设置或播完自动续播。
33 个 Python 测试及 5 个 Node 测试通过，包括控制成功静默、重复暂停/继续、控制失败不假报、
曲库切歌环绕和无已知曲目时不误执行。Swift 编译成功、程序已授权；后续不要无故重编译
改变签名造成再次授权。原先思考中的暂停任务已随本桥接停止而取消，没有重启 Harness。

后台启动控制 App 必须通过 `/usr/bin/open -n -W -g -a <App> --stdout <私有临时文件>
--stderr <私有临时文件> --args pause|resume`。已授权 App 从 LaunchAgent 直接 exec 时，
本机 TCC 将访问归于父服务并拒绝；正常 LaunchServices 启动后访问有效。不要改 TCC 数据库。
临时目录位于本机 .runtime，文件权限 0600，读取 JSON 结果后删除。此调用方式有回归测试。

真实后台桥接文字入口五项验证全部成功：暂停、继续、点歌、下一首、上一首；命令返回约
961/1592/2030/717/690 ms，整轮约 3.5～3.9 秒，全部 agent_called=false、spoken=false。
切歌从《笑忘书》切到项目文件《给我一首歌的时间》，上一首回到《笑忘书》。
随后真实 ASR 日志已记录“暂停播放”“继续播放”“请播放”，均本地接管且没有 Agent/TTS；
其中一次暂停约 722 ms 返回且菜单状态 paused，继续约 443 ms 返回且状态 playing。

## 2026-11-15 唤醒/识别时序和执行反馈修复（中间方案，已由下段原声协议替代）

用户补充现场问题：有迟到/重复“我在”、暂停需尝试多次、静默时不知是否执行。
历史服务日志确有 final ASR 未接管，原代码仅允许 listening 窗口或“问助手”前缀；
旧日志未记识别原文，不能断言每条被忽略的都是暂停。音箱 instruction.log 中可见
暂停最终识别和 Pause 之后出现“我在”Speak，event.log 有 GenerateSpeak“我在”；
逐次云 TTS 与原生唤醒竞争是时序风险，不能把 Python RPC 取消等同远端播报已停止。

用户明确要求保留“我在”，确认成功给不同词/短音，未听清给反馈。新增 `feedback.py`：
Mac 已有 Tingting 一次合成“我在”“没听清指令，请再叫我一次”，生成 150 ms 成功短音。
本地 .runtime/feedback 与音箱 /data/open-xiaoai/feedback 缓存，不提交音频。
开麦通过 event.log 新增 Recognize 确认后才播放缓存“我在”；未确认配套恢复重试一次，
仍失败只给未听清提示。成功动作立即短音确认，不等待约三秒 PNS 恢复，不调用 Agent。
无语音活动约八秒本地提醒；部分识别继续等待 final，保留十八秒总上限。

只识别到唤醒词/“我在”时不结束窗口，也不送 Agent，最多重新开麦一次而不重复“我在”。
原 PCM 队列清理、KWS 重置及 350 ms 提示尾音抑制，减少旧音频/提示自触发。
近期自定义上下文可接管窗口外的暂停/继续，恢复期间排队，不取消 PNS 恢复；没有近期
自定义唤醒的原生控制保持原样，Mac --ask 不会自行创建此上下文。重复同一控制词合并一次，
暂停/继续混合不折叠。last_recognition 仅记录决策与类别，不记录未知语音全文。

此固件无 base64 工具，已改分段 POSIX printf 二进制传输（单命令 <50 KB）并校验长度。
首次部署失败曾导致反馈未就绪，现已恢复：feedback_installed=true、PCM 持续接收。
42 个 Python 测试、5 个 Node 测试通过；真实 SSH 开麦检查返回 ASR_READY。
后台暂停/继续实测：控制约 424/712 ms 返回，成功短音约 250/240 ms 播完，
confirmed=true、agent_called=false，整轮约 3.51/3.52 秒（含并行恢复）。


## 2026-11-15 最终原声协议与现场验收（当前以本段为准）

用户最终将唤醒词指定为“你好小七”，就绪回应“哎”，有效命令先“收到”；
代码成功“已完成”，复杂需求只播报 Agent 回复。未听到、未听清、未能开麦分别提醒。
用户要求全部采用小爱原音色，已停止使用上述中间方案的 Mac Tingting 和成功短音。
`voice_config.py` 统一唤醒文字/拼音；原有 Sherpa 模型可直接支持，无需刷机或下载新模型。

`feedback.py` 在音箱调用 `mibrain.text_to_speech`（save=1、play=0）生成六条固定提示，
缓存到 `/data/open-xiaoai/feedback-native-v1/`，后续直接播放；不把音频放入 Git。
独立命名的 feedback-player 避免原生恢复时 killall miplayer 中断提示，不修改系统播放器。
动态答复仍走音箱原生 TTS；固定提示的首次生成和原生 ASR/动态 TTS 依赖小米在线服务。

保留先确认 ASR 再就绪、提示回声最多重开一次、八秒无输入提醒、十八秒总等待上限、
近期明确暂停/继续接管、恢复期间排队以及 PCM/KWS 清理。收到提示播完才执行命令；
AIVS/PNS 配套恢复保留，不能为了缩短整轮时间而删除。普通本地控制不调用 Agent。
`last_turn.spoken=true` 现在包含固定原声提示；`reply_tts=false` 表示无额外动态答复，
不能继续用旧样本的 spoken=false 作为当前快速控制标准。

验证：43 个 Python 测试及 5 个 Node 测试通过。音箱六条原声缓存及独立播放器实测成功；
健康接口显示唤醒词“你好小七”、反馈缓存就绪、PCM 持续接收。后台暂停/继续实测：
“收到”约 1.27/1.32 秒，控制约 0.73/0.98 秒，“已完成”约 1.44/1.45 秒，
整轮约 3.61/3.74 秒（含并行恢复），均 agent_called=false。复杂文字测试返回“测试成功”，
走现有桌面 Agent 并原生播报。这些计时从 Mac 文字入口开始，不含云端 ASR。

用户现场完成两轮：①“你好小七”→“哎”→“暂停播放”→“收到”→“已完成”；
②唤醒后安静约八秒，收到未听到/未听清提醒。用户明确确认“两轮都正常，是小爱原音色”。
真实语音控制日志也确认本地接管、无 Agent，整轮约 3.60 秒。

部署只重载本桥接 LaunchAgent，没有重启整个 Harness。插件仍为 0.3.0，沿用已验证的
“小米音箱”工作区与原“大爱同学 · 语音对话”会话，保留历史，不因改唤醒词另建会话。


## 2026-11-15 纯代码在线点歌和退下

用户授权：明确歌曲未命中本地后补齐在线搜索，禁止 computer use；“退下”直接由代码结束本轮。
新增 `netease_online.py`：标准库 HTTP 请求 music.163.com 的 cloudsearch 元数据，核对真实歌手/歌名，
以正整数歌曲 ID 构造 Mac 原生 orpheus base64 JSON，调用 `/usr/bin/open -g -a NeteaseMusic.app`。
没有浏览器/界面操作、第三方 API 服务、额外 Agent 思考、Cookie 读取或音频下载。
URL payload 格式已按 ncm-cli 源码核对并在网易云 3.1.6 实测；用户确认纯代码打开《有何不可》确实播放。

本地优先，明确“本地”请求失败不联网。在线结果不把标题里的“原唱”或 origin 引用误当当前歌手，
可再查原唱详情；同名不同歌手提醒补充，不替换翻唱/钢琴/Live。原唱不可播、接口失败、打开失败
由代码固定报错，不交给 Agent 重试。客户端自行处理账号/会员权限；匿名查询不等同客户端登录态。
在线播放更新本机自有曲目指针，避免本地上一首/下一首误用旧位置；在线后切歌需先点播本地歌曲。

新增 `voice_commands.py` 精确识别“退下/退下吧/你退下吧”等独立句子，保留“退下是什么意思”等问题。
在唤醒窗口或近期自定义上下文接受，取消收音计时器/回声重开麦、清除待执行指令及近期接管窗口，
原声回应一次“收到”，恢复原生前端后等待下一次“你好小七”。不调用 Agent，不暂停音乐，不删历史。
已交给外部播放器的动作不撤销；语音前端恢复中保留排队行为，不为退下切断 PNS 恢复。

57 个 Python 测试与 5 个 Node 测试通过。仅重载桥接 LaunchAgent，speaker_connected=true、
feedback_installed=true、backend=desktop、PCM 持续接收，原语音会话 ID 保留。
真实后台文字入口验证：
- “播放许嵩的有何不可”：本地确无该歌，route=local_music、music_source=netease_online、
  agent_called=false。搜索 782.9 ms，原生 URL 调用 671.0 ms，音乐命令 1621.2 ms；
  含收到/已完成与并行恢复整轮 4474.0 ms。status=opened 仅证明系统接受请求。
- “退下”：route=local_control、status=dismissed、agent_called=false，仅收到，无动态 TTS；
  提示约 1312.9 ms，整轮 3629.9 ms（含语音前端恢复）。
- “播放周杰伦的枫”：通过翻唱的原唱引用查到原唱 ID 185912，详情 st=-200、播放地址为空。
  返回 unavailable、ok=false，未发送播放请求，未假报已完成，原声播报不可播放原因。
  搜索 694.2 ms，整轮 11576.2 ms（含约 8 秒错误语音）；agent_called=false。

以上后台入口计时不含唤醒与云端 ASR，也不是耳机实际出声测量。完整新语音入口点歌与退下的
现场验收已向用户发起；上述原生 URL 出声确认与自动化回归是已完成的独立证据。


## 2026-11-15 在线点歌原生试听曲并发修复

用户确认小七能快速搜索并播放对应歌曲，但点播《幻听》时音箱原生播放器也播了试听版。
独立实机复现：原生播放状态为 1（playing）；仅配套重启 AIVS/PNS 后仍为 1；
显式 mphelper pause 后为 2（paused）。根因是已交给独立 mediaplayer 的歌曲不会随语音服务重启停止，
不能把“中断原生回复”和“停止原生音乐”当成同一件事。

`restore_native_voice` 现在在 AIVS/PNS 配套恢复前后显式暂停音箱播放器，保持原恢复等待。
另在自定义收音窗口跟踪 ASR header.dialog_id（最多 32 条，30 秒过期）；同一轮迟到的
AudioPlayer.Play 单独异步暂停。原生独立唤醒的新 dialog_id 不在接管集合，不全局拦截；
不记录播放 URL、未知文本或凭据。守卫不在 WebSocket 读取协程等待 RPC，否则会阻塞自己的响应。
断线取消守卫并清理归属，health.native_music_suppressed 记录迟到播放暂停次数。
这属于事后事件拦截与显式暂停，并未修改固件或关闭小米云端 NLP，不能声称固件级零漏音。

59 个 Python、5 个 Node 测试通过，新增恢复前后暂停、只拦接管 dialog、过期/原生 dialog 放行、
RPC 不阻塞 WebSocket 和连续 Play 不漏最后一次暂停的验证。仅重载本桥接，PCM 与原声提示正常。
实机验证：先使原生试听曲播放，状态=1；通过新后台代码入口点播“播放许嵩的幻听”，
Mac 网易云接受对应在线播放，音箱原生状态=2。search=462.9 ms、open=370.7 ms，
整轮 3793.9 ms、agent_called=false。该测试不是现场 ASR 验收；已请用户复测双播与退下。


## 2026-11-15 如果当时误入 Agent 的修复与退下验收

用户现场确认“退下”无问题。真实日志 local_control/status=dismissed、agent_called=false，
原声只回应“收到”；没有后续收音超时提醒。用户也确认在线搜索和对应歌曲播放很快。
《幻听》双播修复仍只有上段后台实机状态证据，用户本轮实际改测“播放许嵩的如果当时”，
未单独确认修复后双播结果，不把该项标记成已现场验收。

该准确原话落入 Agent 的原因已找到：旧 parse_command 对“如果”全局黑名单，误伤真实歌名。
旧轮 Agent 阶段 117587.4 ms、整轮 126336.6 ms；这些阶段日志不能证明 Agent 每次具体用了哪些脚本。
现改为识别“如果…就/则/的话…动作”等完整条件结构；歌名中单独出现“如果”不再当条件请求。
不是硬编码《如果当时》或歌手白名单；《如果没有你》《如果这就是爱情》等也通过同一通用解析。
新增 artist_query：明确“某歌手的歌/歌曲/音乐”本地优先，本地未找到时按在线实际歌手字段选可用结果，
不让 Agent 自由选点击目标，不用翻唱标题里的“原唱”代替歌手字段。
真正推荐、条件筛选和复合任务继续走 Agent；执行音乐动作应复用 netease_music.py，不用 computer use。

62 个 Python、5 个 Node 测试通过，覆盖标题条件词与真正条件结构、歌手请求、无 Agent 路由。
只重载本桥接，原生暂停/迟到播放守卫与退下逻辑保留。后台精确原话“播放许嵩的如果当时”重测成功：route=local_music、music_source=netease_online、
agent_called=false、status=opened。搜索 741.8 ms、调用网易云 457.7 ms，整轮 4111.0 ms
（含收到/已完成和语音恢复，不含云端 ASR，不等同实际出声计时）。


## 2026-11-15 Bilibili 指定视频代码点播

用户希望在本地 Bilibili 搜索播放指定视频，提供 BV18V4i6WEje：
《女生视角告诉你，女生是怎么感受到被爱的》。本机官方客户端 1.17.6，
`/Applications/哔哩哔哩.app`，bundle=com.bilibili.bilibiliPC，注册 scheme=bilipc。
只读检查本机 App 代码后确认实际入口是 bilipc://page/player（type=ugc、bvid、aid、cid），
手机 bilibili:// 和猜测的 bilipc://video 等只唤起首页，不能据此宣称播放。
用户已确认正确入口打开并播放了指定视频。没有修改客户端或拷贝/输出用户登录凭据。

新增 bilibili_video.py：BV/常规链接直接查 view 元数据，标题通过公开 search/type 搜索；
搜索需要由 finger/spi 生成的新匿名 buvid3，只在进程内存缓存，不读浏览器/App Cookie。
标点转空格作为检索关键词，完整标题仍按归一化文本核对；精确匹配优先，长的包含匹配必须唯一，
否则提示提供 BV，不盲目播放首项。直接链接的 vd_source 等跟踪参数不传客户端。
仅验证普通 UGC 第一分 P，显式 p!=1 报尚未验证，不能悄悄播第一集；短链、番剧与客户端遥控未接入。

bridge 在音乐之前分流明确视频，route=local_video、video_source=bilibili，先收到再代码执行、
成功已完成；失败固定提示，不调用 Agent 重试。推荐/复杂要求仍进 Agent。
恢复前后暂停原生播放器、AIVS/PNS 配套恢复和短期 ASR dialog_id 迟到播放守卫继续保留。
自有 .runtime/video-actions.jsonl 保存轻量动作，不写 Harness 内部会话，不制造 Agent 思考轮次。
现有暂停/继续仍操作网易云，不能据此宣称 Bilibili 完整语音遥控已接通。

73 个 Python、5 个 Node 测试通过；标点检索修正后专项 45 项测试通过。
真实元数据确认 aid=117567549082566、cid=43371006088、标题与用户一致；
原生 URL 的客户端实际播放已由用户确认。完整后台标题入口重测成功：
route=local_video、agent_called=false、status=opened、video_source=bilibili，
search=758.3 ms、metadata=956.7 ms、open=2269.3 ms、command=3228.5 ms、
含原声提示/恢复整轮 6065.9 ms，不含云端 ASR，不作为每次时延或出声时间保证。
仅重载本桥接，原桌面语音会话继续复用，未重启 Harness 或刷固件。

探索时读取的 App 代码及临时解码脚本只用于核对协议，已从自建 .runtime/bilibili-probe 清理；
没有提交第三方 App 源码、音视频、匿名标识、日志、权重或凭据。


## 2026-11-16 唤醒时延与命令停顿续说

用户反馈唤醒响应迟、命令略停顿立即收到。KWS 仍是 Mac Sherpa，从音箱麦克风 PCM 检测，
不使用 Mac 麦克风；云端 ASR 的 final 原先立即分发，不受 18 秒等待上限控制。
现在 voice_config.py 集中设置：首次等待 12 秒、总等待 30 秒、final 后 3 秒续说等待、
partial 最后活动后 10 秒超时。不同 ASR 轮片段合并，同轮修正只覆盖最新片段；简单播放前缀可连成
完整点歌，重复整句不叠加。续说 partial 未完成不执行前半句，重开麦未确认则提醒不执行。
“退下”仍立即取消收集，之前恢复中排队与配套 AIVS/PNS 恢复保留。

云端 final 后尝试静默重开麦，最多三次，不重复“哎”；未修改云端 VAD，重开麦间隙可能丢音，
并非完全连续 ASR。部署首版现场一次续收未确认，已加失败不执行保护；不能把延迟“收到”
当成实际连续录音成功。开麦脚本改比较最后 Recognize 事件，避免日志截断同条数/减少时误判。
该日志轮转边界已用真实 shell 替身回归验证。完整停顿语音体验尚待用户确认。

唤醒优化为首次暂停与开麦合并 RPC；KWS 队列最多四包（当前约 90 ms/包），高负载时丢弃旧包，
不改识别阈值。health.last_wake、command_settle_seconds、collecting_command、wake_queue_audio_ms
可定位检测后开麦/提示和队列积压。现场检测后请求“哎”约 169～310 ms，到播完约 1.23～1.40 秒，
该值不含说唤醒词和模型检测时间，也不等同实际声音起点。观察到 Mac 多进程高负载，健康查询曾延迟
十多秒；未停止任何其他任务，不能保证机器负载高时仍低延迟。

已通过分段续说、partial 未完不执行、退下立即取消、修正保留前段、完整句去重和失败不执行测试。
最终 Python 全量 80 项、Node 5 项通过；健康接口 speaker_connected=true、feedback_installed=true、
command_settle_seconds=3.0。用户现场停顿续说复测请求尚未返回。
仅重载 com.zhangyichi.xiaoai-harness，模型/音色/语音会话保留；源码未涉及 Harness 内部存储。


## 2026-11-16 续收失败误报修复与网易云冷启动

用户现场反馈每次完整点歌仍报“没听清”，网易云关闭时不能启动后播放。日志确认 22:18～22:19
四轮都已收到 final，随后 continue_capture 未确认，旧 finish_command 把可选续收失败当整句失败，
丢弃已有的完整 final。属于本桥接上一轮引入的回归，不是用户没有说话或播放器问题。
现保留已完成 final，等 3 秒后按原分流执行；只有裸动作/尾部“的/然后/并且/还有”等明显未完整句
才在续收失败时提示。若后续 partial 已开始但没 final，仍不执行前段，等待超时提醒；退下立即取消保留。
这不等于云端连续续说已修好，静默重开麦在本机仍会失败，不能宣称任意停顿都能收全。

冷启动独立复现：直接 open App+歌曲 URL 返回 0、进程出现，但用户确认只打开没播放。
修复新增 netease_player.py，先用 AppKit NSRunningApplication 查 running/finishedLaunching，
未运行先只启动 App，等启动完成，再留 2 秒给客户端媒体处理器初始化，最后发一次 URL/文件。
JXA 只读 AppKit 状态，不用 UI/辅助功能、computer use、账号存储或浏览器。
注意 AppKit 桥接数值可能是字符串“0”：必须 Number(count)>0 和 Number(finishedLaunching)===1，
不能用真值判断，否则未运行/未就绪会误判。原在线 5 秒/本地 10 秒 open 超时扩为 25 秒，
启动失败/未就绪不发媒体请求，媒体请求超时不重发。暖启动不追加两秒等待，也不重新启动 App。
本地和在线点歌复用同一 helper。暂停/继续保持原语义：客户端未运行时不凭空恢复未知歌曲。

用户已确认修复后的冷启动自动播放了正确《如果当时》。实机观察状态顺序：
未运行→启动进程→三次 ready=false→ready=true→2 秒缓冲→发送一次歌曲 URL，调用返回 0。
之前“打开但没播放”的首轮不算成功，不能把 open 返回 0 当出声证明。
85 项 Python 和 5 项 Node 测试通过；新增完整 final 不因续收失败丢弃、半句不执行、冷/暖启动顺序
及启动失败不发送歌曲的验证。真实后台入口从网易云未运行开始验证成功：route=local_music、agent_called=false、status=opened，
search=355.5 ms、open（含启动等待）=9743.5 ms、整轮 13765.7 ms，不含云端 ASR。
完整语音点歌的误报修复已请用户复测，尚待回复，不视作现场验收。


## 2026-11-16 锁屏播放与 DELL 输出回退（已现场验收）

用户反馈电源键熄屏后点歌报播放器调用失败。最早日志确有本地 open 失败、网易云 paused，
不能把该次描述成后台在播放。随后单次代码打开音乐出声，但用户指出屏幕并未保持锁定，
该次不算锁屏验证。按用户手动 Control+Command+Q 进行了两轮受控对照：
1. 默认 DELL 输出：记录已锁定时 KWS/ASR/代码音乐路径仍工作，open 请求被接受；
   用户确认锁屏时无声，解锁后才出声。只加 -g 后仍复现。
2. 保持锁定切内置：本机记录 locked=true、网易云 paused→playing；用户确认内置扬声器正常出声。
   解锁后测试程序恢复 DELL。证明当前无声与显示器输出路径有关，不等同系统禁止锁屏播放。
   不据此断言 DELL 内部的具体省电机制；早期一次播放器调用失败也未证明与显示器同源。

实际修复在独立 com.zhangyichi.headphone-output，源码 examples/macos-audio-switch/main.swift：
新增 LockRoutingPolicy，只匹配本机 DELL S2725DSM。IOKit 查活动控制台锁态，系统通知触发，
每 2 秒兜底检查；锁定时从 DELL 切内置，解锁时若仍为自身选择的内置则恢复原 DELL。
目标耳机优先，人工改选输出让出控制；锁屏期间接耳机的恢复指针归一为真实原始输出。
恢复 UID 保存在服务原私有状态，重启可恢复。不模拟解锁、不改安全/电源设置、不影响麦克风或音量。
只重载该独立服务；没有重启 Harness，也没有把路由变更交给 Agent。

本地文件点歌 open 额外使用 -g，保留单次请求和 25 秒超时；只此改变不足以解决显示器无声，
已在 docs 中明确。音乐/启动专项 18 项测试通过；Swift 编译及 25 项规则自测通过。
最终用户现场确认“自动切换和恢复都正常”。日志中国标准时间 23:03:12 locked=true 请求内置，
23:03:21 locked=false 恢复 DELL。华为耳机和锁屏交互有规则测试，未逐一硬件验收。

临时锁屏采样和诊断源码/二进制仅在自有 .runtime，不提交 Git；只提交规则代码与轻量结论。
真正系统睡眠时 Mac 服务不能保证持续运行，本次没有改防睡眠或自动解锁，不宣称解决合盖睡眠。

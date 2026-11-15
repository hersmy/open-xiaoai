# 小爱音箱接入本机 DeepSeek Harness

说 **“大爱同学”**，听到“我在”后提问，Harness 思考后通过音箱播报。
Mac 上的轻量 Sherpa 模型检测来自音箱的 16 kHz PCM，唤醒后调用原生小爱 ASR。
普通“小爱同学”继续保留；当前版本每轮需要再次唤醒，不是免唤醒连续收音。
用户已现场确认语音闭环成功。较复杂问题可能需要几十秒。
明确的本地点歌已在 Mac 桥接中直接执行：成功后不调用 Harness、不播报“我想一想”或成功提示。
曲库查询和播放失败使用代码生成固定答复；推荐、条件与复合需求继续交给 Agent。

默认优先使用已启用的 `desktop-plugin`，在 Harness 桌面“小米音箱”工作区的
**“大爱同学 · 语音对话”** 中实时显示问题、Agent 回复和工具运行。此路径已实机
调用成功且用户确认桌面可见。该会话在重启后继续复用，不必新建。
直接在桌面打字也属于同一会话；语音桥接仅播报自己发起的那轮，不会朗读任意桌面消息。

桌面插件尚未加载时，保留官方 `dsh headless --json` 的独立会话作为兼容入口。
它不等于桌面可见会话。桥接不读取模型凭据、不复制 Token、不改变既有权限。
桌面调用失败不会将同一问题自动重发到 headless，以免重复执行电脑操作。

### 桌面插件

Harness 添加本地文件夹并启用：
`/Users/zhangyichi/codex/open-xiaoai-lx06/examples/harness/desktop-plugin`

插件使用公开 `sessionController`、`workspaceRegistry` 创建/续接专用桌面会话。
只监听随机 localhost 端口，临时认证信息保存于权限 0600 的 `.runtime/desktop.json`，
不要显示或提交其中的认证信息。会话 ID 单独保存于 `desktop-session.json`。

## 运行

音箱须先刷补丁并部署官方 ARMv7 Client。Mac 需要本地 DeepSeek Harness 和 Python。

```sh
uv sync
.venv/bin/python prepare.py
.venv/bin/python bridge.py --speaker-ip <音箱IP>
```

音箱的 `/data/open-xiaoai/server.txt` 指向 `ws://<Mac局域网IP>:4399`。
将 `speaker-boot.sh` 放到音箱 `/data/open-xiaoai/boot.sh`，通过 `/data/init.sh`
调用 `sh /data/open-xiaoai/boot.sh`，即可在音箱上自动重连。

安装 Mac 登录启动服务：

```sh
.venv/bin/python service.py install --speaker-ip <音箱IP>
.venv/bin/python bridge.py --status
.venv/bin/python bridge.py --ask '请简短介绍你可以做什么'
```

最后的 `--ask` 是本机文字测试入口，使用与语音相同的分流；复杂问题实际调用 Agent 并播报，
成功的本地点歌直接打开网易云，不播报。
服务端 4399 接口只接收指定音箱 IP；管理接口 4400 只监听 localhost。
网络或 DHCP 地址变化后更新音箱 server.txt 与服务参数；无线连接不需要重刷固件。

## 对话和停止

- “大爱同学”：唤醒，等“我在”后提问，沿用当前桌面语音会话。
- 再次唤醒会取消桥接拥有的旧任务；不会取消论文或其他会话。
- 桌面会话的删除/更换请在 Harness 及插件配置中显式处理，语音不会直接删除桌面历史。
- 兼容入口“小爱同学，问助手……”仍保留，但不作为主要唤醒方式。
- `.venv/bin/python service.py stop`：停止 Mac 桥接，原生小爱继续使用。
- `service.py start`：重新启动已安装服务。

客户端/电脑服务断线会取消本轮工作，失败不会被播报为已完成。只有 Agent 的
最终成功回复进入 TTS，工具输出和推理不朗读。桌面插件 0.3.0 每轮等待上限 30 分钟；旧 headless 兼容入口默认仍为 180 秒。
长任务在 45 秒后播放等待提示，之后每两分钟一次，不会重新发送问题。
需要交互授权的任务可能由 Harness 拒绝或报错；本桥接不替用户批准工具调用。

日志与会话指针在 `.runtime/`，依赖、客户端二进制和日志不进入 Git。
Mac 服务由 `~/Library/LaunchAgents/com.zhangyichi.xiaoai-harness.plist` 管理；停止后
可移除此文件。音箱若需撤销启动，移除新增的 `/data/init.sh` 调用并停止客户端。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
node --test desktop-plugin/test.mjs
```

实际端到端还需核对 `/health`、客户端版本 RPC、真实 Harness 续接、TTS
播放结束事件，以及现场说出一个问题。电脑操作是否成功取决于 Agent 的实际
工具结果，不能从普通问答成功推断所有电脑操作都已验证。


### 只回应“我在”，随后无声音

先看健康接口中的 asr_count、listening、last_error。唤醒不代表云端 ASR 已收音。
LX06 上仅重启 mico_aivs_lab 后，前端可能仍保留旧采集状态，event_notify 返回成功
却不产生新的 Recognize 请求。当前实现已将 AIVS/PNS 配套恢复，并在 18 秒没收到
识别结果时播报提示。若复发，核对 event.log 的 Recognize 计数、客户端 FD/inode
和网络状态，不要通过反复刷固件解决。


### 有识别和“我想一想”，几分钟后报助手错误

检查日志是否恰好等待 180 秒。旧桌面插件 0.1.0 会在该时间点取消仍在运行工具的
Agent，返回 Voice turn cancelled。这是桥接自身超时，不是用户点击取消，也不代表
模型断线。0.2.0 已改为 30 分钟，连接超时同步延长，并区分超时、主动中断、会话忙碌。
修改桌面插件后，在 Harness 插件页单独关闭再启用该插件，勿重启其他研究任务。
本地认证 health 返回 version=0.2.0 或更新版本、turn_timeout_ms=1800000 才表示新限制已加载。

## 本地音乐

曲库目录：`/Volumes/PHILIPS/小米音箱/music`。目前已从原网易云下载目录复制
11 首歌（9 NCM、2 MP3）、11 份歌词和 10 张封面，逐文件 SHA-256 一致，原文件保留。
NCM 不转换、不解密，由已安装的 `/Applications/NeteaseMusic.app` 打开并解码。
用户已确认项目目录中的《如约而至》NCM 在电脑上能听到音乐。

说“大爱同学”，等“我在”后说：

- “播放许嵩的如约而至”：按歌名/歌手匹配曲库。
- “播放王菲的歌”：从该歌手的本地歌曲中选择一首。
- “播放本地音乐”：随机选择一首。
- “本地曲库有多少首歌”：查看数量和曲目。
- “暂停播放”“暂停”“停止播放”“关掉音乐”：暂停网易云，保留播放位置。
- “继续播放”“恢复播放”“播放”：继续网易云现有歌曲，重复命令保持播放。
- “下一首”“换一首”“切歌”：按项目曲库文件名排序选择下一首，末尾回到开头。
- “上一首”“上一曲”：按同一顺序选择上一首，开头回到末尾。

暂停和继续使用 `music_control.swift` 编译的固定辅助程序，仅操作网易云“控制”菜单，
读取“暂停/播放”菜单状态并在操作后再次确认，避免重复“暂停”反而恢复播放。
2026-11-15 已安装且用户授权辅助功能：`~/Applications/小米音箱音乐控制.app`。
初次安装可用 `.venv/bin/python install_music_control.py`，然后通过
`open -n -a "$HOME/Applications/小米音箱音乐控制.app" --args permission`
打开权限页，在系统设置添加并开启此程序；没有权限会明确提示。
后台控制通过 LaunchServices 启动已授权 App，并用私有临时文件读取结果；不要从
LaunchAgent 直接 exec 辅助程序，否则 macOS 可能将权限归到父服务并拒绝访问。
非必要不要重新编译已授权程序，ad-hoc 签名变化可能导致需要重新授权。

切歌位置保存在桥接自己的 `.runtime/netease-state.json`，只包含上次打开的本地曲目信息。
上一首/下一首按本程序最后点播记录执行；在网易云手动改歌后，请先用语音点播一首同步位置。
这不是网易云当前播放列表，也未增加歌曲播完自动切下一首；音量和循环模式尚未接入。

Mac 桥接在任何 Agent 调用和等待提示前解析明确点歌指令，直接在线程中调用
`netease_music.py` 的函数查找文件并交给网易云。成功后本轮结束，不需要 Agent 生成确认。
上述成功的播放、暂停、继续与切歌均静默结束，不会用 Agent 复述。
原生小爱回答的中断及 AIVS/PNS 恢复与播放控制并行，保留恢复等待，避免下一轮无法收音。
立即再次唤醒时先等待尚未结束的恢复，再打开 ASR。曲库查询使用固定答复；失败给短提示，
均不会转给 Agent 重试。含推荐、条件和复合操作等请求继续走原桌面会话。

快速操作不会创建 Harness Agent 思考轮次，记录在本机 `.runtime/music-actions.jsonl`；
普通复杂对话仍显示在“大爱同学 · 语音对话”。不直接写 Harness 内部会话文件。
桌面插件 0.3.0 的旧音乐钩子保留用于直接调用插件入口的兼容场景；正常语音已由桥接提前接管。
直接在桌面输入文字不会经过语音 HTTP 入口；开发 Agent 可使用以下命令：

```sh
cd /Users/zhangyichi/codex/open-xiaoai-lx06/examples/harness
.venv/bin/python netease_music.py --list
.venv/bin/python netease_music.py --command '播放许嵩的如约而至'
```

按 `歌手 - 歌名.扩展名` 命名；支持扫描子目录，忽略隐藏文件、歌词、封面与指向曲库外的符号链接。
环境变量 `XIAOAI_MUSIC_DIR` 可覆盖曲库位置。新增歌曲复制后下次点歌即生效，无需重建索引。
歌词和封面是可选附件，不是点歌必需文件；所有音频和附件均不提交 Git。

播放走 Mac 当前声音输出：电脑扬声器、耳机或用户自行连接的蓝牙音箱。
华为耳机连接时自动切换 Mac 输出的独立服务，见 [macOS 音频切换](../macos-audio-switch/README.md)。
无需重刷音箱。此实现尚无自动播完续播；不让 LX06 直接解码 NCM。
系统打开请求成功仅记为 `status=opened`，不能据此推断每次已经出声。
原生小爱 ASR/TTS 和 Harness 推理仍沿用原有配置，并非整套离线语音系统。

### 点歌耗时诊断

`bridge.py --status` 的 `last_turn` 包含 `route`、`agent_called`、`spoken` 和各阶段毫秒数。
本地点歌应为 `route=local_music`、`agent_called=false`、`spoken=false`。
`command_result_after_ms` 是从 Mac 分流开始到打开请求返回的耗时，不是耳机实际出声测量；
`total_ms` 包含等待语音前端恢复。`music_lookup_ms` 和 `music_open_ms` 分别反映查找与应用打开。
详细阶段也写入本机 service.log。以上日志、设备状态、原始对话均不提交 Git。

2026-11-15 文字入口实测：优化前同一句请求整轮 20.4 秒（含 Agent 和两段 TTS），
优化后两次打开请求返回约 2.30 秒、0.20 秒，整轮约 3.65 秒、3.49 秒。
查找分别 65.2/18.8 ms，调用网易云分别 2208.9/179.2 ms，语音前端恢复约 3.5 秒。
另一次优化后早期测试为 11.3 秒，尚无细分数据定位该次延迟；不要把后续较快样本当作每次保证。
这些样本不包含云端 ASR，也没有测量播放器真正出声的时间。

后续用户已现场确认连续两轮语音点歌均成功、没有额外播报；对应日志从 ASR final 到
打开请求返回约 1.92/1.50 秒，整轮都约 3.54 秒。独立暂停/继续测试约 0.14～0.42 秒，
包括重复暂停/继续状态保持检查；不含云端 ASR和并行语音前端恢复。
完整后台桥接实测暂停、继续、点歌、下一首、上一首全部成功：控制/打开返回约
0.96/1.59/2.03/0.72/0.69 秒，整轮约 3.5～3.9 秒，均无 Agent、无成功 TTS。
真实 ASR 收到的暂停/继续也已在日志核对到 paused/playing，约 0.72/0.44 秒返回；
这些控制返回时间包含状态核对，不代表云端识别耗时。

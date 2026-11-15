# 小爱音箱接入本机 DeepSeek Harness

说 **“大爱同学”**，听到“我在”后提问，Harness 思考后通过音箱播报。
Mac 上的轻量 Sherpa 模型检测来自音箱的 16 kHz PCM，唤醒后调用原生小爱 ASR。
普通“小爱同学”继续保留；当前版本每轮需要再次唤醒，不是免唤醒连续收音。
用户已现场确认语音闭环成功。较复杂问题可能需要几十秒。

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

最后的 `--ask` 是本机文字测试入口，也会实际调用 Agent 和在音箱播放。
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

桌面插件 0.3.0 会先调用 `netease_music.py` 查找并打开文件，再将实际结果与原始
语音交给同一 Harness 会话生成简短回复，避免 Agent 重复打开播放器。
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
无需重刷音箱。此实现尚无播放队列、自动下一首或暂停控制；不让 LX06 直接解码 NCM。
系统打开请求成功仅记为 `status=opened`，不能据此推断每次已经出声。
原生小爱 ASR/TTS 和 Harness 推理仍沿用原有配置，并非整套离线语音系统。

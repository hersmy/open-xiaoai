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
最终成功回复进入 TTS，工具输出和推理不朗读。默认每轮等待上限 180 秒。
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

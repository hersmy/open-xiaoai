# 小米音箱项目交接规则

- 开始先读 `docs/LOCAL_HANDOFF.md` 和 `examples/harness/README.md`，再修改运行服务。
- 当前有效源码和 Mac 服务目录是 `/Users/zhangyichi/codex/open-xiaoai-lx06`。
  `/Volumes/PHILIPS/小米音箱/open-xiaoai-lx06` 是早先下载的刷机副本，不能把它当成运行源码。
- 已刷 LX06 1.94.13，boot0/system0；基础通信已验证。日常开发通过 SSH/Wi-Fi，不重新刷机。
- 当前自定义唤醒词“你好小七”（替代旧“大爱同学”）；原生“小爱同学”继续保留。词与拼音在 `examples/harness/voice_config.py`。
- 用户指定复用本地 DeepSeek Harness 的 Agent/模型配置，不另接小智云，不复制或输出 Token。
- 本地曲库在 `/Volumes/PHILIPS/小米音箱/music`；NCM 保持原格式，交给 Mac 网易云播放，走当前系统音频输出。点歌使用 `examples/harness/.venv/bin/python examples/harness/netease_music.py --command '播放歌名'`，不要重复打开。明确歌曲未命中本地时由 `netease_online.py` 直接 HTTP 搜索并通过 orpheus 原生 URL 交给网易云，禁止用 computer use；明确“本地”的请求不联网。
- 明确点歌由 bridge 本地快速执行，不调用 Harness；原生 AIVS/PNS 恢复保留，不能为了省三秒而删除。复杂请求仍走 Agent；快速动作记在本机 `.runtime/music-actions.jsonl`，不伪造桌面思考轮次。
- 当前语音协议：确认开麦后原声“哎”，有效指令先“收到”，代码执行成功说“已完成”，复杂任务播报 Agent 回复；未听到/未听清有明确提醒。固定提示由音箱原生 TTS 生成并缓存 MP3，禁止改用 Mac 音色。近期自定义上下文中的暂停/继续不得静默丢弃，恢复中须排队而非取消 PNS 恢复。
- 暂停/继续用 `music_control.swift` 的专用辅助功能 App 控制并确认网易云菜单状态；不要改为盲目 toggle。已授权 App 在 `~/Applications/小米音箱音乐控制.app`，非必要不重编译以免签名变化导致重新授权。上一首/下一首按项目曲库与本机 `.runtime/netease-state.json` 执行。
- 华为耳机自动切换输出是独立服务 `com.zhangyichi.headphone-output`；源码和管理入口在 `examples/macos-audio-switch/`，安装于内置盘 `~/Library/Application Support/XiaoaiAudioSwitch/`，不通过重启 Harness 或音箱桥接来管理。
- 桌面实时对话目标为用户现有“小米音箱”工作区（`/Volumes/PHILIPS/小米音箱`）中的语音专用会话。
  桥接 headless 会话不等于桌面可见会话；必须实际确认插件就绪和会话归属后才能宣称已接通。
- 音箱端客户端在 `/data/open-xiaoai`，Mac LaunchAgent 是 `com.zhangyichi.xiaoai-harness`。
  修改 bridge.py 后重启此服务；不要重启整个 Harness 或影响“期刊论文实验”的进行中任务。
- 不直接编辑 Harness 的 session JSONL、投影数据库、工作区存储或凭据。使用其公开会话 API。
- 仅将代码、规则、文档和轻量验证结果提交 Git；排除 `.runtime/`、`.venv/`、模型、固件、二进制、日志、音频和秘密。
- 适当验证后白名单提交并尝试推送；未配置可写远端/认证失败必须报告未同步，不能称已发布。
- “退下”由代码精确匹配，原声“收到”后清掉收音/重开麦/待执行指令和近期接管上下文，等待下次唤醒；不调用 Agent、不暂停音乐、不删除会话。在线搜索不可播或歌手/版本不匹配时明确报错，不自动替换翻唱。

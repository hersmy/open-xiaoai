# LX06 语音接入本机 DeepSeek Harness（最终实现）

用户授权刷机、基础通信、语音 Agent，并明确改用“大爱同学”、复用本机 Harness、
将语音会话显示在已建的“小米音箱”工作区。实现状态与路径见 `docs/LOCAL_HANDOFF.md`。

链路：音箱麦克风 PCM → Mac 本地 Sherpa KWS（大爱同学）→ 音箱提示“我在”→
ubus 打开原生 ASR → 最终问题 → Harness 桌面插件的公开 sessionController →
专用持久会话 → 成功的最终回复 → 原生 TTS。普通“小爱同学”保留。

桌面插件通过 workspaceRegistry 复用 `/Volumes/PHILIPS/小米音箱`，创建并续接
“大爱同学 · 语音对话”。不接管研究会话，不改权限，不改凭据，不拼接 JSONL。
桥接与桌面插件之间只使用随机 loopback 端口和本机 0600 临时认证文件。
未装桌面插件时可用官方 headless CLI 兼容入口；失败不自动换后端重试同一问题。

只朗读完成的 final 文本，推理、工具调用及失败中间结果不朗读。保留会话指针，
每轮须唤醒；接收新问题或断线时取消当前桥接拥有的任务。TTS 参数按 shell 单参数转义。
启动路径：音箱 /data/init.sh 调用客户端，Mac LaunchAgent 管理桥接；无需再刷机。

验证包括协议/权限边界测试、超时进程清理、模型离线 KWS、实际 PCM 和版本 RPC、
Harness bash/pwd、用户现场唤醒问答确认，以及插件真实桌面会话调用/播放和用户可见性确认。
暂未实现免唤醒连续收音或低延迟逐 token 播报。模型、音频、运行环境、秘密及日志不进 Git。

# 小米音箱项目交接规则

- 开始先读 `docs/LOCAL_HANDOFF.md` 和 `examples/harness/README.md`，再修改运行服务。
- 当前有效源码和 Mac 服务目录是 `/Users/zhangyichi/codex/open-xiaoai-lx06`。
  `/Volumes/PHILIPS/小米音箱/open-xiaoai-lx06` 是早先下载的刷机副本，不能把它当成运行源码。
- 已刷 LX06 1.94.13，boot0/system0；基础通信已验证。日常开发通过 SSH/Wi-Fi，不重新刷机。
- 用户确定自定义唤醒词“大爱同学”；原生“小爱同学”继续保留。
- 用户指定复用本地 DeepSeek Harness 的 Agent/模型配置，不另接小智云，不复制或输出 Token。
- 桌面实时对话目标为用户现有“小米音箱”工作区（`/Volumes/PHILIPS/小米音箱`）中的语音专用会话。
  桥接 headless 会话不等于桌面可见会话；必须实际确认插件就绪和会话归属后才能宣称已接通。
- 音箱端客户端在 `/data/open-xiaoai`，Mac LaunchAgent 是 `com.zhangyichi.xiaoai-harness`。
  修改 bridge.py 后重启此服务；不要重启整个 Harness 或影响“期刊论文实验”的进行中任务。
- 不直接编辑 Harness 的 session JSONL、投影数据库、工作区存储或凭据。使用其公开会话 API。
- 仅将代码、规则、文档和轻量验证结果提交 Git；排除 `.runtime/`、`.venv/`、模型、固件、二进制、日志、音频和秘密。
- 适当验证后白名单提交并尝试推送；未配置可写远端/认证失败必须报告未同步，不能称已发布。

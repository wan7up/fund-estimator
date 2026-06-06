# 安全说明

本项目是自部署基金辅助工具，公开仓库不包含任何真实凭证。

请不要提交以下内容：

- Cloudflare API Token、GitHub Token、SSH 私钥。
- OpenAI 或兼容模型服务的 API Key。
- 飞书 App ID/App Secret、tenant access token、用户 open_id。
- `data/` 目录、SQLite 数据库、飞书通知状态文件、AI 配置文件和会话文件。
- 服务器证书、Cloudflare DNS 插件配置和 `.env` 私有配置。

如果你误提交了真实密钥：

1. 立即到对应平台撤销该密钥。
2. 重新生成新密钥并只放在服务器环境变量或运行时数据目录。
3. 必要时清理 Git 历史，但不要把“删掉文件”等同于密钥安全。

部署前可以运行：

```bash
git status --short
git grep -n -I -E 'cfut_|ghp_|github_pat_|AKIA|AIza|xox[baprs]-|sk-[A-Za-z0-9_-]{12,}|tenant_access_token|app_secret'
```

命中结果需要人工判断。代码字段名、测试占位值不等于真实密钥，但真实密钥一旦进入提交历史就应视为泄露。

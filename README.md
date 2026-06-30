# 基金工具箱

基金工具箱是一个面向手机浏览器和自部署服务器的基金辅助看板。项目使用 FastAPI 提供 API 和静态页面，默认首页集成三个工具：

- `实时估值`：根据国内场外公募基金披露持仓和实时行情估算盘中净值。
- `基金对比`：对 2-4 只基金做基础画像、评分、相似度和差异点对比。
- `套利提醒`：监控跨境 LOF/ETF 的场内溢价、折价、申购赎回状态和通知机会。

> **基于公开持仓和实时行情计算的估算净值，不是基金公司公布的官方净值，仅供研究和参考，不构成投资建议。**

## 使用范围

本项目仅供个人学习、研究和非商业使用。不得将本项目或其输出用于商业服务、付费产品、代客理财、投资顾问销售、自动化交易决策或其它商业用途。

项目展示的估算净值、溢价率、对比评分、AI 短评和通知内容均来自公开数据与程序计算，可能存在延迟、错误或偏差。使用者应自行核验数据并独立判断风险。

## 功能

- 单页工具台：`/` 默认展示实时估值，`/estimate`、`/compare`、`/arbitrage` 可直达对应标签。
- 基金代码/名称搜索。
- 按浏览器设备 ID 分组的自选基金列表，适合个人或小团队自部署使用。
- 单基金和批量实时估值。
- 披露权重、前十归一和关联板块增强三种估值口径。
- 前十大持仓贡献拆解、关联板块代理、前十股票占净值比和风险说明。
- 页面聚焦自选基金估值、官方净值、官方涨跌、预计估值、预计涨跌和前十股票占净值比。
- 支持跳转好买基金手机版，完整基金档案优先使用外部平台查看。
- Web 页面在 A 股开市期间每 15 秒自动刷新估算净值。
- SQLite 缓存基金信息、净值、持仓和短时行情。
- FastAPI HTTP API 与内置 Web 看板。
- 基金对比页支持可选 AI 短评，API Key 只保存在部署实例本地数据目录。
- 套利提醒页支持可选飞书 OpenAPI 通知，飞书凭证只保存在部署实例本地数据目录。
- 轻量 PWA：支持 iOS Safari 添加到主屏幕，提供 manifest 和自定义图标；不离线缓存实时行情。

## 快速启动

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn fund_estimator.api.app:app --reload --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。

Docker Compose 快速体验：

```bash
docker compose -f docker-compose.quickstart.yml up -d --build
```

打开 `http://服务器IP:8000`。SQLite 数据默认保存在 `./data/fund_estimator.sqlite3`。

仓库里的 `docker-compose.yml` 是带 HTTPS 反代的生产模板，适合已经准备好证书和 Nginx 配置的服务器；第一次部署建议先使用 `docker-compose.quickstart.yml`。

默认启动会连接真实东方财富/天天基金数据源。离线演示可强制使用内置 mock 数据：

```powershell
$env:FUND_ESTIMATOR_FORCE_MOCK = '1'
.\.venv\Scripts\python.exe -m uvicorn fund_estimator.api.app:app --reload --host 127.0.0.1 --port 8000
```

如果真实数据源请求失败，接口会返回明确错误。只有显式开启下面这个变量时，真实源失败才会回退到 mock：

```powershell
$env:FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK = '1'
```

## API

- `GET /health`
- `GET /api/funds/search?q=001438`
- `GET /api/funds/{code}/nav`
- `GET /api/funds/{code}/holdings`
- `GET /api/estimate?code=001438&mode=both`
- `POST /api/estimate/batch`
- `GET /api/source/status`
- `GET /api/watchlist`
- `POST /api/watchlist/{code}`
- `DELETE /api/watchlist/{code}`
- `GET /api/lof/opportunities`
- `GET /api/lof/search?q=161128`
- `GET /api/lof/{code}`
- `GET /api/lof/watchlist`
- `POST /api/lof/watchlist/{code}`
- `DELETE /api/lof/watchlist/{code}`
- `PUT /api/lof/watchlist/order`
- `GET /api/lof/notice/status`
- `PUT /api/lof/notice/settings`
- `POST /api/lof/notice/feishu/connect`
- `POST /api/lof/notice/feishu/poll`
- `POST /api/lof/notice/feishu/disconnect`
- `POST /api/lof/notice/test`
- `GET /api/etf/opportunities`
- `POST /api/compare`
- `GET /api/compare/ai/status`
- `POST /api/compare/ai/login`
- `PUT /api/compare/ai/config`
- `GET /api/compare/ai/models`
- `POST /api/compare/ai/commentary`

示例：

```json
{
  "fund_code": "001438",
  "fund_name": "易方达瑞享混合E",
  "official_nav": 9.8255,
  "official_nav_date": "2026-05-25",
  "actual_change_pct": 3.03,
  "estimated_nav": 9.9132,
  "estimated_nav_date": "2026-05-26",
  "estimated_change_pct": 0.89,
  "valuation_status": "estimated",
  "is_official_nav": false,
  "holdings_date": "2026-03-31",
  "top10_weight_sum": 73.11,
  "confidence": "medium"
}
```

## 估值逻辑

`/api/estimate` 会先检查最新正式净值日期：

- 如果最新净值日期已经是 Asia/Shanghai 当天，返回 `valuation_status=official_nav`，`estimated_nav` 直接等于官方净值，不再使用持仓和实时行情估算。
- 如果当天官方净值尚未更新，返回 `valuation_status=estimated`，再按前十大持仓计算盘中估值。
- 响应始终明确返回 `official_nav + official_nav_date`；估算可用时返回 `estimated_nav + estimated_nav_date`，官方净值已出时 `estimated_nav_date` 为 `null`。
- `estimated_nav_date` 按 A 股交易时段标记：9:30 前使用上一个交易日，9:30 起使用当天，15:00 后保持当天最终估算日期。
- `actual_change_pct` 使用东方财富净值序列中的官方 `equityReturn` 字段，不用两个净值自行倒推。

页面术语：

- `预计估值`：按披露持仓和实时行情计算出的本工具估算净值。
- `预计涨跌`：本工具主展示的基金估算涨跌；优先使用关联板块增强口径，无法识别关联板块时退回披露权重口径。
- `官方涨跌`：数据源披露的最新官方净值日涨跌幅，日期为最新官方净值日期。
- `前十股票涨跌`：将可估值的前十大股票持仓归一到 100% 后的组合涨跌，用来观察这些股票本身当天怎么走。
- `前十股票占净值比`：前十大股票持仓权重合计，字段来自东方财富持仓表的“占净值比例”。该值可能很低，通常说明基金股票仓位低、持仓分散，或前十股票只占基金净值的一小部分。
- `外部详情`：好买基金使用手机版 `https://m.howbuy.com/fund/{基金代码}/`。

披露权重口径直接使用持仓披露权重：

```text
r_top10 = sum(weight_i * return_i)
estimated_nav = last_nav * (1 + r_top10)
```

前十归一口径会先将可估值的前十大持仓权重归一到 100%，用于观察组合本身的盘中走势。接口默认返回 `raw`、`normalized` 和 `enhanced` 三种机器可读结果。

关联板块增强口径用于处理“前十大之外的股票仓位”：系统会根据基金名称、类型和前十大持仓识别主题线索，并选择一个可实时报价的场内代理标的。计算方式为：

```text
residual_stock_weight = max(股票仓位 - 可估值前十大权重, 0)
enhanced_return = raw_return + residual_stock_weight * theme_proxy_return
```

接口字段：

- `raw`：只按披露权重计算，等价于“前十大对基金净值的直接贡献”。
- `normalized`：将前十大归一到 100%，用于观察重仓组合自身走势。
- `enhanced`：`raw` 加上关联板块对未披露股票仓位的代理贡献。
- `theme_proxy`：增强估值使用的关联板块、代理代码、代理涨跌和代理权重。

页面主展示优先使用 `enhanced`；如果没有可识别的关联板块或代理行情，则退回 `raw`。

## 回测研究

可以用脚本对最近官方净值日做粗略误差对比：

```bash
python scripts/backtest_estimates.py 011370 --days 20
python scripts/backtest_estimates.py 011370 001438 --days 30 --json
```

脚本会比较 `raw`、`normalized`、`enhanced` 与官方日涨跌幅的平均绝对误差。注意它使用当前可取得的最近一期持仓回看近期行情，并不代表历史当天真实持仓，因此只适合研究估值口径方向，不适合当作严格绩效归因。

关联板块代理 ETF 也可以单独验证。脚本会把目标基金和候选 ETF 的官方净值日涨跌幅对齐，按平均绝对误差和相关性排序：

```bash
python scripts/select_theme_proxy.py 011370 --theme cpo --days 20
python scripts/select_theme_proxy.py 011370 --candidates 159994 515050 515880 159507 --days 30 --json
```

代理选择原则是：先从同主题可实时报价 ETF 中形成候选池，再优先选择历史官方涨跌 MAE 更低、相关性更高的候选。这个结果只作为当前主题代理的证据，后续如果基金风格或市场 ETF 产品发生变化，应重新跑脚本确认。

## 数据源与缓存

真实数据源通过 provider 层封装：

- 天天基金/东方财富基金代码搜索。
- 东方财富基金净值页面数据。
- 东方财富 F10 前十大持仓。
- 东方财富 push2 股票实时行情。
- 新浪 A 股行情兜底源。
- 东方财富基金详情字段，包括阶段收益、规模、资产配置、基金经理、费率和同类排名。
- 东方财富场内基金实时行情，用于 LOF 场内价格、涨跌幅和成交额。
- 东方财富 F10 费率页面，尽力解析 LOF 申购/赎回状态和限额。
- HaoETF 公开页面，用于核心跨境 LOF 的实时估值、实时溢价和相关期货字段。
- Yahoo Finance/yfinance 代理行情兜底，用于核心跨境 LOF 的盘中估算净值。

缓存默认写入 `data/fund_estimator.sqlite3`：

- 基金信息/净值：10 分钟。
- 持仓：1 天。
- 股票行情：15 秒。
- LOF 场内行情：15 秒。
- LOF 海外代理行情：15 秒。
- LOF 申购/赎回状态：10 分钟。
- mock 演示模式默认写入 `data/fund_estimator.mock.sqlite3`，避免污染真实数据缓存。

相关环境变量：

- `FUND_ESTIMATOR_DB`：SQLite 文件路径。
- `FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK=0`：关闭 mock 兜底。
- `FUND_ESTIMATOR_FORCE_MOCK=1`：强制使用内置演示数据。
- `FUND_ESTIMATOR_BATCH_CONCURRENCY=4`：自选基金批量估值并发数，默认 4，允许范围 1-8。
- `LOF_NOTICE_DIR`：LOF 通知状态与 ledger 目录，默认跟随 `FUND_ESTIMATOR_DB` 所在目录或 `data/`。
- `LOF_NOTICE_ENABLED=1`：启用 LOF worker 飞书通知。
- `LOF_FEISHU_TIMEOUT_SECONDS=30`：飞书接入和发送接口超时时间。
- `LOF_NOTICE_DAILY_SUMMARY_TIME=10:00`：每日固定摘要时间，页面里的飞书通知设置会覆盖这个默认值。
- `LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY=1`：无可操作机会时也发送一条简短确认。
- `FUND_ESTIMATOR_COMPARE_AI_PASSWORD`：基金对比 AI 配置页管理密码；不设置时 AI 配置功能关闭。

## 敏感信息

公开仓库不应包含任何真实凭证。以下内容必须只放在服务器环境变量或运行时数据目录中：

- Cloudflare API Token、GitHub Token、SSH 私钥。
- OpenAI 或兼容模型服务的 API Key。
- 飞书 App ID/App Secret、tenant access token、用户 open_id。
- `data/` 目录、SQLite 数据库、飞书通知状态文件、AI 配置文件和会话文件。

本仓库通过 `.gitignore` 和 `.dockerignore` 默认忽略这些运行时文件。提交前建议至少执行：

```bash
git status --short
git grep -n -I -E 'cfut_|ghp_|github_pat_|AKIA|AIza|xox[baprs]-|sk-[A-Za-z0-9_-]{12,}|tenant_access_token|app_secret'
```

如果误提交过真实密钥，应立即在对应平台撤销并重新生成；仅从最新提交删除文件不能让历史中的密钥失效。

## LOF 溢价监控

LOF 工具第一版聚焦核心跨境 LOF 和个人自选，不做宽泛全市场列表。页面会展示：

- 场内实时价格、涨跌幅、成交额。
- 最新官方净值和官方溢价率。
- 基于海外代理行情的估算净值和估算溢价率。
- 申购/赎回状态、日累计限额、费率和风险标记。

默认机会阈值：

- 普通机会：溢价或折价绝对值达到 2%。
- 强机会：溢价或折价绝对值达到 5%。
- 每日飞书摘要：默认 10:00 发送可操作 LOF 代码、估算溢价、参考标的期间涨幅、成交额和限额。
- 飞书即时通知仍保留在 `scan --notify`，默认触发线是估算溢价达到 3%，或估算折价达到 -5%。

每日飞书摘要由 Web 应用内置调度器处理：页面里修改通知时间或开关后，后端会保存设置并立即重排下一次执行时间；当前 Docker 部署不需要额外启用 systemd timer。

手动 worker：

```bash
python -m fund_estimator.lof_worker daily-summary
python -m fund_estimator.lof_worker scan --notify
python -m fund_estimator.lof_worker send-test
```

备用 systemd 部署文件：

- `deploy/systemd/fund-estimator-lof-daily-notice.service`
- `deploy/systemd/fund-estimator-lof-daily-notice.timer`
- `deploy/systemd/fund-estimator-lof-worker.service`
- `deploy/systemd/fund-estimator-lof-worker.timer`

`fund-estimator-lof-daily-notice.timer` 仅作为不用 Web 内置调度器时的备用方案。`fund-estimator-lof-worker.timer` 是每分钟扫描/即时通知方案，后续需要实时盯盘时再启用。飞书机器人凭证由页面扫码接入流程创建，并保存在 `LOF_NOTICE_DIR` 的状态文件里；模板见 `deploy/systemd/lof-notice.env.example`。

飞书接入流程：

1. 打开监控页，点击“接入飞书”。
2. 页面会生成飞书机器人接入二维码。
3. 用飞书扫码并按提示配置机器人。
4. 页面轮询到配置结果后保存机器人 App ID/App Secret 和用户 open_id。
5. 点击“测试”，确认机器人消息能送达。

## Linux 部署

服务器部署建议用 Docker 或 systemd。手机访问时不要绑定 `127.0.0.1`，服务需要监听 `0.0.0.0`，再通过服务器 IP、域名或 Nginx 反代访问。

Docker：

```bash
docker build -t fund-estimator .
docker run -d --name fund-estimator \
  -p 8000:8000 \
  -v fund-estimator-data:/app/data \
  -e FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK=0 \
  fund-estimator
```

systemd：

```bash
sudo useradd --system --home /opt/fund-estimator --shell /usr/sbin/nologin fundestimator
sudo mkdir -p /opt/fund-estimator /var/lib/fund-estimator
sudo rsync -a --delete ./ /opt/fund-estimator/
sudo chown -R fundestimator:fundestimator /opt/fund-estimator /var/lib/fund-estimator
python3.12 -m venv /opt/fund-estimator/.venv
/opt/fund-estimator/.venv/bin/pip install -r requirements.txt
sudo cp deploy/systemd/fund-estimator.service /etc/systemd/system/
sudo install -d -m 0750 /etc/fund-estimator
sudo cp deploy/systemd/lof-notice.env.example /etc/fund-estimator/lof-notice.env
sudo editor /etc/fund-estimator/lof-notice.env
sudo systemctl daemon-reload
sudo systemctl enable --now fund-estimator
```

Nginx 反代示例在 `deploy/nginx/fund-estimator.conf`。生产环境建议配 HTTPS；如果只在家庭局域网测试，可以直接用 `http://服务器IP:8000` 在手机浏览器打开。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用 fake/mock 数据，不依赖外部网络。

## 错误码

- `FUND_NOT_FOUND`：基金代码不存在。
- `HOLDINGS_NOT_AVAILABLE`：基金没有可用持仓数据。
- `QUOTE_FETCH_FAILED`：实时股票行情获取失败。
- `UNSUPPORTED_FUND_TYPE`：持仓资产无法映射到支持行情代码。
- `INVALID_FUND_CODE`：基金代码不是 6 位数字。

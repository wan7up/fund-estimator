# 场外基金实时估值预测器

一个用于国内场外公募基金盘中实时估值的原型工具。它基于最近披露的前十大持仓，叠加实时股票行情，估算基金当日净值变化。

> **基于公开持仓和实时行情计算的估算净值，不是基金公司公布的官方净值，仅供研究和参考，不构成投资建议。**

## 功能

- 基金代码/名称搜索。
- 单用户本地自选基金列表。
- 单基金和批量实时估值。
- 披露权重与前十归一两种估值口径。
- 前十大持仓贡献拆解、前十股票占净值比、估值置信度和风险说明。
- 页面聚焦自选基金估值、官方净值、官方涨跌、预计估值、预计涨跌、前十股票占净值比和估值置信度。
- 支持跳转天天基金手机版和好买基金手机版，完整基金档案优先使用外部平台查看。
- Web 页面在 A 股开市期间每 15 秒自动刷新估算净值。
- SQLite 缓存基金信息、净值、持仓和短时行情。
- FastAPI HTTP API 与内置 Web 看板。

## 快速启动

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn fund_estimator.api.app:app --reload --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。

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
- `预计涨跌`：按披露权重计算出的基金估算涨跌，也是页面主展示口径。
- `官方涨跌`：数据源披露的最新官方净值日涨跌幅，日期为最新官方净值日期。
- `前十股票涨跌`：将可估值的前十大股票持仓归一到 100% 后的组合涨跌，用来观察这些股票本身当天怎么走。
- `前十股票占净值比`：前十大股票持仓权重合计，字段来自东方财富持仓表的“占净值比例”。该值可能很低，通常说明基金股票仓位低、持仓分散，或前十股票只占基金净值的一小部分。
- `估值置信度`：只评价“持仓穿透估算”的可信程度，不评价基金本身好坏。
- `外部详情`：天天基金使用手机版 `https://unitmob.1234567.com.cn/mpz/detail.html?code={基金代码}`；好买基金使用手机版 `https://m.howbuy.com/fund/{基金代码}/`。

披露权重口径直接使用持仓披露权重：

```text
r_top10 = sum(weight_i * return_i)
estimated_nav = last_nav * (1 + r_top10)
```

前十归一口径会先将可估值的前十大持仓权重归一到 100%，用于观察组合本身的盘中走势。接口默认返回两种结果，页面主展示值使用披露权重口径。接口字段名仍保留 `raw` / `normalized` 作为机器可读字段。

## 数据源与缓存

真实数据源通过 provider 层封装：

- 天天基金/东方财富基金代码搜索。
- 东方财富基金净值页面数据。
- 东方财富 F10 前十大持仓。
- 东方财富 push2 股票实时行情。
- 新浪 A 股行情兜底源。
- 东方财富基金详情字段，包括阶段收益、规模、资产配置、基金经理、费率和同类排名。

缓存默认写入 `data/fund_estimator.sqlite3`：

- 基金信息/净值：10 分钟。
- 持仓：1 天。
- 股票行情：15 秒。
- mock 演示模式默认写入 `data/fund_estimator.mock.sqlite3`，避免污染真实数据缓存。

相关环境变量：

- `FUND_ESTIMATOR_DB`：SQLite 文件路径。
- `FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK=0`：关闭 mock 兜底。
- `FUND_ESTIMATOR_FORCE_MOCK=1`：强制使用内置演示数据。

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

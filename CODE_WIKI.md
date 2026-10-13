# 基金工具箱（fund-estimator）Code Wiki

> - 生成日期：2026-10-12（基于 `worktree/` 当前代码与文档全量分析）；2026-10-13 起本文档移入仓库随 GitHub 维护，原工作区散置说明文档已并入本文档
> - 路径约定：除特殊说明外，本文中的源码路径均相对仓库根目录（即本文档所在目录，本机绝对路径 `/Users/mac/Documents/Trae-CN/fund-valuation/worktree`）
> - 项目性质：个人自部署的基金辅助研究工具，**非商业用途，输出不构成投资建议**

---

## 目录

1. [项目概览](#1-项目概览)
2. [工作区与仓库结构](#2-工作区与仓库结构)
3. [技术栈与依赖](#3-技术栈与依赖)
4. [系统架构](#4-系统架构)
5. [后端模块详解](#5-后端模块详解)
6. [前端模块详解](#6-前端模块详解)
7. [API 参考](#7-api-参考)
8. [环境变量与配置](#8-环境变量与配置)
9. [运行时数据与敏感文件](#9-运行时数据与敏感文件)
10. [测试体系](#10-测试体系)
11. [运维与研究脚本](#11-运维与研究脚本)
12. [部署方案](#12-部署方案)
13. [安全约定](#13-安全约定)
14. [多 Session 协作约定](#14-多-session-协作约定)
15. [错误码参考](#15-错误码参考)
16. [常用命令速查](#16-常用命令速查)
17. [移交备注（重要）](#17-移交备注重要)

---

## 1. 项目概览

基金工具箱是一个面向**手机浏览器**和**自部署 Linux 服务器**的基金辅助看板，FastAPI 提供 API + 静态页面，单页工具台集成三个工具：

| 工具 | 直达路径 | 功能 |
|---|---|---|
| 实时估值 | `/`、`/estimate` | 根据场外公募基金披露持仓 + 实时行情估算盘中净值（三种口径） |
| 基金对比 | `/compare` | 对 2-4 只基金做画像、评分、相似度、差异点对比，可选 AI 短评 |
| 套利提醒 | `/arbitrage` | 监控跨境 LOF/ETF 的场内溢价/折价、申赎状态，飞书通知机会 |

### 权威来源（三处一致才算数）

| 来源 | 位置 |
|---|---|
| GitHub | `https://github.com/wan7up/fund-estimator`（默认分支 `main`） |
| Trae 工作副本 worktree | `fund-valuation/worktree`（本工作区内，可自由修改） |
| 线上服务器 | `/opt/fund-estimator`，访问地址 `https://fund.mbl.dpdns.org`，SSH 用户 `root` |

约定：**本机只做开发和验证，不跑长期 worker / 飞书通知 / 定时任务**；线上运行以服务器 Docker/服务状态为准。

### 重要边界（来自《项目管理说明.md》，该文件已并入本文档）

- 场外估值、基金对比、LOF 套利属于**不同功能面**，改一个不要顺手改另一个。
- LOF 主列表是"完整监控列表"，不是机会榜；阈值只用于风险标记和飞书通知。
- 申购/赎回状态 `unknown` **不能简单视为暂停**；只有明确"暂停申购"才从通知候选剔除。
- LOF 页面默认按 `official_premium_pct` 从高到低排序，默认隐藏明确"暂停申购"的基金；申赎状态两行展示（申购：状态/限额/费率；赎回：状态/费率）。
- 飞书通知以服务器配置和状态文件（state/ledger）为准，本机不保留通知状态；排查先查服务器，不读本机 `data/`。
- 服务器与 GitHub 访问：凭据（token/私钥/密钥）只放在本机用户级目录或服务器环境文件，不入聊天、不入仓库。

---

## 2. 工作区与仓库结构

### 2.1 整体工作区布局

```text
fund-valuation/                      # Trae 工作区根目录（2026-10-13 起只保留 worktree/）
└── worktree/                        # ★ git 仓库，所有代码+文档在这里
    └── CODE_WIKI.md                 # 本文档（代码全览，随仓库走，GitHub 可见）
```

> 2026-10-13 起，原工作区根目录的 `README.md`、`项目管理说明.md`、`服务器与GitHub凭据说明.md` 已合并进本文档（要点见 §1 重要边界、§16、§17），本机不再保留散文件；要看任何说明，以 GitHub 上的本文件为准。

### 2.2 worktree 仓库结构

```text
worktree/
├── CODE_WIKI.md                      # 本文档：代码全览（架构/API/部署/协作约定）
├── fund_estimator/                  # Python 包主体
│   ├── api/
│   │   └── app.py                   # FastAPI 应用工厂 + 全部路由（约 630 行）
│   ├── data_sources/                # 数据源 provider 层
│   │   ├── base.py                  # 抽象接口
│   │   ├── eastmoney.py             # 主源：东方财富/天天基金（约 648 行）
│   │   ├── sina.py                  # A 股行情兜底源
│   │   ├── mock.py                  # 内置演示数据（约 504 行）
│   │   ├── composite.py             # 组装 fallback 链
│   │   └── trading_calendar.py      # A 股交易日判断（chinese-calendar）
│   ├── models/                      # Pydantic 模型
│   │   ├── schema.py                # 估值/持仓/净值/自选等核心模型（约 421 行）
│   │   ├── lof.py                   # LOF 监控与通知模型
│   │   └── etf.py                   # ETF 监控模型
│   ├── services/                    # 业务逻辑层（核心）
│   │   ├── estimator.py             # 估值引擎（约 767 行）
│   │   ├── theme_proxy.py           # 关联板块代理识别
│   │   ├── confidence.py           # 置信度分级
│   │   ├── cache.py                 # SQLite TTL 缓存（约 345 行）
│   │   ├── watchlist.py             # 场外基金自选
│   │   ├── exceptions.py            # AppError/错误码体系
│   │   ├── http_settings.py         # 全局 HTTP 超时/UA 配置
│   │   ├── lof.py                   # LOF 溢价监控（约 1508 行）
│   │   ├── lof_config.py            # 核心跨境 LOF 清单 + 海外代理配置
│   │   ├── etf.py                   # ETF 溢价监控（约 463 行）
│   │   ├── etf_config.py            # 核心跨境 ETF 清单
│   │   ├── lof_notifications.py     # 飞书通知 + 状态/ledger（约 1391 行）
│   │   ├── lof_notice_scheduler.py  # 每日摘要调度器
│   │   ├── comparison.py            # 基金对比（约 1429 行）
│   │   └── compare_ai.py            # OpenAI 兼容模型网关（约 528 行，统筹专属）
│   ├── web/                         # 纯静态前端（零构建）
│   │   ├── index.html / shell.js / shell.css      # 统一工具壳
│   │   ├── manifest.webmanifest     # 轻量 PWA（无 service worker）
│   │   ├── estimate.html / app.js / styles.css   # 实时估值页
│   │   ├── arbitrage.html / lof_app.js / lof.css  # 套利提醒页
│   │   ├── compare.html / compare_app.js / compare.css  # 基金对比页
│   │   └── vendor/qrcode.js         # 飞书二维码库
│   └── lof_worker.py                # LOF worker CLI 入口
├── scripts/                          # 运维/研究脚本（见 §11）
├── tests/                            # pytest 测试套件（9 文件 116 测试，2026-10-12 后含全局列表语义）
├── deploy/                           # nginx / systemd / certbot 部署模板
│   ├── nginx/fund-estimator.conf    # HTTP 反代（裸机 systemd 场景）
│   ├── nginx/fund-tools.conf         # HTTPS 反代（Compose 场景）
│   └── systemd/                     # 8 个 unit 文件
├── data/                             # 运行时数据（gitignore，见 §9）
├── Dockerfile                        # python:3.12-slim 单阶段构建
├── docker-compose.quickstart.yml     # 快速体验（单容器，8000 直出）
├── docker-compose.yml                # 生产模板（app + nginx HTTPS 反代）
├── requirements.txt                  # 依赖清单
├── pyproject.toml                    # 仅 pytest 配置
├── .env.example                      # 环境变量模板
├── README.md                         # 项目主文档（最权威的功能说明）
├── 协作说明.md                        # 多 session 协作边界（见 §14）
├── SECURITY.md                       # 敏感信息约定
└── LICENSE                           # Non-Commercial Personal Research License
```

代码规模：约 **18,700 行**（Python + 前端 HTML/JS/CSS），最大文件为 `services/lof.py`（1508 行）、`services/comparison.py`（1429 行）、`services/lof_notifications.py`（1391 行）。

---

## 3. 技术栈与依赖

| 组件 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.12 | Dockerfile 使用 `python:3.12-slim` |
| Web 框架 | FastAPI ≥0.115 + uvicorn[standard] | 单应用同时服务 API 和静态页面 |
| HTTP 客户端 | httpx ≥0.27（含 socksio） | provider 层抓取外部数据 |
| HTML 解析 | lxml ≥5.0 | 东财 F10 持仓/费率页面解析 |
| 数据模型 | pydantic ≥2.7 | 全部 API 模型 |
| 海外行情 | yfinance ≥0.2 | 跨境 LOF/ETF 的海外代理行情兜底 |
| 交易日历 | chinese-calendar ≥1.11 | A 股节假日判断（近期加入，见 git log） |
| 测试 | pytest ≥8.0 | 全离线 fake/mock 数据 |
| 表单 | python-multipart ≥0.0.9 | 语音文件上传 |
| 前端 | 原生 HTML/JS/CSS | **无构建工具、无框架**；iframe 隔离各工具页 |
| 数据库 | SQLite | 单文件缓存 + 自选列表存储 |
| 部署 | Docker / docker-compose / systemd / Nginx / certbot | 见 §12 |

`pyproject.toml` 仅含 pytest 配置（`testpaths=["tests"]`、`pythonpath=["."]`、`addopts="-q"`），**不是**打包配置。

---

## 4. 系统架构

### 4.1 分层总览

```text
┌─────────────────────────────────────────────────────────────┐
│ 浏览器（手机优先）                                            │
│  index.html 工具壳 (shell.js 切换 4 个 iframe 工具页)          │
│  localStorage：deviceId / 今日估值缓存 / AI 聊天记录           │
└───────────────┬─────────────────────────────────────────────┘
                │ fetch / SSE
┌───────────────▼─────────────────────────────────────────────┐
│ FastAPI  api/app.py  create_app()                            │
│  ├─ 页面路由：/ /estimate /arbitrage /compare /monitor │
│  ├─ /tool/* → 各工具独立页面                                   │
│  ├─ /api/* → 业务 API（37 个端点）                            │
│  ├─ /static → web/ 静态资源                                   │
│  ├─ lifespan 启动 LofDailyNoticeScheduler（每日飞书摘要）      │
│  └─ AppError 统一异常处理 → ApiErrorResponse                  │
├──────────────────────────────────────────────────────────────┤
│ services 业务层                                               │
│  estimator.py   估值引擎（raw/normalized/enhanced 三口径）     │
│  comparison.py  基金对比（评分/相似度/差异点）                 │
│  compare_ai.py  OpenAI 兼容网关（配置存 data/，密钥不出服务器）  │
│  lof.py/etf.py  溢价监控流水线 + 机会分级                      │
│  lof_notifications.py  飞书通知（QR 接入/token/消息/ledger）   │
│  cache.py       SQLite TTL 缓存（自选列表也在这里）             │
│  watchlist.py   自选列表服务（全局共享，见 §5.4）                │
│  theme_proxy.py 关联板块代理识别                               │
│  confidence.py  置信度分级                                     │
├──────────────────────────────────────────────────────────────┤
│ data_sources provider 层（composite.py 组装 fallback 链）      │
│  eastmoney.py（主源）→ sina.py（A股兜底）/ yfinance（海外兜底）  │
│  mock.py（演示模式）   trading_calendar.py（交易日/节假日）     │
└──────────────────────────────────────────────────────────────┘
                │ httpx
   外部：东方财富/天天基金、新浪行情、HaoETF、Yahoo Finance、飞书 OpenAPI
```

### 4.2 关键设计原则

1. **Provider 层 + fallback 链**：真实数据源统一封装在 `data_sources/`，`composite.py` 组装多源兜底；默认真实源失败即报错，只有显式设置 `FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK=1` 才回退 mock。
2. **SQLite TTL 缓存**：所有外部数据先过 `cache.py`，按数据类型差异化 TTL（行情 15 秒 ~ 持仓 1 天）。
3. **iframe 工具隔离**：四个工具页互不感知，由 shell 统一切换，避免 JS 互相污染。
4. **密钥只在服务端**：AI API Key、飞书 App Secret 全部存 `data/` 运行时文件或环境变量，永不出现在前端和仓库。
5. **官方净值优先**：估值接口先判断官方净值是否已出（当天则直接用官方值），避免"估算覆盖事实"。
6. **ledger 防重发**：飞书通知写入 JSONL ledger，重启/重复扫描不会重复打扰。

### 4.3 一次估值请求的生命周期

```text
GET /api/estimate?code=001438&mode=both
  → 校验 6 位数字代码（INVALID_FUND_CODE）
  → 估算日期：交易日 9:30 前用上一交易日，9:30 起用当天，15:00 后锁定当天
  → 查官方净值日期：
      已是当天 → valuation_status=official_nav，直接返回官方净值
      未更新   → valuation_status=estimated，继续 ↓
  → 取前十大持仓（缓存 1 天）→ 逐只取实时行情（缓存 15 秒）
  → raw：r = Σ weight_i × return_i
  → normalized：r_norm = raw / (可估值权重合计/100)
  → enhanced：r_enh = raw + residual_stock_weight × theme_proxy_return
  → estimated_nav = last_nav × (1 + r)
  → assess_confidence() 生成 confidence(HIGH/MEDIUM/LOW) + notes
  → EstimateResponse{official_nav, estimated_nav, raw/normalized/enhanced, theme_proxy, ...}
```

---

## 5. 后端模块详解

### 5.1 API 层（`api/app.py`，约 630 行）

- **应用工厂 `create_app()`**：创建核心服务（estimator、watchlist、comparison、lof monitor、notice service、对比 AI 网关等）→ 挂载 `/static` → 注册 lifespan → 启动 `LofDailyNoticeScheduler` → 注册 `AppError` 异常处理器（统一返回 `ApiErrorResponse`）。
- **页面路由**：`/`、`/estimate`、`/arbitrage`、`/compare`、`/monitor` 均返回 `index.html`（`/monitor` 为兼容别名）；`/tool/estimate|arbitrage|compare` 分别返回对应工具页，供 iframe 加载。
- **密码注入**：`FUND_ESTIMATOR_COMPARE_AI_PASSWORD`（对比 AI 管理密码）注入对比 AI 网关。
- **调度器接线**：`PUT /api/lof/notice/settings` 更新设置后调用 `lof_notice_scheduler.wake()` 立即重排下一次执行时间。
- 全部 API 端点见 §7。

### 5.2 估值引擎（`services/estimator.py`，约 767 行）

入口流程（L115-161）：校验代码 → 计算估值日期（交易日判断）→ 官方净值可用性分流。

**官方净值检查与基线选择**（L173-207）：官方净值已出时要求上一期净值做基线；未出时用最新净值做估算基准。

**三种口径**（L232-285、L664-727）：

| 口径 | 公式 | 用途 |
|---|---|---|
| `raw` | `r = Σ weight_i × return_i`（披露权重直接加权） | 前十大对净值的直接贡献 |
| `normalized` | `r_norm = raw / (可估值权重合计 / 100)` | 前十归一到 100%，观察重仓组合自身走势 |
| `enhanced` | `r_enh = raw + residual_stock_weight × theme_proxy_return`，其中 `residual_stock_weight = max(股票仓位 − 可估值前十大权重, 0)` | 补上"前十大之外"的股票仓位贡献 |

结果构造（L664-679）：`estimated_nav = last_nav × (1 + return_ratio)`，`estimated_change_pct = return_ratio × 100`。

**其它关键规则**：
- `estimated_nav_date`：交易日 9:30 前用上一交易日，9:30 起用当天，15:00 后保持当天最终估算日期（L730-744；节假日由 `trading_calendar.py` + chinese-calendar 判断）。
- `actual_change_pct` 直接使用东财净值序列官方 `equityReturn` 字段，**不**用两个净值倒推。
- 批量估值 `POST /api/estimate/batch` 并发数由 `FUND_ESTIMATOR_BATCH_CONCURRENCY`（默认 4，范围 1-8）控制；单项失败落为 `BatchEstimateItem.error`，不炸整批。

### 5.3 置信度（`services/confidence.py`）

`assess_confidence(fund_type, holdings_date, top10_weight_sum, usable_weight_sum, missing_quote_count, unmapped_count, ...)` → `(ConfidenceLevel, notes)`：

| 级别 | 条件（全部同时满足） |
|---|---|
| `HIGH` | 类型适合 ∧ 持仓披露 ≤180 天 ∧ top10 ≥60 ∧ 可估值权重 ≥55 ∧ 无缺失行情 ∧ 无未映射持仓 |
| `MEDIUM` | 类型适合 ∧ ≤365 天 ∧ top10 ≥40 ∧ 可估值权重 ≥30 |
| `LOW` | 其余（含债券/货币/QDII/FOF/商品/REIT 等不适合穿透估值的类型） |

notes 恒含免责声明"非官方净值，仅供研究和参考，不构成投资建议"。

### 5.4 主题代理（`services/theme_proxy.py`）

L177-209：根据**基金名称、类型、前十大持仓文本**匹配主题关键词并打分，选择最高分候选作为增强估值的场内代理标的（可实时报价的 ETF）。代理质量可用 `scripts/select_theme_proxy.py` / `check_theme_proxy_weekly.py` 离线验证（见 §11）。页面主展示优先 `enhanced`，无可用代理时退回 `raw`。

### 5.5 数据源层（`data_sources/`）

| 文件 | 职责 |
|---|---|
| `base.py` | provider 抽象接口（基金搜索/净值/持仓/行情等方法签名） |
| `eastmoney.py` | 主源（L121-159 解析基金页 `Data_netWorthTrend` 提取净值/`equityReturn`）：代码搜索、净值序列、F10 前十大持仓、push2 股票实时行情、基金详情（阶段收益/规模/资产配置/经理/费率/同类排名）、场内基金行情、F10 费率页解析 LOF 申赎状态、HaoETF 跨境 LOF 估值页 |
| `sina.py` | A 股行情兜底源（164 行，含 `SINA:shxxxxxx` 形式的代理代码） |
| `mock.py` | 内置演示数据（约 504 行），配合 `FUND_ESTIMATOR_FORCE_MOCK=1`，写独立 mock 库避免污染真实缓存 |
| `composite.py` | 组装主源 + 兜底的 fallback 链，控制 mock 回退行为 |
| `trading_calendar.py` | A 股交易日/节假日判断（chinese-calendar，近期 git 提交引入） |

`http_settings.py` 统一管理 httpx 超时、UA 等全局设置。

### 5.6 缓存层（`services/cache.py`，约 345 行）

SQLite 单文件（默认 `data/fund_estimator.sqlite3`，`FUND_ESTIMATOR_DB` 可改路径），同时承担 TTL 缓存和自选列表持久化。

| 数据 | TTL |
|---|---|
| 基金信息/净值 | 10 分钟 |
| 持仓 | 1 天 |
| 股票实时行情 | 15 秒 |
| LOF 场内行情 | 15 秒 |
| LOF 海外代理行情 | 15 秒 |
| LOF 申购/赎回状态 | 10 分钟 |

mock 模式默认写 `data/fund_estimator.mock.sqlite3`。

### 5.7 自选列表（`services/watchlist.py`）

`WatchlistService` 基于 `SQLiteCache` 的 `list/add/delete/reorder_watchlist(device_id)`。**按浏览器设备 ID 分组，设备之间互不继承**（2026-10-13 定稿）：设备 ID 由前端 localStorage 生成、随 `X-Device-Id` 头传递；`watchlist_devices` 表记录每设备的 `last_active_at`（每次列表操作自动刷新）。每个设备的列表就是它自己增删后剩下的那份（删了就是删了，实时落库）；**新设备 ID 首次出现时不继承任何已有列表（含 `default` 存档），场外自选默认只展示一只候选基金 011370**（常量 `NEW_DEVICE_CANDIDATE_CODE/NAME`，`cache.py` 顶部，用户 2026-10-13 指定），LOF 自选从空开始。`default` 设备为历史合并存档（场外 34 只 + LOF 2 只，2026-10-12 找回，备份 `*.bak-20261012-globalwatchlist`），只读不参与新设备逻辑。用户自己清缓存/换设备后需要恢复时由助手从服务器 DB 手动迁移。每日飞书日报由 scheduler 调 `cache.most_recent_lof_watchlist_device()` 盯"最近活跃设备"的列表（同用户当前正在用的一致）。排序数据与当前列表不一致时抛 `WATCHLIST_REORDER_INVALID`（422）。LOF 自选（`/api/lof/watchlist`）为独立一套，同样机制，模型见 `models/lof.py` 的 `LofWatchlistItem`。

### 5.8 LOF 套利监控（`services/lof.py` + `lof_config.py`）

**关键常量**（L39-54）：`DEFAULT_NORMAL_THRESHOLD_PCT=2.0`、`DEFAULT_STRONG_THRESHOLD_PCT=5.0`、`DEFAULT_MIN_TURNOVER_YUAN=3_000_000`、`TRADING_PAUSED="暂停"`、`MARKET_TZ=Asia/Shanghai`。

**核心跨境 LOF 清单**（`lof_config.py`，24 只，`CoreLof(code, theme, proxies)` + `ProxyLeg(symbol, weight, label)`）：

| 代码 | 主题 | 海外代理（权重） |
|---|---|---|
| 161128 | US technology | NQ=F |
| 161130 / 160213 | Nasdaq 100 | NQ=F |
| 161125 / 050025 | S&P 500 | ES=F |
| 160140 | US REIT | VNQ |
| 164906 | China internet | KWEB 0.7 + ^HSI 0.3 |
| 160644 | 港美互联网 | KWEB 0.6 + NQ=F 0.3 + ^HSI 0.1 |
| 160717 / 161831 / 160924 | H 股指数 | ^HSI |
| 164705 / 164824 | India | ^NSEI |
| 160416 | Oil | CL=F |
| 162411 | Oil and gas | XOP 0.7 + CL=F 0.3 |
| 160216 | Commodity | CL=F 0.6 + GC=F 0.4 |
| 160723 / 161129 / 501018 | Oil | CL=F |
| 501021 / 501025 / 501300 | Hong Kong | ^HSI |
| 501310 | Overseas China | ^HSI 0.5 + KWEB 0.5 |
| 501312 | Overseas technology | NQ=F |

**国内 LOF 代理规则**（`DOMESTIC_LOF_PROXY_RULES`，通过新浪报价的场内 ETF 代理）：科创→sh588000、证券→sh512880、人工智能→sz159819、有色→sh512400、白酒→sh512690、军工→sh512660。`infer_domestic_lof_proxy()` 要求名称/类型含"指数"且非 QDII 才匹配。

**识别辅助**：`LOF_CODE_PREFIXES = ("16","501","502","503","505","506")`；`CLOSED_FUND_KEYWORDS`（定开/封闭/持有期/战略配售）直接排除；`looks_like_lof_fund()` 允许核心清单外但名称/类型含 LOF/QDII/跨境 的基金进入监控。

**数据流水线**（L115-232、L1173-1255）：
- 场内价格：`EastmoneyLofMarketDataSource` 从东财行情列表拉 `latest_price/change_pct/turnover_yuan`，解析 SH/SZ/UNKNOWN 市场。
- 估算净值：海外代理加权变化 → `reference_change` → `estimated_nav = profile.last_nav × (1 + reference_change/100)`（注意 T 日净值与海外时区差异，近期提交 "Estimate domestic LOF T-day NAV" 即此逻辑）。
- 溢价率：`official_premium_pct`（官方净值口径）与 `estimated_premium_pct`（估算净值口径）并存。

**机会分级**（L93-112）：`_classify_signal()` 按阈值划 `neutral/normal/strong`；`_is_trade_leg_paused()` 处理申赎暂停对套利方向的影响。`signal_basis` 标明信号基于 estimated 还是 official。

### 5.9 ETF 监控（`services/etf.py` + `etf_config.py`）

与 LOF 平行的一套跨境 ETF 溢价监控（16 只）：159605/159607/513050（中概，KWEB 0.7+^HSI 0.3）、513330（恒生互联网）、513180/513130（恒生科技，^HSI 0.7+KWEB 0.3）、159941/513100（纳指，NQ=F）、513500（标普，ES=F）、513400（道指，^DJI）、513030/159561（德国，^GDAXI）、513520/513880（日本，^N225）、520580/159822（亚洲新兴，^HSI 0.5+^NSEI 0.5）。`looks_like_cross_border_etf()` 支持关键词启发式扩展。

### 5.10 飞书通知（`services/lof_notifications.py`，约 1391 行）

- **状态与持久化**：`lof_notice_state.json`（App ID/Secret、open_id、tenant_key、设置、防重发日期戳）+ `lof_notice_ledger.jsonl`（发送台账）。目录由 `LOF_NOTICE_DIR` 决定（默认跟随 DB 目录）。
- **接入流程**（L292-445）：`begin_feishu_connect()` 生成 `verification_uri_complete`（页面渲染二维码）→ 手机飞书扫码配置机器人 → `poll_feishu_connect()` 轮询 `device_code` → 成功后保存 `open_id/tenant_key/app_id/app_secret`。另有隐藏回调端点 `GET /api/lof/notice/feishu/callback`。
- **发送**（L1224-1280）：`_get_tenant_access_token()` 调 `/open-apis/auth/v3/tenant_access_token/internal`（带缓存）；`_send_feishu_openapi()` 调 `/open-apis/im/v1/messages`。
- **通知类型**：每日摘要（默认 10:00，`LOF_NOTICE_DAILY_SUMMARY_TIME`）、下午盘检查、新股/新基提醒（IPO reminder）、即时机会通知。
- **即时通知阈值**（L866-910）：`NOTICE_PREMIUM_THRESHOLD_PCT=3.0`（估算溢价 ≥3%）或估算折价达 -5%；明确暂停申购的标的剔除；`_notice_premium_pct()` 优先用 `estimated_premium_pct`。
- **防重发**（L505-591、L564-591）：基于 `MARKET_TZ` 本地时间与 `last_daily_summary_date/last_afternoon_check_date` 等状态戳；`notify_from_scan()` 写 ledger；状态含 `cooldown_count`。

### 5.11 每日摘要调度器（`services/lof_notice_scheduler.py`）

`LofDailyNoticeScheduler`（L21-79）：`start()` 创建 asyncio 循环任务，`stop()` 取消，`wake()` 通过 `asyncio.Event` 唤醒立即重算下次执行时间（设置页保存后触发）；循环按延迟等待并执行 `run_once()`，异常只记日志不崩溃。**当前 Docker 部署由 Web 应用内置此调度器**（`FUND_ESTIMATOR_LOF_NOTICE_SCHEDULER=1`），systemd timer 仅为备用。

### 5.12 LOF Worker CLI（`fund_estimator/lof_worker.py`）

```bash
python -m fund_estimator.lof_worker scan [--notify]        # 扫描机会，--notify 触发飞书即时通知
python -m fund_estimator.lof_worker daily-summary          # 按状态判断执行 摘要/下午检查/新基提醒
python -m fund_estimator.lof_worker send-test              # 测试通知（样例代码 501312）
```

通用参数：`--normal-threshold-pct`、`--strong-threshold-pct`、`--min-turnover-yuan`、`--limit`、`--force`。

### 5.13 基金对比（`services/comparison.py`，约 1429 行）

- **评分策略**（L39-67）：`balanced/aggressive/low_cost` 三种策略，对 performance、ranking、scale、allocation、holdings、manager、similarity 七个维度差异化加权。
- **流水线**（L139-168）：`compare()` 加载候选基金 → 两两相似度 → 构建 score inputs → 生成各基金结果 → 主题分析 → 推荐结论 → 风险提示。
- **相似度**（L250-303）：2-4 只基金 pairwise 计算持仓相似度、资产配置相似度、画像相似度、主题相似度，持仓不可用时降级组合。
- **评分与结果**（L563-585、L717-749）：各维度打分 + `score_breakdown` → 策略权重合成 total score → 生成 snapshot、推荐理由（reasons）、排名（rank）、标记 recommended。
- **差异点**（L893-986）：按近 1 年/6 月/3 月/1 月涨幅 gap 排序，结合持仓重合度、仓位、规模输出差异驱动说明。

### 5.14 AI 模型网关（`services/compare_ai.py`，约 528 行，⚠️ 统筹 session 专属文件）

- **配置存储**（L79-90）：`data/compare_ai_config.json`（base_url、API Key、模型名）、`data/compare_ai_sessions.json`（会话）。API Key **只存服务器本地数据目录**，不回传前端。
- **登录机制**（L102-128）：`FUND_ESTIMATOR_COMPARE_AI_PASSWORD` 校验 → 发 token → `is_authenticated()` 用 token hash + 过期时间验证。不设密码 = 功能整体关闭。
- **模型列表**（L154-177）：`GET {base_url}/models`，保存的 Key 作 Bearer。
- **短评生成**（L179-216、L308-337）：persona（可自定义）system prompt + `CompareResponse` JSON 作 user prompt，调 `/chat/completions`（限定 temperature/max_tokens），要求输出 4 行、120-220 字中文短评。

### 5.15 异常与错误码（`services/exceptions.py`）

`AppError(code, message, status_code, details)`；`DataSourceError` 继承之并默认 502。经 `app.exception_handler(AppError)` 统一转 `ApiErrorResponse` JSON。业务错误码见 §15。

---

## 6. 前端模块详解

### 6.1 工具壳（`web/index.html` + `shell.js` + `shell.css`）

- `index.html`：顶部三标签导航 + 3 个 `data-pane` 区域及对应 iframe。
- `shell.js`：`TOOLS` 配置三个工具（名称/路径/标题）；`toolFromLocation()` 支持 hash/path 直达；`activateTool()` 切换 tab、pane、按需加载 iframe 并更新 URL。
- **PWA**（`manifest.webmanifest`）：名称/图标/主题色/启动页配置，支持 iOS Safari 添加主屏；**刻意不做 service worker**，不缓存实时行情（避免旧行情误导）。

### 6.2 实时估值页（`estimate.html` + `app.js` 957 行 + `styles.css`）

- 初始化生成/读取 `deviceId`（localStorage），维护 `watchlist/estimates/selectedCode` 状态。
- A 股开市期间每 15 秒调 `POST /api/estimate/batch` 自动刷新。
- **"今日上次结果"缓存**（L417-479）：按 `日期+deviceId` 组 key 存当天成功结果，跨天自动失效清理，避免旧交易日数据误当当天。
- 主展示优先 `enhanced` 口径（预计涨跌），无代理时退回 `raw`；同屏展示官方涨跌、前十股票涨跌（normalized）、前十股票占净值比。
- 外链好买基金手机版 `https://m.howbuy.com/fund/{code}/`。

### 6.3 套利提醒页（`arbitrage.html` + `lof_app.js` 970 行 + `lof.css`）

- 默认按 `official_premium_pct` 降序；`showPurchasePaused` 开关控制隐藏明确暂停申购的基金（默认隐藏）。
- 每行两行展示申赎：申购状态+限额+费率 / 赎回状态+费率。
- **飞书接入 UI**（L317-470）：生成二维码（`vendor/qrcode.js`）→ 轮询接入状态 → 断开 → 测试通知；通知设置（时间/开关）保存后后端立即重排调度。

### 6.4 基金对比页（`compare.html` + `compare_app.js` 1025 行 + `compare.css`）

基金选择 → 对比表格（画像/评分/相似度/差异点）→ AI 短评（需先在对比页用管理密码登录并保存模型配置）。

### 6.5 localStorage key 一览

| key（代码常量） | 用途 | 页面 |
|---|---|---|
| `fund_estimator_device_id`（`DEVICE_ID_KEY`） | 浏览器设备标识（服务端已忽略，仅前端缓存分组用） | 全部 |
| `todayEstimateCacheKey(today)` 生成的日期+设备组合 key（app.js L417） | 当日估值结果缓存，跨天自动清理 | 估值 |

---

## 7. API 参考

页面路由（均 `include_in_schema=False`）：`/`、`/estimate`、`/arbitrage`、`/compare`、`/monitor` → `index.html`；`/tool/estimate|arbitrage|compare` → 各工具页。

| 分组 | 端点 | 说明 |
|---|---|---|
| 健康 | `GET /health` | 健康检查 |
|  | `GET /api/source/status` | 当前数据源模式（real/mock） |
| 基础数据 | `GET /api/funds/search?q=` | 代码/名称搜索 |
|  | `GET /api/funds/{code}/nav` | 官方净值序列/画像 |
|  | `GET /api/funds/{code}/holdings` | 前十大持仓 |
| 估值 | `GET /api/estimate?code=&mode=both` | 单基金估值（raw/normalized/enhanced） |
|  | `POST /api/estimate/batch` | 批量估值（失败项落 `error`） |
| 自选 | `GET /api/watchlist` | 列表（全局共享，X-Device-Id 忽略） |
|  | `POST /api/watchlist/{code}` | 添加 |
|  | `DELETE /api/watchlist/{code}` | 删除 |
|  | `PUT /api/watchlist/order` | 排序 |
| LOF | `GET /api/lof/opportunities` | 完整监控列表+机会分级 |
|  | `GET /api/lof/search?q=` | LOF 搜索 |
|  | `GET /api/lof/{code}` | 单只 LOF 详情 |
|  | `GET/POST/DELETE /api/lof/watchlist...`、`PUT /api/lof/watchlist/order` | LOF 自选（独立一套） |
| ETF | `GET /api/etf/opportunities` | 跨境 ETF 监控 |
| LOF 通知 | `GET /api/lof/notice/status` | 通知状态（LofNoticeStatus） |
|  | `PUT /api/lof/notice/settings` | 更新设置（触发调度器 wake） |
|  | `POST /api/lof/notice/feishu/connect` | 发起飞书扫码接入 |
|  | `POST /api/lof/notice/feishu/poll` | 轮询接入结果 |
|  | `GET /api/lof/notice/feishu/callback` | 隐藏回调端点 |
|  | `POST /api/lof/notice/feishu/disconnect` | 断开 |
|  | `POST /api/lof/notice/test` | 测试消息 |
| 对比 | `POST /api/compare` | 2-4 只基金对比 |
| 对比 AI | `GET /api/compare/ai/status` | 网关状态 |
|  | `POST /api/compare/ai/login` | 管理密码登录 |
|  | `PUT /api/compare/ai/config` | 保存模型服务配置 |
|  | `GET /api/compare/ai/models` | 拉取可用模型列表 |
|  | `POST /api/compare/ai/commentary` | 生成 AI 短评 |

估值响应示例字段：`official_nav / official_nav_date / actual_change_pct / estimated_nav / estimated_nav_date / estimated_change_pct / valuation_status(official_nav|estimated) / raw / normalized / enhanced / theme_proxy / top10_weight_sum / confidence`。

---

## 8. 环境变量与配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `FUND_ESTIMATOR_DB` | `data/fund_estimator.sqlite3` | SQLite 路径 |
| `FUND_ESTIMATOR_FORCE_MOCK` | `0` | `1` 强制内置演示数据（写 mock 库） |
| `FUND_ESTIMATOR_ALLOW_MOCK_FALLBACK` | `0` | 真实源失败是否回退 mock |
| `FUND_ESTIMATOR_BACKGROUND_SCAN` | `0` | 后台扫描开关 |
| `FUND_ESTIMATOR_LOF_NOTICE_SCHEDULER` | `1` | Web 内置每日摘要调度器 |
| `FUND_ESTIMATOR_BATCH_CONCURRENCY` | `4` | 批量估值并发（1-8） |
| `FUND_ESTIMATOR_COMPARE_AI_PASSWORD` | 空 | 对比 AI 配置页管理密码；空=关闭 |
| `LOF_NOTICE_DIR` | 跟随 DB 目录 | 通知 state/ledger 目录 |
| `LOF_NOTICE_ENABLED` | `1` | 启用 LOF 飞书通知 |
| `LOF_NOTICE_DAILY_SUMMARY_TIME` | `10:00` | 每日摘要时间（页面设置可覆盖） |
| `LOF_NOTICE_SEND_EMPTY_DAILY_SUMMARY` | `1` | 无机会也发确认摘要 |
| `LOF_FEISHU_TIMEOUT_SECONDS` | `30` | 飞书接口超时 |

---

## 9. 运行时数据与敏感文件

`data/` 目录（gitignore + dockerignore 双重排除）当前含 `fund_estimator.sqlite3` 与 `fund_estimator.mock.sqlite3`。运行时会生成：

| 文件 | 内容 | 敏感 |
|---|---|---|
| `data/fund_estimator.sqlite3` | 缓存 + 自选列表 | 低 |
| `lof_notice_state.json` | 飞书 App ID/Secret、open_id、通知设置 | **高** |
| `lof_notice_ledger.jsonl` | 通知发送台账 | 低 |
| `data/compare_ai_config.json` | 模型服务 base_url、**API Key**、模型名 | **高** |
| `data/compare_ai_sessions.json` | AI 配置页会话 | 中 |
| `data/theme_proxy_weekly/latest.json` | 每周代理复核报告 | 无 |

---

## 10. 测试体系

- **位置**：`tests/`，9 个文件共 **116 个测试**（pytest，`./.venv/bin/python -m pytest`）。
- **全离线**：API 测试通过环境 `FUND_ESTIMATOR_FORCE_MOCK=1` 强制 mock 源；服务层测试注入 `FakeFundSource/FakeHoldingsSource/FakeQuoteSource` 等 fake 对象，**不依赖外部网络**。

| 文件 | 测试数 | 覆盖 |
|---|---|---|
| `test_lof.py` | 55 | LOF 监控/溢价/申赎/通知（最大） |
| `test_compare.py` | 15 | 对比流水线/相似度/评分 |
| `test_api.py` | 14 | API 端点 + 错误路径（FUND_NOT_FOUND 等） |
| `test_estimator.py` | 14 | 估值口径/边界输入 |
| `test_parsers.py` | 8 | 东财页面解析 |
| `test_compare_ai.py` | 4 | 网关配置/登录/短评 |
| `test_confidence.py` | 3 | 置信度分级 |
| `test_cache.py` | 2 | 缓存读写/TTL |
| `test_sina.py` | 1 | 新浪兜底源 |

---

## 11. 运维与研究脚本（`scripts/`）

| 脚本 | 用途 |
|---|---|
| `backtest_estimates.py` | 估值口径误差回测：`python scripts/backtest_estimates.py 011370 --days 20 [--json]`，对比 raw/normalized/enhanced 与官方日涨跌的平均绝对误差（用最近持仓回看，仅作口径研究） |
| `select_theme_proxy.py` | 代理选择：`--theme cpo` 或 `--candidates 159994 515050 ...`，按官方净值日涨跌的 MAE + 相关性给候选 ETF 排序 |
| `check_theme_proxy_weekly.py` | 每周代理复核：`--db --out data/theme_proxy_weekly/latest.json`，新代理最近 20 个重合净值日 MAE 改善 ≥0.15% 且相关性不明显变差才标记"建议切换"（防止代理频繁跳动）；**只写报告不自动切换** |
| `check_session_scope.py` | 多 session 协作边界检查（见 §14） |
| `run_server.py` / `run_linux.sh` | 本机/服务器启动辅助 |
| `start_lof_background.py` | 后台启动 LOF worker 辅助 |

---

## 12. 部署方案

### 12.1 Docker

`Dockerfile`：`python:3.12-slim` → pip 装 requirements → 拷贝代码 → `mkdir /app/data` → CMD `uvicorn fund_estimator.api.app:app --host 0.0.0.0 --port 8000`。

### 12.2 Docker Compose（两套）

| | `docker-compose.quickstart.yml` | `docker-compose.yml`（生产） |
|---|---|---|
| 服务 | `fund-tools` 单容器 | `fund-tools` + `fund-tools-proxy`（nginx:1.27-alpine） |
| 端口 | `8000:8000` 直出 | 宿主 `18367→8443`（TLS） |
| 反代 | 无 | `deploy/nginx/fund-tools.conf`（8443 TLS + HTTP/2 + WebSocket upgrade），证书挂载 `deploy/certs/letsencrypt` |
| 数据卷 | `./data:/app/data` | 同左 |

### 12.3 systemd 单元（`deploy/systemd/`，8 个）

| 单元 | 职责 |
|---|---|
| `fund-estimator.service` | 裸机 venv 直跑 FastAPI（不依赖 Docker 的方案） |
| `fund-tools-compose.service` | **服务器重启后自动拉起 Compose**（推荐随生产 Compose 一起安装） |
| `fund-estimator-lof-worker.service` + `.timer` | 每分钟扫描 + 即时通知（备用，需要实时盯盘再启用） |
| `fund-estimator-lof-daily-notice.service` + `.timer` | 每日摘要（不用 Web 内置调度器时的备用方案） |
| `fund-certbot-renew.service` + `.timer` | 证书定时续期（配合 `deploy/certbot-renew.sh`） |

### 12.4 Nginx

- `fund-estimator.conf`：HTTP 80 → `127.0.0.1:8000`（裸机/systemd 场景）。
- `fund-tools.conf`：HTTPS 8443（TLS + HTTP/2 + SSL session）→ 容器 `fund-tools:8000`。

### 12.5 生产要点

- 手机访问必须监听 `0.0.0.0`，走服务器 IP/域名/反代，不要绑 `127.0.0.1`。
- 证书、AI 配置、自选数据都在 `data/` 与 `deploy/certs/`，重启容器不丢。
- 服务器部署历史：`sudo cp deploy/systemd/fund-tools-compose.service /etc/systemd/system/ && systemctl enable --now fund-tools-compose`。

---

## 13. 安全约定（SECURITY.md）

**绝不入库**：Cloudflare/GitHub token、SSH 私钥、OpenAI 兼容 API Key、飞书 App ID/Secret、tenant_access_token、open_id、`data/` 全部运行时文件、证书、`.env`。

`.gitignore`/`.dockerignore` 已显式排除：`data/`、`*.sqlite3`、`.env*`（保留 example）、`*.pem/*.key`、`compare_ai_config.json`、`compare_ai_sessions.json`、`lof_notice_state.json`、`lof_notice_ledger.jsonl`、`cloudflare_token.txt` 等。

提交前扫描：

```bash
git status --short
git grep -n -I -E 'cfut_|ghp_|github_pat_|AKIA|AIza|xox[baprs]-|sk-[A-Za-z0-9_-]{12,}|tenant_access_token|app_secret'
```

误提交密钥 = 立即平台撤销重发；删文件不等于密钥安全。License 为 **Non-Commercial Personal Research License**（禁止商用）。

---

## 14. 多 Session 协作约定（协作说明.md）

本项目曾用多个 Codex session 并行开发，靠 `scripts/check_session_scope.py` 约束修改边界（新 session 接手前必读）：

- **动工前**：`./.venv/bin/python scripts/check_session_scope.py <role> --preflight` —— 打印允许修改范围，且要求工作区**无未提交改动**（有则先不要开工）。
- **收尾前**：`./.venv/bin/python scripts/check_session_scope.py <role>` —— 检查改动是否越界；越界必须自己撤回。

| Role | 允许范围 |
|---|---|
| `orchestrator` | README、协作说明、Docker/compose、`.env.example`、`deploy/**`、`api/app.py`、`web/index.html`、`shell.js/css`、`manifest`、`services/compare_ai.py`、`check_session_scope.py` |
| `estimate` | `estimate.html/app.js/styles.css` + 估值相关 API/模型/服务/测试 |
| `arbitrage` | `arbitrage.html/lof_app.js/lof.css`、`vendor/qrcode.js` + LOF/ETF 相关文件 |
| `compare` | `compare.html/compare_app.js/compare.css` + 对比相关文件 |

> 原 `assistant`（AI 咨询）scope 已随工具整体移除而删除。

**只有 orchestrator 可改**：README、协作说明、Docker 全家桶、`deploy/**`、`api/app.py`、shell 三件套、manifest、scope 脚本、`compare_ai.py`。改共享文件（如 `cache.py`、`schema.py`）需先说明理由并确认不破坏其它工具。

**新增工具模式**：新页面 + 新 JS/CSS → `index.html` 加 tab + iframe pane → `shell.js` TOOLS 加配置 → `app.py` 加 `/new-tool` 和 `/tool/new-tool` 路由 → 不把逻辑塞进已有工具 JS。

**每次修改后至少验证**：`node --check` 全部改动 JS + `./.venv/bin/python -m pytest`；页面验收访问 `/ /estimate /arbitrage /compare` 四个地址。

---

## 15. 错误码参考

| 错误码 | 场景 | HTTP |
|---|---|---|
| `INVALID_FUND_CODE` | 基金代码非 6 位数字 | 422 |
| `FUND_NOT_FOUND` | 基金代码不存在 | 404 |
| `HOLDINGS_NOT_AVAILABLE` | 无可用持仓数据 | 404 |
| `QUOTE_FETCH_FAILED` | 实时行情获取失败 | 502 |
| `UNSUPPORTED_FUND_TYPE` | 持仓资产无法映射到行情代码 | 422 |
| `WATCHLIST_REORDER_INVALID` | 自选排序数据与列表不一致 | 422 |
| `DataSourceError` 系 | 数据源层面失败 | 502（默认） |

---

## 16. 常用命令速查

```bash
# 本机开发
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m uvicorn fund_estimator.api.app:app --reload --host 127.0.0.1 --port 8000
FUND_ESTIMATOR_FORCE_MOCK=1 ./.venv/bin/python -m uvicorn fund_estimator.api.app:app ...   # 离线演示

# 测试
./.venv/bin/python -m pytest
node --check fund_estimator/web/app.js   # 前端语法检查（改 JS 后必跑）

# LOF worker（服务器上）
python -m fund_estimator.lof_worker scan --notify
python -m fund_estimator.lof_worker daily-summary
python -m fund_estimator.lof_worker send-test

# 研究
python scripts/backtest_estimates.py 011370 --days 20
python scripts/select_theme_proxy.py 011370 --theme cpo --days 20
python scripts/check_theme_proxy_weekly.py --db data/fund_estimator.sqlite3 --out data/theme_proxy_weekly/latest.json

# 部署
docker compose -f docker-compose.quickstart.yml up -d --build
docker compose up -d --build                                  # 生产（需证书）
ssh root@217.142.226.158 "cd /opt/fund-estimator && git status --short && docker ps"
```

---

## 17. 移交备注（重要）

1. **工作区安排（2026-10-13 定稿）**：整个项目由此工作区独立维护，唯一权威代码目录是 `worktree/`（远端 `origin = github.com/wan7up/fund-estimator`，默认分支 `main`，另有本地分支 `codex/trading-calendar-deps`）。Codex 侧旧目录 `fund-valuation-docs` 留在原处、不再使用也不做参考；本工作区根目录只保留 `worktree/`，所有说明文档（本文档）随仓库放 GitHub。迁移残留（外层空壳 `.git`、`.agents/`、`.codex/`）已于 2026-10-12 清理。
2. **tmp_patch/ 已删除**（2026-10-12 整理）：原为 3 个已合入主线的补丁（same-day cache、batch 速度优化、CPO 主题代理）+ 2 个 json 产物，仅作过历史参考，不作为部署源。
3. **AI 咨询工具已移除**（2026-10-12，应用户要求）：独立 AI 咨询页、`/api/ai-chat/*` 路由、模型/服务层及 compare_ai 网关中仅供该页使用的 `stream_chat_completion`/`transcribe_audio` 已整体删除；基金对比页的 AI 短评保留。提交 `3b79462`。
4. **近期开发方向**（git log）：A 股节假日历法适配（chinese-calendar）、LOF T 日净值估算、国内 LOF 折价预警、批量估值提速、当日缓存展示、AI 咨询工具移除。
5. **协作机制仍适用**：即使不再用 Codex，`check_session_scope.py` 的边界思想（估值/套利/对比三个功能面隔离、共享文件须统筹确认）仍应遵守。
6. **数据纪律**：本机 `data/` 里的 SQLite 是历史运行数据；线上通知以服务器 state/ledger 为准，排查问题先查服务器，不要读本机 `data/`。
7. **服务器信息**：线上 `https://fund.mbl.dpdns.org`，服务器目录 `/opt/fund-estimator`，SSH `root@217.142.226.158`（IP 若变更以实际 SSH 配置为准；本机 SSH 配置别名 `oracle-217`，私钥放用户级目录、不放在本项目目录）；密钥/证书一律不入聊天、不入仓库。域名失效后 IP 后路：`https://217.142.226.158:18367`（证书警告点继续）。

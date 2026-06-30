const DEVICE_ID_KEY = "fund_estimator_device_id";
const DAILY_ESTIMATE_CACHE_PREFIX = "fund_estimator_daily_estimates_v1";

function createDeviceId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function getDeviceId() {
  try {
    let deviceId = window.localStorage.getItem(DEVICE_ID_KEY);
    if (!deviceId) {
      deviceId = createDeviceId();
      window.localStorage.setItem(DEVICE_ID_KEY, deviceId);
    }
    return deviceId;
  } catch {
    return "default";
  }
}

const state = {
  watchlist: [],
  estimates: new Map(),
  selectedCode: null,
  refreshInFlight: false,
  expandedHoldings: new Set(),
  deviceId: getDeviceId(),
  recentlyAddedCode: null,
  recentlyAddedTimer: null,
  addingCode: null,
};

const els = {
  searchForm: document.querySelector("#searchForm"),
  searchInput: document.querySelector("#searchInput"),
  searchResults: document.querySelector("#searchResults"),
  addTypedBtn: document.querySelector("#addTypedBtn"),
  refreshBtn: document.querySelector("#refreshBtn"),
  watchRows: document.querySelector("#watchRows"),
  mobileCards: document.querySelector("#mobileCards"),
  statusText: document.querySelector("#statusText"),
  sourceBadge: document.querySelector("#sourceBadge"),
  detailTitle: document.querySelector("#detailTitle"),
  detailMeta: document.querySelector("#detailMeta"),
  summaryStrip: document.querySelector("#summaryStrip"),
  externalLinksPanel: document.querySelector("#externalLinksPanel"),
  holdingsList: document.querySelector("#holdingsList"),
  notesList: document.querySelector("#notesList"),
};

function clsForPct(value) {
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "flat";
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${fmt(value, 2)}%`;
}

function isOfficial(est) {
  return est?.valuation_status === "official_nav" || est?.is_official_nav;
}

function isUnavailable(est) {
  return est?.valuation_status === "unavailable" || est?.is_unavailable;
}

function isProxyEstimate(est) {
  return est?.raw?.method === "proxy_exchange_traded_fund_return" || String(est?.data_source || "").includes("proxy_quote");
}

function hasModelEstimate(est) {
  return est?.estimated_nav !== null && est?.estimated_nav !== undefined && est?.estimated_change_pct !== null && est?.estimated_change_pct !== undefined;
}

function officialNav(est) {
  return est.official_nav ?? est.last_nav;
}

function officialNavDate(est) {
  return est.official_nav_date ?? est.nav_date;
}

function estimatedNavDate(est) {
  return est.estimated_nav_date || (est.estimate_time ? est.estimate_time.slice(0, 10) : null);
}

function estimatedNavText(est) {
  if (isUnavailable(est)) return "本基金无法预计";
  return isOfficial(est) && !hasModelEstimate(est) ? "--" : fmt(est.estimated_nav, 4);
}

function estimatedDateText(est) {
  if (isUnavailable(est)) return officialNavDate(est) || "--";
  if (isOfficial(est)) return est.estimated_nav_date || officialNavDate(est);
  return estimatedNavDate(est) || officialNavDate(est);
}

function estimateChangeText(est) {
  if (isUnavailable(est)) return "--";
  return isOfficial(est) && !hasModelEstimate(est) ? "--" : fmtPct(est.estimated_change_pct);
}

function actualChangeText(est) {
  return fmtPct(est.actual_change_pct);
}

function actualDateText(est) {
  return est.actual_change_date || officialNavDate(est);
}

function top10MovePct(est) {
  if (isProxyEstimate(est)) return est.raw ? est.raw.estimated_change_pct : null;
  return est.normalized ? est.normalized.estimated_change_pct : null;
}

function moveLabel(est) {
  if (isProxyEstimate(est)) return "代理走势";
  return "前十股票涨跌";
}

function coverageLabel(est) {
  if (isProxyEstimate(est)) return "代理标的";
  return "前十股票占净值比";
}

function coverageText(est) {
  if (isProxyEstimate(est)) {
    const proxy = est.holdings && est.holdings[0];
    return proxy ? proxy.stock_code : "--";
  }
  return est.top10_weight_sum ? `${fmt(est.top10_weight_sum, 2)}%` : "--";
}

function itemStateClass(code) {
  const classes = [];
  if (state.selectedCode === code) classes.push("selected");
  if (state.recentlyAddedCode === code) classes.push("recently-added");
  return classes.join(" ");
}

function markRecentlyAdded(code) {
  state.recentlyAddedCode = code;
  if (state.recentlyAddedTimer) clearTimeout(state.recentlyAddedTimer);
  state.recentlyAddedTimer = setTimeout(() => {
    if (state.recentlyAddedCode === code) {
      state.recentlyAddedCode = null;
      renderRows();
    }
  }, 2000);
}

function scrollToFundCard(code) {
  if (!window.matchMedia("(max-width: 960px)").matches) return;
  window.requestAnimationFrame(() => {
    const node = document.querySelector(`[data-code="${code}"]`);
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function estimateMethodText(est) {
  if (isProxyEstimate(est)) return "按场内代理走势";
  if (est?.primary_mode === "enhanced") return "按关联板块修正";
  if (est?.primary_mode === "asset_scaled") return "按股票仓位修正";
  if (est?.primary_mode === "normalized") return "按前十股票走势";
  return "按披露占比估算";
}

function unavailableEstimate(item, result) {
  const profile = result?.profile || {};
  const error = result?.error || {};
  return {
    fund_code: profile.code || item.code,
    fund_name: profile.name || item.name || item.code,
    fund_type: profile.fund_type || null,
    fund_details: profile.details || {},
    official_nav: profile.last_nav,
    official_nav_date: profile.nav_date,
    nav_date: profile.nav_date,
    actual_change_pct: profile.actual_change_pct,
    actual_change_date: profile.nav_date,
    valuation_status: "unavailable",
    is_unavailable: true,
    estimated_nav: null,
    estimated_nav_date: null,
    estimated_change_pct: null,
    confidence: "low",
    top10_weight_sum: 0,
    raw: null,
    normalized: null,
    enhanced: null,
    theme_proxy: null,
    holdings: [],
    notes: [error.message || "本基金暂时无法预计"],
    warnings: [],
  };
}

function confidenceText(value) {
  if (value === "high") return "高";
  if (value === "medium") return "中";
  if (value === "low") return "低";
  return "--";
}

function providerText(provider) {
  const names = {
    "eastmoney+sina": "东方财富 / 新浪",
    eastmoney: "东方财富",
    sina: "新浪",
    mock: "演示数据",
  };
  return names[provider] || provider || "未知";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function howbuyFundUrl(code) {
  return `https://m.howbuy.com/fund/${encodeURIComponent(code)}/`;
}

function externalLinkHtml(code, compact = false) {
  const cls = compact ? "external-link compact-link" : "external-link";
  return `
    <a class="${cls}" href="${howbuyFundUrl(code)}">查看基金详情</a>`;
}

function externalDetailLinksHtml(code) {
  return `
    <div class="external-detail">
      <div class="card-actions detail-action">${externalLinkHtml(code)}</div>
    </div>`;
}

function orderButtonsHtml(code) {
  const index = state.watchlist.findIndex((item) => item.code === code);
  const upDisabled = index <= 0 ? " disabled" : "";
  const downDisabled = index < 0 || index >= state.watchlist.length - 1 ? " disabled" : "";
  return `
    <div class="order-buttons" aria-label="调整顺序">
      <button class="order-btn" data-move="${code}" data-direction="up" type="button" title="上移"${upDisabled}>↑</button>
      <button class="order-btn" data-move="${code}" data-direction="down" type="button" title="下移"${downDisabled}>↓</button>
    </div>`;
}

function cardHeadActionsHtml(code) {
  return `
    <div class="fund-card-actions">
      ${orderButtonsHtml(code)}
      <button class="delete-btn" data-delete="${code}" title="删除">×</button>
    </div>`;
}

function holdingsToggleHtml(est, code, estimateDate, holdingMove) {
  const expanded = state.expandedHoldings.has(code);
  const disabled = !est.holdings || !est.holdings.length;
  const disabledAttr = disabled ? " disabled" : "";
  const expandedText = expanded ? "收起明细∧" : "展开明细∨";
  return `
    <button class="fund-card-row holdings-toggle" data-toggle-holdings="${code}" type="button" aria-expanded="${expanded}"${disabledAttr}>
      <span>
        <span>${moveLabel(est)} · ${estimateDate}</span>
        <small>${disabled ? "暂无明细" : expandedText}</small>
      </span>
      <strong class="${clsForPct(holdingMove || 0)}">${holdingMove === null ? "--" : fmtPct(holdingMove)}</strong>
    </button>
    ${expanded ? holdingsContributionHtml(est) : ""}`;
}

function holdingsContributionHtml(est) {
  if (!est.holdings || !est.holdings.length) {
    return `<div class="holding-contrib-list"><div class="empty compact">暂无持仓明细</div></div>`;
  }
  return `
    <div class="holding-contrib-list">
      ${est.holdings
        .map((holding) => {
          const pctClass = clsForPct(holding.change_pct || 0);
          const contributionClass = clsForPct(holding.contribution_pct || 0);
          const warning = holding.warning ? `<small>${escapeHtml(holding.warning)}</small>` : "";
          return `
            <div class="holding-contrib-item">
              <div>
                <strong>${escapeHtml(holding.stock_name)}</strong>
                <small>${holding.stock_code} · ${fmt(holding.weight_pct, 2)}%</small>
                ${warning}
              </div>
              <div>
                <span>涨跌</span>
                <strong class="${pctClass}">${fmtPct(holding.change_pct)}</strong>
              </div>
              <div>
                <span>贡献</span>
                <strong class="${contributionClass}">${fmtPct(holding.contribution_pct)}</strong>
              </div>
            </div>`;
        })
        .join("")}
    </div>`;
}

function themeProxyRowHtml(est) {
  const proxy = est?.theme_proxy;
  if (!proxy) return "";
  const pctClass = clsForPct(proxy.change_pct || 0);
  return `
    <div class="fund-card-row theme-proxy-row">
      <span>关联板块 · ${escapeHtml(proxy.theme)}</span>
      <strong class="${pctClass}">${fmtPct(proxy.change_pct)}</strong>
      <small>${escapeHtml(proxy.proxy_name || proxy.proxy_code)} · 代理${fmt(proxy.weight_pct, 2)}%</small>
    </div>`;
}

function fundBasicHtml(est) {
  const actualDate = actualDateText(est);
  return `
    <div class="fund-basic">
      <div>
        <span>官方净值</span>
        <strong>${fmt(officialNav(est), 4)}</strong>
        <small>${officialNavDate(est)}</small>
      </div>
      <div>
        <span>官方涨跌</span>
        <strong class="${clsForPct(est.actual_change_pct || 0)}">${actualChangeText(est)}</strong>
        <small>${actualDate}</small>
      </div>
    </div>`;
}

function estimateBoxesHtml(est) {
  const official = isOfficial(est);
  const unavailable = isUnavailable(est);
  const estimateDate = estimatedDateText(est);
  const boxClass = unavailable ? "estimate-box unavailable-estimate" : official ? "estimate-box comparison-estimate" : "estimate-box";
  const valueClass = unavailable || official ? "comparison-value" : clsForPct(est.estimated_change_pct || 0);
  const hint = unavailable
    ? "<small class=\"estimate-hint\">缺少可用持仓或代理行情</small>"
    : official
      ? "<small class=\"estimate-hint\">官方已更新</small>"
      : `<small class="estimate-hint">${estimateMethodText(est)}</small>`;
  const navLabel = "预估净值";
  const changeLabel = "预估涨跌";
  return `
    <div class="estimate-boxes">
      <div class="${boxClass}">
        <span>${navLabel}</span>
        <strong class="${valueClass}">${estimatedNavText(est)}</strong>
        <small class="estimate-date">${estimateDate || "--"}</small>
        ${hint}
      </div>
      <div class="${boxClass}">
        <span>${changeLabel}</span>
        <strong class="${valueClass}">${estimateChangeText(est)}</strong>
        <small class="estimate-date">${estimateDate || "--"}</small>
        ${hint}
      </div>
    </div>`;
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Device-Id": state.deviceId,
    ...(options.headers || {}),
  };
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = data.error || { code: response.status, message: response.statusText };
    throw new Error(`${err.code}: ${err.message}`);
  }
  return data;
}

async function loadSourceStatus() {
  try {
    const status = await api("/api/source/status");
    const modeText = status.mode === "real" ? "数据来源：" : "演示数据：";
    els.sourceBadge.textContent = `${modeText}${providerText(status.provider)}`;
    els.sourceBadge.title = status.mock_fallback_enabled ? "演示数据兜底已开启" : "";
    els.sourceBadge.className = status.mode;
  } catch (error) {
    els.sourceBadge.textContent = "数据源状态未知";
  }
}

async function loadWatchlist() {
  state.watchlist = await api("/api/watchlist");
  const restored = restoreTodayEstimateCache();
  if (!restored) renderRows();
  await refreshEstimates({ background: restored });
}

function todayEstimateCacheKey(today = localDateText()) {
  return `${DAILY_ESTIMATE_CACHE_PREFIX}:${state.deviceId}:${today}`;
}

function pruneOldEstimateCaches(today = localDateText()) {
  try {
    const keepKey = todayEstimateCacheKey(today);
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (key && key.startsWith(`${DAILY_ESTIMATE_CACHE_PREFIX}:`) && key !== keepKey) {
        window.localStorage.removeItem(key);
      }
    }
  } catch {
    // localStorage may be disabled in private browsing; live refresh still works.
  }
}

function restoreTodayEstimateCache() {
  if (!state.watchlist.length) return false;
  const today = localDateText();
  pruneOldEstimateCaches(today);
  try {
    const raw = window.localStorage.getItem(todayEstimateCacheKey(today));
    if (!raw) return false;
    const payload = JSON.parse(raw);
    if (payload?.date !== today || !Array.isArray(payload.results)) return false;
    const watchCodes = new Set(state.watchlist.map((item) => item.code));
    const nextEstimates = new Map();
    for (const item of payload.results) {
      if (item?.code && watchCodes.has(item.code)) {
        nextEstimates.set(item.code, item);
      }
    }
    if (!nextEstimates.size) return false;
    state.estimates = nextEstimates;
    const codes = state.watchlist.map((item) => item.code);
    if (!state.selectedCode || !codes.includes(state.selectedCode)) {
      state.selectedCode = codes[0];
    }
    renderRows();
    renderDetail(state.selectedCode);
    els.statusText.textContent = `显示今日上次结果 · 后台刷新中`;
    return true;
  } catch {
    return false;
  }
}

function saveTodayEstimateCache(results) {
  try {
    const today = localDateText();
    const payload = {
      date: today,
      savedAt: new Date().toISOString(),
      results,
    };
    window.localStorage.setItem(todayEstimateCacheKey(today), JSON.stringify(payload));
    pruneOldEstimateCaches(today);
  } catch {
    // Cache is only a display accelerator; ignore storage failures.
  }
}

async function refreshEstimates(options = {}) {
  const background = Boolean(options.background);
  if (state.refreshInFlight) return;
  if (!state.watchlist.length) {
    els.statusText.textContent = "暂无自选";
    renderEmptyDetail();
    return;
  }
  state.refreshInFlight = true;
  els.statusText.textContent = background ? "显示今日上次结果 · 后台刷新中" : "刷新中";
  try {
    const codes = state.watchlist.map((item) => item.code);
    const results = await api("/api/estimate/batch", {
      method: "POST",
      body: JSON.stringify({ codes, mode: "both" }),
    });
    saveTodayEstimateCache(results);
    state.estimates.clear();
    for (const item of results) {
      state.estimates.set(item.code, item);
    }
    if (!state.selectedCode || !codes.includes(state.selectedCode)) {
      state.selectedCode = codes[0];
    }
    renderRows();
    renderDetail(state.selectedCode);
    const refreshHint = shouldAutoRefresh() ? " · 开市中自动刷新" : "";
    els.statusText.textContent = `已更新 ${new Date().toLocaleTimeString()}${refreshHint}`;
  } finally {
    state.refreshInFlight = false;
  }
}

function renderRows() {
  if (!state.watchlist.length) {
    els.watchRows.innerHTML = `<tr><td colspan="8" class="empty">搜索基金代码并加入自选后开始估值</td></tr>`;
    els.mobileCards.innerHTML = `<div class="empty">搜索基金代码并加入自选后开始估值</div>`;
    return;
  }

  els.watchRows.innerHTML = state.watchlist
    .map((item) => {
      const result = state.estimates.get(item.code);
      if (!result) {
        return rowShell(item, `<td colspan="6" class="flat">等待估值</td>`);
      }
      if (!result.ok) {
        const est = unavailableEstimate(item, result);
        const estimateDate = estimatedDateText(est);
        const actualDate = actualDateText(est);
        return `
        <tr data-code="${item.code}" class="${itemStateClass(item.code)}">
          <td>
            <div class="fund-name">
              <strong>${escapeHtml(est.fund_name)}</strong>
              <small>${est.fund_code} · ${escapeHtml(est.fund_type || "--")}</small>
            </div>
          </td>
          <td>${fmt(officialNav(est), 4)}<br><small>${officialNavDate(est) || "--"}</small></td>
          <td class="flat">${estimatedNavText(est)}<br><small>${estimateDate}</small></td>
          <td class="flat">${estimateChangeText(est)}<br><small>${estimateDate}</small></td>
          <td class="${clsForPct(est.actual_change_pct || 0)}">${actualChangeText(est)}<br><small>${actualDate || "--"}</small></td>
          <td class="flat">--<br><small>${estimateDate}</small></td>
          <td>--<br><small>占基金净值</small></td>
          <td class="row-actions">${externalLinkHtml(est.fund_code, true)}<button class="delete-btn" data-delete="${item.code}" title="删除">×</button></td>
        </tr>`;
      }
      const est = result.estimate;
      const official = isOfficial(est);
      const holdingMove = top10MovePct(est);
      const estimateDate = estimatedDateText(est);
      const actualDate = actualDateText(est);
      return `
        <tr data-code="${item.code}" class="${itemStateClass(item.code)}">
          <td>
            <div class="fund-name">
              <strong>${escapeHtml(est.fund_name)}</strong>
              <small>${est.fund_code} · ${escapeHtml(est.fund_type || "--")}</small>
            </div>
          </td>
          <td>${fmt(officialNav(est), 4)}<br><small>${officialNavDate(est)}</small></td>
          <td>${estimatedNavText(est)}<br><small>${estimateDate}</small></td>
          <td class="${official ? "flat" : clsForPct(est.estimated_change_pct)}">${estimateChangeText(est)}<br><small>${estimateDate}</small></td>
          <td class="${clsForPct(est.actual_change_pct || 0)}">${actualChangeText(est)}<br><small>${actualDate}</small></td>
          <td class="${clsForPct(holdingMove || 0)}">${holdingMove === null ? "--" : fmtPct(holdingMove)}<br><small>${estimateDate}</small></td>
          <td>${coverageText(est)}<br><small>${isProxyEstimate(est) ? "场内代理" : "占基金净值"}</small></td>
          <td class="row-actions">${externalLinkHtml(est.fund_code, true)}<button class="delete-btn" data-delete="${item.code}" title="删除">×</button></td>
        </tr>`;
    })
    .join("");
  els.mobileCards.innerHTML = state.watchlist.map(renderCard).join("");
}

function rowShell(item, cells) {
  return `
    <tr data-code="${item.code}" class="${itemStateClass(item.code)}">
      <td><div class="fund-name"><strong>${escapeHtml(item.name || item.code)}</strong><small>${item.code}</small></div></td>
      ${cells}
      <td class="row-actions">${externalLinkHtml(item.code, true)}<button class="delete-btn" data-delete="${item.code}" title="删除">×</button></td>
    </tr>`;
}

function renderCard(item) {
  const result = state.estimates.get(item.code);
  if (!result) {
    return `
      <article class="fund-card ${itemStateClass(item.code)}" data-code="${item.code}">
        <div class="fund-card-head">
        <div class="fund-card-title"><strong>${escapeHtml(item.name || item.code)}</strong><small>${item.code}</small></div>
          ${cardHeadActionsHtml(item.code)}
        </div>
        <div class="flat">等待估值</div>
        ${externalDetailLinksHtml(item.code)}
      </article>`;
  }
  if (!result.ok) {
    const est = unavailableEstimate(item, result);
    const estimateDate = estimatedDateText(est);
    const holdingMove = top10MovePct(est);
    return `
      <article class="fund-card ${itemStateClass(item.code)}" data-code="${item.code}">
        <div class="fund-card-head">
          <div class="fund-card-title"><strong>${escapeHtml(est.fund_name)}</strong><small>${est.fund_code} · ${escapeHtml(est.fund_type || "--")}</small></div>
          ${cardHeadActionsHtml(item.code)}
        </div>
        ${fundBasicHtml(est)}
        ${estimateBoxesHtml(est)}
        ${holdingsToggleHtml(est, est.fund_code, estimateDate, holdingMove)}
        ${themeProxyRowHtml(est)}
        <div class="fund-card-row">
          <span>${coverageLabel(est)}</span>
          <strong>--</strong>
        </div>
        ${externalDetailLinksHtml(est.fund_code)}
      </article>`;
  }

  const est = result.estimate;
  const official = isOfficial(est);
  const estimateDate = estimatedDateText(est);
  const holdingMove = top10MovePct(est);
  return `
    <article class="fund-card ${itemStateClass(item.code)}" data-code="${item.code}">
      <div class="fund-card-head">
        <div class="fund-card-title">
          <strong>${escapeHtml(est.fund_name)}</strong>
          <small>${est.fund_code} · ${escapeHtml(est.fund_type || "--")}</small>
        </div>
        ${cardHeadActionsHtml(item.code)}
      </div>
      ${fundBasicHtml(est)}
      ${estimateBoxesHtml(est)}
      ${holdingsToggleHtml(est, est.fund_code, estimateDate, holdingMove)}
      ${themeProxyRowHtml(est)}
      <div class="fund-card-row">
        <span>${coverageLabel(est)}</span>
        <strong>${coverageText(est)}</strong>
      </div>
      ${externalDetailLinksHtml(est.fund_code)}
    </article>`;
}

function renderDetail(code) {
  const result = state.estimates.get(code);
  if (!result) {
    renderEmptyDetail();
    return;
  }
  if (!result.ok) {
    const item = state.watchlist.find((entry) => entry.code === code) || { code };
    const est = unavailableEstimate(item, result);
    const estimateDate = estimatedDateText(est);
    els.detailTitle.textContent = est.fund_name;
    els.detailMeta.textContent = `${est.fund_code} · 暂无预估值`;
    els.summaryStrip.innerHTML = `
      ${fundBasicHtml(est)}
      ${estimateBoxesHtml(est)}
      <div class="fund-basic compact-basic">
        <div>
          <span>${moveLabel(est)}</span>
          <strong class="flat">--</strong>
          <small>${estimateDate}</small>
        </div>
        <div>
          <span>${coverageLabel(est)}</span>
          <strong>--</strong>
        </div>
      </div>
    `;
    els.externalLinksPanel.innerHTML = externalLinkHtml(est.fund_code);
    els.holdingsList.innerHTML = `<div class="empty">暂无可用持仓或代理行情。</div>`;
    els.notesList.innerHTML = est.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("");
    return;
  }

  const est = result.estimate;
  const official = isOfficial(est);
  const estimateDate = estimatedDateText(est);
  const holdingMove = top10MovePct(est);
  els.detailTitle.textContent = est.fund_name;
  els.detailMeta.textContent = official
    ? `${est.fund_code} · 官方净值 ${officialNavDate(est)}`
    : `${est.fund_code} · 预估 ${estimateDate} · 持仓 ${est.holdings_date}`;
  els.summaryStrip.innerHTML = `
    ${fundBasicHtml(est)}
    ${estimateBoxesHtml(est)}
    ${themeProxyRowHtml(est)}
    <div class="fund-basic compact-basic">
      <div>
        <span>${moveLabel(est)}</span>
        <strong class="${clsForPct(holdingMove || 0)}">${holdingMove === null ? "--" : fmtPct(holdingMove)}</strong>
        <small>${estimateDate}</small>
      </div>
      <div>
        <span>${coverageLabel(est)}</span>
        <strong>${coverageText(est)}</strong>
      </div>
    </div>
  `;
  els.externalLinksPanel.innerHTML = externalLinkHtml(est.fund_code);
  els.holdingsList.innerHTML = !est.holdings.length
    ? `<div class="empty">当天官方净值已披露，暂无预估对比明细。</div>`
    : est.holdings
        .map((holding) => {
          const pctClass = clsForPct(holding.change_pct || 0);
          return `
            <div class="holding">
              <div><strong>${escapeHtml(holding.stock_name)}</strong><br><small>${holding.stock_code} · ${holding.weight_pct}%</small></div>
              <div>${fmt(holding.latest_price, 2)}</div>
              <div class="${pctClass}">${fmtPct(holding.change_pct)}</div>
              <div class="${clsForPct(holding.contribution_pct || 0)}">${fmtPct(holding.contribution_pct)}</div>
            </div>`;
        })
        .join("");
  els.notesList.innerHTML = [...est.notes, ...est.warnings].map((note) => `<li>${escapeHtml(note)}</li>`).join("");
}

function renderEmptyDetail() {
  els.detailTitle.textContent = "估值详情";
  els.detailMeta.textContent = "";
  els.summaryStrip.innerHTML = `<div class="empty">暂无估值结果</div>`;
  els.externalLinksPanel.innerHTML = "";
  els.holdingsList.innerHTML = "";
  els.notesList.innerHTML = "";
}

async function runSearch(query) {
  const q = query.trim();
  if (!q) {
    els.searchResults.classList.remove("active");
    return;
  }
  const results = await api(`/api/funds/search?q=${encodeURIComponent(q)}`);
  els.searchResults.innerHTML = results.length
    ? results
        .map(
          (item) => `
            <div class="search-result" data-add="${item.code}">
              <span>${escapeHtml(item.name)}</span>
              <strong>${item.code}</strong>
            </div>`
        )
        .join("")
    : `<div class="empty">未找到匹配基金</div>`;
  els.searchResults.classList.add("active");
}

async function addCode(code) {
  const normalized = code.trim();
  if (!/^\d{6}$/.test(normalized)) {
    els.statusText.textContent = "请输入6位基金代码";
    return;
  }
  if (state.addingCode) return;
  const alreadyExists = state.watchlist.some((item) => item.code === normalized);
  state.addingCode = normalized;
  els.statusText.textContent = alreadyExists ? "正在移到顶部..." : "正在加入...";
  try {
    const added = await api(`/api/watchlist/${normalized}`, { method: "POST" });
    els.searchResults.classList.remove("active");
    els.searchInput.value = "";
    await loadWatchlist();
    state.selectedCode = normalized;
    markRecentlyAdded(normalized);
    renderRows();
    renderDetail(normalized);
    scrollToFundCard(normalized);
    els.statusText.textContent = alreadyExists
      ? "已在自选，已移到顶部"
      : `已加入 ${added.name || normalized}`;
  } finally {
    state.addingCode = null;
  }
}

els.searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await runSearch(els.searchInput.value);
  } catch (error) {
    els.statusText.textContent = error.message;
  }
});

els.addTypedBtn.addEventListener("click", async () => {
  try {
    await addCode(els.searchInput.value);
  } catch (error) {
    els.statusText.textContent = error.message;
  }
});

els.refreshBtn.addEventListener("click", async () => {
  try {
    await refreshEstimates();
  } catch (error) {
    els.statusText.textContent = error.message;
  }
});

els.searchResults.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-add]");
  if (!target) return;
  try {
    await addCode(target.dataset.add);
  } catch (error) {
    els.statusText.textContent = error.message;
  }
});

els.watchRows.addEventListener("click", async (event) => {
  await handleWatchClick(event);
});

els.mobileCards.addEventListener("click", async (event) => {
  await handleWatchClick(event);
});

async function handleWatchClick(event) {
  const holdingsToggle = event.target.closest("[data-toggle-holdings]");
  if (holdingsToggle) {
    event.stopPropagation();
    toggleHoldingDetails(holdingsToggle.dataset.toggleHoldings);
    return;
  }
  const moveBtn = event.target.closest("[data-move]");
  if (moveBtn) {
    event.stopPropagation();
    await moveWatchlistItem(moveBtn.dataset.move, moveBtn.dataset.direction);
    return;
  }
  const deleteBtn = event.target.closest("[data-delete]");
  if (deleteBtn) {
    event.stopPropagation();
    await api(`/api/watchlist/${deleteBtn.dataset.delete}`, { method: "DELETE" });
    await loadWatchlist();
    return;
  }
  if (event.target.closest("a")) return;
  const row = event.target.closest("[data-code]");
  if (!row) return;
  state.selectedCode = row.dataset.code;
  renderRows();
  renderDetail(state.selectedCode);
}

function toggleHoldingDetails(code) {
  if (!code) return;
  if (state.expandedHoldings.has(code)) {
    state.expandedHoldings.delete(code);
  } else {
    state.expandedHoldings.add(code);
  }
  renderRows();
}

async function moveWatchlistItem(code, direction) {
  const currentIndex = state.watchlist.findIndex((item) => item.code === code);
  if (currentIndex < 0) return;
  const targetIndex = direction === "up" ? currentIndex - 1 : currentIndex + 1;
  if (targetIndex < 0 || targetIndex >= state.watchlist.length) return;
  const codes = state.watchlist.map((item) => item.code);
  [codes[currentIndex], codes[targetIndex]] = [codes[targetIndex], codes[currentIndex]];
  await saveWatchlistOrder(codes);
}

async function saveWatchlistOrder(codes) {
  const itemMap = new Map(state.watchlist.map((item) => [item.code, item]));
  state.watchlist = codes.map((code) => itemMap.get(code)).filter(Boolean);
  renderRows();
  try {
    const saved = await api("/api/watchlist/order", {
      method: "PUT",
      body: JSON.stringify({ codes }),
    });
    state.watchlist = saved;
    renderRows();
    if (!state.selectedCode || !codes.includes(state.selectedCode)) {
      state.selectedCode = codes[0] || null;
    }
    if (state.selectedCode) renderDetail(state.selectedCode);
    els.statusText.textContent = "排序已保存";
  } catch (error) {
    els.statusText.textContent = error.message;
    await loadWatchlist();
  }
}

let searchTimer = null;
els.searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    runSearch(els.searchInput.value).catch((error) => {
      els.statusText.textContent = error.message;
    });
  }, 250);
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".search")) {
    els.searchResults.classList.remove("active");
  }
});

function isMarketRefreshWindow(now = new Date()) {
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = now.getHours() * 60 + now.getMinutes();
  const morning = minutes >= 9 * 60 + 30 && minutes <= 11 * 60 + 30;
  const afternoon = minutes >= 13 * 60 && minutes <= 15 * 60;
  return morning || afternoon;
}

function localDateText(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shouldAutoRefresh(now = new Date()) {
  if (!isMarketRefreshWindow(now) || !state.watchlist.length) return false;
  if (!state.estimates.size) return true;
  const today = localDateText(now);
  return Array.from(state.estimates.values()).some((result) => {
    const est = result?.estimate;
    return result?.ok && est && !isOfficial(est) && estimatedDateText(est) === today;
  });
}

setInterval(() => {
  if (shouldAutoRefresh()) {
    refreshEstimates().catch((error) => {
      els.statusText.textContent = error.message;
    });
  }
}, 15000);

loadSourceStatus();
loadWatchlist().catch((error) => {
  els.statusText.textContent = error.message;
  renderEmptyDetail();
});

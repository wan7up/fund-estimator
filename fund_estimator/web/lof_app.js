const DEVICE_ID_KEY = "lof_premium_monitor_device_id";

function createDeviceId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `lof-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
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
  deviceId: getDeviceId(),
  items: [],
  watchlist: [],
  selectedCode: null,
  filter: "all",
  showPurchasePaused: false,
  lofSort: { key: "official_premium_pct", direction: "desc" },
  scanInFlight: false,
  lastResponse: null,
  noticeStatus: null,
  noticeInFlight: false,
  noticePollTimer: null,
  noticeConnectPending: false,
};

const EST_PREMIUM_HINT = "场内价相对盘中估算净值的溢价；估算净值来自海外指数/期货代理或可用估算源";
const OFFICIAL_PREMIUM_HINT = "场内价相对基金公司已披露最新净值的溢价；不是集思录数据";
const REFERENCE_CHANGE_HINT = "参考标的从基金官方净值日到当前的加权涨跌幅；LOF 盘中估算净值主要由它把官方净值向前滚动得到";

const els = {
  sourceBadge: document.querySelector("#sourceBadge"),
  searchForm: document.querySelector("#lofSearchForm"),
  searchInput: document.querySelector("#lofSearchInput"),
  searchResults: document.querySelector("#lofSearchResults"),
  addTypedBtn: document.querySelector("#lofAddTypedBtn"),
  refreshBtn: document.querySelector("#lofRefreshBtn"),
  statusText: document.querySelector("#lofStatusText"),
  errorBanner: document.querySelector("#errorBanner"),
  filterTabs: document.querySelectorAll("[data-filter]"),
  purchasePausedToggle: document.querySelector("#lofPurchasePausedToggle"),
  sortButtons: document.querySelectorAll("[data-lof-sort]"),
  statTotal: document.querySelector("#statTotal"),
  statOpportunity: document.querySelector("#statOpportunity"),
  statStrong: document.querySelector("#statStrong"),
  statActionable: document.querySelector("#statActionable"),
  statWatchlist: document.querySelector("#statWatchlist"),
  rows: document.querySelector("#lofRows"),
  cards: document.querySelector("#lofCards"),
  detailTitle: document.querySelector("#lofDetailTitle"),
  detailMeta: document.querySelector("#lofDetailMeta"),
  detailSummary: document.querySelector("#lofDetailSummary"),
  proxyList: document.querySelector("#lofProxyList"),
  riskList: document.querySelector("#lofRiskList"),
  noticeEnabledInput: document.querySelector("#noticeEnabledInput"),
  noticeTimeInput: document.querySelector("#noticeTimeInput"),
  ipoReminderToggleBtn: document.querySelector("#ipoReminderToggleBtn"),
  noticeConnectBtn: document.querySelector("#noticeConnectBtn"),
  noticeDisconnectBtn: document.querySelector("#noticeDisconnectBtn"),
  noticeConnectPanel: document.querySelector("#noticeConnectPanel"),
  noticeConnectHint: document.querySelector("#noticeConnectHint"),
  noticeQrBox: document.querySelector("#noticeQrBox"),
  noticeTestBtn: document.querySelector("#noticeTestBtn"),
  noticeStatusText: document.querySelector("#noticeStatusText"),
  noticeDependents: document.querySelectorAll("[data-notice-dependent]"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${fmt(value, 2)}%`;
}

function fmtMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const amount = Number(value);
  if (Math.abs(amount) >= 100000000) return `${fmt(amount / 100000000, 2)}亿`;
  if (Math.abs(amount) >= 10000) return `${fmt(amount / 10000, 0)}万`;
  return fmt(amount, 0);
}

function clsForPct(value) {
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "flat";
}

function levelText(level) {
  if (level === "strong") return "强信号";
  if (level === "normal") return "普通机会";
  return "观察";
}

function signalText(item) {
  if (item.is_opportunity && !item.actionable) return "受限机会";
  if (item.is_opportunity) return levelText(item.level);
  return "观察";
}

function directionText(direction) {
  if (direction === "premium") return "溢价";
  if (direction === "discount") return "折价";
  if (direction === "neutral") return "中性";
  return "未知";
}

function statusText(value) {
  if (!value || value === "unknown") return "未知";
  return value;
}

function fmtFee(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return `费率${fmt(value, 2)}%`;
}

function statusLine(label, status, parts = []) {
  return [label + statusText(status), ...parts.filter(Boolean)].map(escapeHtml).join(" · ");
}

function statusCell(item) {
  const limit = item.daily_purchase_limit_yuan ? `限额${fmtMoney(item.daily_purchase_limit_yuan)}` : null;
  const fee = fmtFee(item.fee_rate_pct);
  return `
    <div class="status-cell">
      <span>${statusLine("申购", item.purchase_status, [limit, fee])}</span>
      <small>${statusLine("赎回", item.redemption_status, [fee])}</small>
    </div>`;
}

function premiumValue(item) {
  return item.estimated_premium_pct ?? item.official_premium_pct;
}

function referenceChange(item) {
  if (item.reference_change_pct !== null && item.reference_change_pct !== undefined) {
    return Number(item.reference_change_pct);
  }
  if (!item.proxy_moves || !item.proxy_moves.length) return null;
  let total = 0;
  let weight = 0;
  for (const move of item.proxy_moves) {
    if (move.change_pct === null || move.change_pct === undefined || Number.isNaN(Number(move.change_pct))) continue;
    total += Number(move.change_pct) * Number(move.weight || 0);
    weight += Number(move.weight || 0);
  }
  if (!weight) return null;
  return total / weight;
}

function referenceText(item) {
  const change = referenceChange(item);
  if (change === null) return "--";
  return fmtPct(change);
}

function referencePeriodText(item) {
  if (item.reference_period_start) {
    return `${item.reference_period_start} 至当前`;
  }
  if (item.reference_basis === "auxiliary_latest_daily") return "最近一日辅助观察";
  if (item.reference_period_end) return `截至 ${item.reference_period_end}`;
  return "--";
}

function movePeriodText(move) {
  if (move.warning) return move.warning;
  if (move.period_start) return `${move.period_start} 至当前`;
  if (move.change_basis === "latest_daily") return "最近一日";
  return move.source;
}

function relationEstimatePct(item) {
  const official = item.official_premium_pct;
  const reference = referenceChange(item);
  if (official === null || official === undefined || reference === null || reference === undefined) return null;
  if (1 + Number(reference) / 100 === 0) return null;
  return ((1 + Number(official) / 100) / (1 + Number(reference) / 100) - 1) * 100;
}

function watchSet() {
  return new Set(state.watchlist.map((item) => item.code));
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Device-Id": state.deviceId,
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
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
    els.sourceBadge.textContent = status.mode === "mock" ? "演示数据" : "实时数据";
    els.sourceBadge.className = status.mode === "mock" ? "mock" : "real";
  } catch {
    els.sourceBadge.textContent = "数据源未知";
    els.sourceBadge.className = "";
  }
}

async function loadWatchlist() {
  state.watchlist = await api("/api/lof/watchlist");
}

function syncNoticeInputs(status) {
  els.noticeEnabledInput.checked = Boolean(status.enabled);
  els.noticeTimeInput.value = status.daily_summary_time || "10:00";
  renderIpoReminderToggle(Boolean(status.ipo_reminder_enabled));
}

function renderIpoReminderToggle(enabled, busy = false) {
  els.ipoReminderToggleBtn.textContent = busy ? "打新保存中" : `打新提醒${enabled ? "开" : "关"}`;
  els.ipoReminderToggleBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
  els.ipoReminderToggleBtn.classList.toggle("active", enabled);
}

function compactNoticeTarget(value) {
  const text = String(value || "").trim();
  if (text.length <= 12) return text || "--";
  return `${text.slice(0, 5)}...${text.slice(-5)}`;
}

function renderNoticeVisibility(enabled) {
  for (const element of els.noticeDependents) {
    element.hidden = !enabled || (element === els.noticeDisconnectBtn && !state.noticeStatus?.connected);
  }
  if (!enabled) {
    els.noticeConnectPanel.hidden = true;
    els.noticeQrBox.innerHTML = "";
  }
}

function renderNoticeStatus(prefix = "") {
  const status = state.noticeStatus;
  if (!status) {
    renderNoticeVisibility(els.noticeEnabledInput.checked);
    els.noticeStatusText.textContent = prefix || "";
    return;
  }
  const noticeEnabled = Boolean(status.enabled);
  renderNoticeVisibility(noticeEnabled);
  if (!noticeEnabled) {
    els.noticeStatusText.textContent = "";
    return;
  }
  const targetText = !status.app_configured
    ? "未接入飞书"
    : status.target_set
      ? `已绑定/${compactNoticeTarget(status.target_name || status.target_kind)}`
      : "未接入飞书";
  const lastText = status.last_status ? `最近：${status.last_status}` : "最近：尚未发送";
  els.noticeStatusText.textContent = `${targetText} · ${lastText}`;
  if (status.connected) state.noticeConnectPending = false;
  if (!state.noticeConnectPending) {
    els.noticeConnectPanel.hidden = true;
    els.noticeQrBox.innerHTML = "";
  }
  if (state.noticeConnectPending && !status.connected) {
    els.noticeStatusText.textContent = "未接入飞书 · 最近：等待扫码";
  }
  els.noticeConnectBtn.disabled = state.noticeInFlight;
  els.noticeConnectBtn.textContent = status.connected || state.noticeConnectPending ? "重新接入" : "接入飞书";
  els.noticeDisconnectBtn.hidden = !status.connected;
  els.noticeDisconnectBtn.disabled = state.noticeInFlight;
  els.ipoReminderToggleBtn.disabled = false;
  els.noticeTestBtn.disabled = state.noticeInFlight || !status.target_set || !status.app_configured;
}

async function loadNoticeStatus() {
  try {
    const status = await api("/api/lof/notice/status");
    state.noticeStatus = status;
    syncNoticeInputs(status);
    renderNoticeStatus();
  } catch (error) {
    els.noticeStatusText.textContent = `通知状态读取失败：${error.message}`;
  }
}

async function saveNoticeSettings(prefix = "已自动保存", overrides = {}) {
  if (state.noticeInFlight) return;
  const nextEnabled = Boolean(overrides.enabled ?? els.noticeEnabledInput.checked);
  renderNoticeVisibility(nextEnabled);
  state.noticeInFlight = true;
  els.noticeStatusText.textContent = nextEnabled ? "保存通知设置..." : "";
  let saved = false;
  try {
    const status = await api("/api/lof/notice/settings", {
      method: "PUT",
      body: JSON.stringify({
        enabled: overrides.enabled ?? els.noticeEnabledInput.checked,
        daily_summary_time: overrides.daily_summary_time ?? (els.noticeTimeInput.value || "10:00"),
        ipo_reminder_enabled: overrides.ipo_reminder_enabled ?? Boolean(state.noticeStatus?.ipo_reminder_enabled),
      }),
    });
    state.noticeStatus = status;
    syncNoticeInputs(status);
    saved = true;
  } catch (error) {
    els.noticeStatusText.textContent = `保存失败：${error.message}`;
    if (state.noticeStatus) syncNoticeInputs(state.noticeStatus);
    if (state.noticeStatus) renderNoticeVisibility(Boolean(state.noticeStatus.enabled));
  } finally {
    state.noticeInFlight = false;
    if (saved) renderNoticeStatus(prefix);
  }
}

async function toggleIpoReminder() {
  const next = !Boolean(state.noticeStatus?.ipo_reminder_enabled);
  renderIpoReminderToggle(next, true);
  await saveNoticeSettings(next ? "打新提醒已开启" : "打新提醒已关闭", { ipo_reminder_enabled: next });
}

async function connectFeishuNotice() {
  if (state.noticeInFlight) return;
  if (state.noticeStatus?.connected) {
    const confirmed = window.confirm("重新接入会覆盖当前飞书机器人绑定。确定继续？");
    if (!confirmed) return;
  }
  state.noticeInFlight = true;
  els.noticeConnectBtn.disabled = true;
  els.noticeStatusText.textContent = "生成飞书接入二维码...";
  try {
    const result = await api("/api/lof/notice/feishu/connect", { method: "POST" });
    if (!result.qr_url) throw new Error(result.setup_hint || "飞书未返回二维码");
    state.noticeConnectPending = true;
    renderFeishuQr(result);
    scheduleFeishuPoll(result.interval_seconds || 5);
    els.noticeStatusText.textContent = "请用飞书扫码配置机器人";
  } catch (error) {
    state.noticeConnectPending = false;
    els.noticeConnectPanel.hidden = true;
    els.noticeStatusText.textContent = `接入失败：${error.message}`;
  } finally {
    state.noticeInFlight = false;
    renderNoticeStatus();
  }
}

function renderFeishuQr(result) {
  els.noticeConnectPanel.hidden = false;
  els.noticeConnectHint.textContent = result.expires_at
    ? `请用飞书扫描二维码，二维码约在 ${new Date(result.expires_at).toLocaleTimeString()} 前有效。`
    : "请用飞书扫描二维码，按提示配置机器人。";
  els.noticeQrBox.innerHTML = "";
  if (typeof qrcode === "function") {
    const qr = qrcode(0, "M");
    qr.addData(result.qr_url);
    qr.make();
    els.noticeQrBox.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 3 });
  } else {
    els.noticeQrBox.innerHTML = `<a href="${escapeHtml(result.qr_url)}" target="_blank" rel="noreferrer">打开飞书接入链接</a>`;
  }
}

function scheduleFeishuPoll(intervalSeconds) {
  if (state.noticePollTimer) clearTimeout(state.noticePollTimer);
  state.noticePollTimer = setTimeout(() => {
    pollFeishuNotice().catch((error) => {
      els.noticeStatusText.textContent = `接入轮询失败：${error.message}`;
      if (state.noticeConnectPending) scheduleFeishuPoll(intervalSeconds);
    });
  }, Math.max(2, Number(intervalSeconds) || 5) * 1000);
}

async function pollFeishuNotice() {
  const result = await api("/api/lof/notice/feishu/poll", { method: "POST" });
  if (result.status === "connected") {
    state.noticeConnectPending = false;
    if (state.noticePollTimer) clearTimeout(state.noticePollTimer);
    state.noticePollTimer = null;
    els.noticeConnectPanel.hidden = true;
    await loadNoticeStatus();
    renderNoticeStatus("飞书已接入");
    return;
  }
  if (result.status === "expired" || result.status === "failed") {
    state.noticeConnectPending = false;
    if (state.noticePollTimer) clearTimeout(state.noticePollTimer);
    state.noticePollTimer = null;
    els.noticeConnectPanel.hidden = true;
    els.noticeStatusText.textContent = result.setup_hint || "飞书接入失败，请重新尝试";
    return;
  }
  if (result.qr_url) renderFeishuQr(result);
  scheduleFeishuPoll(result.interval_seconds || 5);
}

async function disconnectFeishuNotice() {
  if (state.noticeInFlight) return;
  if (state.noticeStatus?.connected) {
    const confirmed = window.confirm("断开后会清除本地保存的飞书机器人凭据，并停止发送飞书通知。确定断开？");
    if (!confirmed) return;
  }
  state.noticeInFlight = true;
  els.noticeDisconnectBtn.disabled = true;
  els.noticeStatusText.textContent = "断开飞书接入...";
  let disconnected = false;
  try {
    const status = await api("/api/lof/notice/feishu/disconnect", { method: "POST" });
    state.noticeConnectPending = false;
    if (state.noticePollTimer) clearTimeout(state.noticePollTimer);
    state.noticePollTimer = null;
    els.noticeConnectPanel.hidden = true;
    state.noticeStatus = status;
    syncNoticeInputs(status);
    disconnected = true;
  } catch (error) {
    els.noticeStatusText.textContent = `断开失败：${error.message}`;
  } finally {
    state.noticeInFlight = false;
    if (disconnected) renderNoticeStatus("已断开");
    else els.noticeDisconnectBtn.disabled = false;
  }
}

async function sendNoticeTest() {
  if (state.noticeInFlight) return;
  state.noticeInFlight = true;
  els.noticeTestBtn.disabled = true;
  els.noticeStatusText.textContent = "发送测试通知...";
  try {
    await api("/api/lof/notice/test", { method: "POST" });
    await loadNoticeStatus();
    renderNoticeStatus("测试已执行");
  } catch (error) {
    els.noticeStatusText.textContent = `测试失败：${error.message}`;
  } finally {
    state.noticeInFlight = false;
    renderNoticeStatus();
  }
}

async function fetchScan(force) {
  const params = new URLSearchParams({
    limit: "500",
    refresh: force ? "true" : "false",
  });
  return api(`/api/lof/opportunities?${params.toString()}`);
}


function needsInitialScan(response) {
  const items = response?.items || [];
  const errors = response?.errors || [];
  return items.length === 0 && errors.some((error) => String(error).includes("后台扫描尚未完成"));
}

async function refreshLof(force = false) {
  if (state.scanInFlight) return;
  state.scanInFlight = true;
  els.refreshBtn.disabled = true;
  els.statusText.textContent = force ? "扫描中" : "读取缓存";
  try {
    let response = await fetchScan(force);
    if (!force && needsInitialScan(response)) {
      els.statusText.textContent = "首次扫描中，可能需要几十秒";
      response = await fetchScan(true);
    }
    state.lastResponse = response;
    state.items = response.items || [];
    if (!state.items.some((item) => item.code === state.selectedCode)) {
      state.selectedCode = state.items[0]?.code || null;
    }
    renderAll();
  } catch (error) {
    showErrors([error.message]);
    els.statusText.textContent = "读取失败";
  } finally {
    state.scanInFlight = false;
    els.refreshBtn.disabled = false;
  }
}


function sortValue(item, key) {
  if (!key) return null;
  const value = item[key];
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value);
}

function sortItems(items, sortState) {
  if (!sortState.key) return items;
  const direction = sortState.direction === "asc" ? "asc" : "desc";
  return items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const leftValue = sortValue(left.item, sortState.key);
      const rightValue = sortValue(right.item, sortState.key);
      if (leftValue === null && rightValue === null) return left.index - right.index;
      if (leftValue === null) return 1;
      if (rightValue === null) return -1;
      const diff = direction === "asc" ? leftValue - rightValue : rightValue - leftValue;
      return diff || left.index - right.index;
    })
    .map((entry) => entry.item);
}

function toggleSort(sortState, key) {
  if (sortState.key === key) {
    sortState.direction = sortState.direction === "desc" ? "asc" : "desc";
  } else {
    sortState.key = key;
    sortState.direction = "desc";
  }
}

function renderSortButtons(buttons, sortState) {
  for (const button of buttons) {
    const key = button.dataset.lofSort;
    const active = key === sortState.key;
    button.classList.toggle("active", active);
    const indicator = button.querySelector("span");
    if (indicator) {
      indicator.textContent = active ? (sortState.direction === "desc" ? "↓" : "↑") : "↕";
    }
  }
}

function isPurchasePaused(item) {
  return item.purchase_status === "暂停";
}

function applyPurchasePausedFilter(items, showPurchasePaused) {
  if (showPurchasePaused) return items;
  return items.filter((item) => !isPurchasePaused(item));
}

function renderPurchasePausedToggle(button, showPurchasePaused, pausedCount) {
  if (!button) return;
  button.classList.toggle("active", showPurchasePaused);
  button.setAttribute("aria-pressed", String(showPurchasePaused));
  const suffix = pausedCount ? `(${pausedCount})` : "";
  button.textContent = `申购暂停: ${showPurchasePaused ? "显示" : "隐藏"}${suffix}`;
}

function filteredItems() {
  const watched = watchSet();
  let items = state.items;
  if (state.filter === "opportunity") items = items.filter((item) => item.is_opportunity);
  if (state.filter === "actionable") items = items.filter((item) => item.actionable);
  if (state.filter === "watchlist") items = items.filter((item) => watched.has(item.code));
  items = applyPurchasePausedFilter(items, state.showPurchasePaused);
  return sortItems(items, state.lofSort);
}


function showErrors(errors) {
  const visibleErrors = (errors || []).filter(Boolean);
  if (!visibleErrors.length) {
    els.errorBanner.hidden = true;
    els.errorBanner.innerHTML = "";
    return;
  }
  els.errorBanner.hidden = false;
  els.errorBanner.innerHTML = visibleErrors.slice(0, 3).map((error) => `<span>${escapeHtml(error)}</span>`).join("");
}

function renderStats() {
  const opportunityCount = state.items.filter((item) => item.is_opportunity).length;
  const strongCount = state.items.filter((item) => item.level === "strong" && item.actionable).length;
  const actionableCount = state.items.filter((item) => item.actionable).length;
  els.statTotal.textContent = state.items.length || "--";
  els.statOpportunity.textContent = opportunityCount;
  els.statStrong.textContent = strongCount;
  els.statActionable.textContent = actionableCount;
  els.statWatchlist.textContent = state.watchlist.length;
}

function renderStatus() {
  const response = state.lastResponse;
  if (!response) {
    els.statusText.textContent = "等待加载";
    return;
  }
  const scannedAt = response.scanned_at ? new Date(response.scanned_at).toLocaleTimeString() : "--";
  const visibleCount = filteredItems().length;
  els.statusText.textContent = `已更新 ${scannedAt} · 显示 ${visibleCount}/${state.items.length}`;
}


function riskTags(item, limit = 4) {
  const risks = item.risks && item.risks.length ? item.risks : ["暂无风险"];
  return `<div class="risk-tags">${risks
    .slice(0, limit)
    .map((risk) => {
      const warning = /暂停|限制|不足|缺失|过期|未知|冷却/.test(risk);
      return `<span class="risk-tag ${warning ? "warning" : ""}">${escapeHtml(risk)}</span>`;
    })
    .join("")}</div>`;
}

function signalBadge(item) {
  const blocked = item.is_opportunity && !item.actionable;
  const label = signalText(item);
  const cls = blocked ? "blocked" : item.level;
  return `<span class="signal-badge ${cls}">${label}</span>`;
}

function renderRows() {
  const items = filteredItems();
  if (!state.items.length) {
    els.rows.innerHTML = `<tr><td colspan="10" class="empty">暂无扫描结果</td></tr>`;
    els.cards.innerHTML = `<div class="empty">暂无扫描结果</div>`;
    return;
  }
  if (!items.length) {
    els.rows.innerHTML = `<tr><td colspan="10" class="empty">当前筛选没有结果</td></tr>`;
    els.cards.innerHTML = `<div class="empty">当前筛选没有结果</div>`;
    return;
  }
  const watched = watchSet();
  els.rows.innerHTML = items.map((item) => rowHtml(item, watched.has(item.code))).join("");
  els.cards.innerHTML = items.map((item) => cardHtml(item, watched.has(item.code))).join("");
}

function rowHtml(item, watched) {
  const selected = state.selectedCode === item.code ? "selected" : "";
  const premium = premiumValue(item);
  return `
    <tr data-lof-code="${item.code}" class="${selected} level-${item.level}">
      <td>
        <div class="fund-name">
          <strong>${escapeHtml(item.name)}</strong>
          <small>${item.code} · ${escapeHtml(item.theme || item.fund_type || "LOF")} ${signalBadge(item)}</small>
        </div>
      </td>
      <td>${fmt(item.exchange_price, 3)}<br><small class="${clsForPct(item.exchange_change_pct || 0)}">${fmtPct(item.exchange_change_pct)}</small></td>
      <td>${fmt(item.estimated_nav, 4)}<br><small>${item.estimated_nav_time ? "盘中估算" : "--"}</small></td>
      <td class="${clsForPct(item.estimated_premium_pct || 0)}"><strong>${fmtPct(item.estimated_premium_pct)}</strong></td>
      <td class="${clsForPct(item.official_premium_pct || 0)}">${fmtPct(item.official_premium_pct)}<br><small>${item.official_nav_date || "--"}</small></td>
      <td class="${clsForPct(referenceChange(item) || 0)}">${referenceText(item)}<br><small>${referencePeriodText(item)}</small></td>
      <td>${fmtMoney(item.exchange_turnover_yuan)}</td>
      <td>${statusCell(item)}</td>
      <td>${riskTags(item)}</td>
      <td class="row-actions">
        <button class="watch-btn ${watched ? "active" : ""}" type="button" data-lof-watch="${item.code}" title="${watched ? "移出自选" : "加入自选"}">${watched ? "★" : "☆"}</button>
      </td>
    </tr>`;
}

function cardHtml(item, watched) {
  const selected = state.selectedCode === item.code;
  return `
    <article class="lof-card level-${item.level} ${selected ? "selected" : ""}" data-lof-code="${item.code}">
      <div class="card-head">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <small>${item.code} · ${escapeHtml(item.theme || item.fund_type || "LOF")}</small>
        </div>
        <button class="watch-btn ${watched ? "active" : ""}" type="button" data-lof-watch="${item.code}" title="${watched ? "移出自选" : "加入自选"}">${watched ? "★" : "☆"}</button>
      </div>
      <div class="card-metrics">
        <div><span title="${EST_PREMIUM_HINT}">估算溢价</span><strong class="${clsForPct(item.estimated_premium_pct || 0)}">${fmtPct(item.estimated_premium_pct)}</strong></div>
        <div><span title="${REFERENCE_CHANGE_HINT}">参考标的期间涨幅</span><strong class="${clsForPct(referenceChange(item) || 0)}">${referenceText(item)}</strong></div>
        <div><span title="${OFFICIAL_PREMIUM_HINT}">官方净值溢价</span><strong class="${clsForPct(item.official_premium_pct || 0)}">${fmtPct(item.official_premium_pct)}</strong></div>
        <div><span>场内价</span><strong>${fmt(item.exchange_price, 3)}</strong></div>
      </div>
      ${riskTags(item, 3)}
      ${selected ? mobileInlineDetailHtml(lofSummaryHtml(item), "参考标的明细", proxyMovesHtml(item, "暂无参考标的行情"), riskListHtml(item)) : ""}
    </article>`;
}

function metric(label, value, detail = "", valueClass = "", title = "") {
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
  return `
    <div class="metric">
      <span${titleAttr}>${escapeHtml(label)}</span>
      <strong class="${valueClass}">${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>`;
}

function proxyMovesHtml(item, emptyText) {
  if (!item.proxy_moves || !item.proxy_moves.length) {
    return `<div class="empty compact">${escapeHtml(emptyText)}</div>`;
  }
  return item.proxy_moves
    .map(
      (move) => `
        <div class="proxy-row">
          <div><strong>${escapeHtml(move.symbol)}</strong><small>${escapeHtml(move.label)}</small></div>
          <span>${fmt(Number(move.weight || 0) * 100, 0)}%</span>
          <strong class="${clsForPct(move.change_pct || 0)}">${fmtPct(move.change_pct)}</strong>
          <small>${escapeHtml(movePeriodText(move))}</small>
        </div>`
    )
    .join("");
}

function riskListHtml(item, emptyText = "暂无风险标记") {
  return (item.risks && item.risks.length ? item.risks : [emptyText])
    .map((risk) => `<li>${escapeHtml(risk)}</li>`)
    .join("");
}

function mobileInlineDetailHtml(summaryHtml, marketTitle, marketHtml, risksHtml) {
  return `
    <div class="mobile-inline-detail">
      <div class="detail-summary">${summaryHtml}</div>
      <h3>${escapeHtml(marketTitle)}</h3>
      <div class="proxy-list">${marketHtml}</div>
      <h3>风险标记</h3>
      <ul class="risk-list">${risksHtml}</ul>
    </div>`;
}

function lofSummaryHtml(item) {
  const premium = premiumValue(item);
  const relationPremium = relationEstimatePct(item);
  return `
    ${metric("估算溢价", fmtPct(item.estimated_premium_pct), fmt(item.estimated_nav, 4), clsForPct(item.estimated_premium_pct || 0), EST_PREMIUM_HINT)}
    ${metric("参考标的期间涨幅", referenceText(item), referencePeriodText(item), clsForPct(referenceChange(item) || 0), REFERENCE_CHANGE_HINT)}
    ${metric("官方净值溢价", fmtPct(item.official_premium_pct), `${fmt(item.official_nav, 4)} / ${item.official_nav_date || "--"}`, clsForPct(item.official_premium_pct || 0), OFFICIAL_PREMIUM_HINT)}
    ${metric("折算关系", fmtPct(relationPremium), `官方 ${fmtPct(item.official_premium_pct)} / 标的 ${referenceText(item)}`, clsForPct(relationPremium || 0), "按 (1+官方净值溢价)/(1+参考标的期间涨幅)-1 折算")}
    ${metric("场内价格", fmt(item.exchange_price, 3), fmtPct(item.exchange_change_pct), clsForPct(item.exchange_change_pct || 0))}
    ${metric("成交额", fmtMoney(item.exchange_turnover_yuan), `申购 ${statusText(item.purchase_status)} / 赎回 ${statusText(item.redemption_status)}`)}
    ${metric("信号", directionText(item.direction), `${signalText(item)} · ${fmtPct(premium)}`)}
    ${metric("日限额", fmtMoney(item.daily_purchase_limit_yuan), item.fee_rate_pct === null || item.fee_rate_pct === undefined ? "费率 --" : `费率 ${fmt(item.fee_rate_pct, 2)}%`)}`;
}


function renderDetail() {
  const item = state.items.find((entry) => entry.code === state.selectedCode);
  if (!item) {
    els.detailTitle.textContent = "LOF详情";
    els.detailMeta.textContent = "";
    els.detailSummary.innerHTML = `<div class="empty">选择一只基金查看详情</div>`;
    els.proxyList.innerHTML = "";
    els.riskList.innerHTML = "";
    return;
  }
  els.detailTitle.textContent = item.name;
  els.detailMeta.textContent = `${item.code} · ${directionText(item.direction)} · ${signalText(item)}`;
  els.detailSummary.innerHTML = lofSummaryHtml(item);
  els.proxyList.innerHTML = proxyMovesHtml(item, "暂无参考标的行情");
  els.riskList.innerHTML = riskListHtml(item);
}


function renderFilters() {
  for (const tab of els.filterTabs) {
    tab.classList.toggle("active", tab.dataset.filter === state.filter);
  }
  renderPurchasePausedToggle(
    els.purchasePausedToggle,
    state.showPurchasePaused,
    state.items.filter(isPurchasePaused).length
  );
}

function renderAll() {
  showErrors(state.lastResponse?.errors || []);
  renderStats();
  renderFilters();
  renderSortButtons(els.sortButtons, state.lofSort);
  renderRows();
  renderDetail();
  renderStatus();
}

async function runSearch(query) {
  const q = query.trim();
  if (!q) {
    els.searchResults.classList.remove("active");
    els.searchResults.innerHTML = "";
    return;
  }
  const results = await api(`/api/lof/search?q=${encodeURIComponent(q)}`);
  els.searchResults.innerHTML = results.length
    ? results
        .map(
          (item) => `
            <button class="search-result" type="button" data-lof-add="${item.code}">
              <span>${escapeHtml(item.name)}</span>
              <strong>${item.code}</strong>
            </button>`
        )
        .join("")
    : `<div class="empty compact">未找到匹配LOF</div>`;
  els.searchResults.classList.add("active");
}

async function addLofCode(code) {
  const normalized = code.trim();
  if (!/^\d{6}$/.test(normalized)) {
    els.statusText.textContent = "请输入6位基金代码";
    return;
  }
  await api(`/api/lof/watchlist/${normalized}`, { method: "POST" });
  els.searchInput.value = "";
  els.searchResults.classList.remove("active");
  await loadWatchlist();
  state.selectedCode = normalized;
  await refreshLof(false);
}

async function toggleWatch(code) {
  const watched = state.watchlist.some((item) => item.code === code);
  await api(`/api/lof/watchlist/${code}`, { method: watched ? "DELETE" : "POST" });
  await loadWatchlist();
  await refreshLof(false);
}

function handleItemClick(event) {
  const watch = event.target.closest("[data-lof-watch]");
  if (watch) {
    event.stopPropagation();
    toggleWatch(watch.dataset.lofWatch).catch((error) => {
      showErrors([error.message]);
      els.statusText.textContent = "自选更新失败";
    });
    return;
  }
  const row = event.target.closest("[data-lof-code]");
  if (!row) return;
  state.selectedCode = row.dataset.lofCode;
  renderRows();
  renderDetail();
}


let searchTimer = null;

els.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(els.searchInput.value).catch((error) => showErrors([error.message]));
});

els.searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    runSearch(els.searchInput.value).catch((error) => showErrors([error.message]));
  }, 250);
});

els.addTypedBtn.addEventListener("click", () => {
  addLofCode(els.searchInput.value).catch((error) => showErrors([error.message]));
});

els.searchResults.addEventListener("click", (event) => {
  const target = event.target.closest("[data-lof-add]");
  if (!target) return;
  addLofCode(target.dataset.lofAdd).catch((error) => showErrors([error.message]));
});

els.refreshBtn.addEventListener("click", () => {
  refreshLof(false);
});

els.noticeEnabledInput.addEventListener("change", () => {
  const enabled = els.noticeEnabledInput.checked;
  renderNoticeVisibility(enabled);
  saveNoticeSettings(enabled ? "通知已开启" : "通知已关闭", { enabled });
});

els.noticeTimeInput.addEventListener("change", () => {
  saveNoticeSettings("通知时间已更新");
});

els.noticeTestBtn.addEventListener("click", () => {
  sendNoticeTest();
});

els.noticeConnectBtn.addEventListener("click", () => {
  connectFeishuNotice();
});

els.noticeDisconnectBtn.addEventListener("click", () => {
  disconnectFeishuNotice();
});

els.purchasePausedToggle?.addEventListener("click", () => {
  state.showPurchasePaused = !state.showPurchasePaused;
  renderAll();
});

for (const tab of els.filterTabs) {
  tab.addEventListener("click", () => {
    state.filter = tab.dataset.filter || "all";
    renderAll();
  });
}

for (const button of els.sortButtons) {
  button.addEventListener("click", () => {
    toggleSort(state.lofSort, button.dataset.lofSort);
    renderAll();
  });
}

els.rows.addEventListener("click", handleItemClick);
els.cards.addEventListener("click", handleItemClick);

document.addEventListener("click", (event) => {
  const ipoButton = event.target.closest("#ipoReminderToggleBtn");
  if (ipoButton) {
    event.preventDefault();
    toggleIpoReminder().catch((error) => {
      els.noticeStatusText.textContent = `打新提醒切换失败：${error.message}`;
      if (state.noticeStatus) syncNoticeInputs(state.noticeStatus);
    });
    return;
  }
  if (!event.target.closest(".search")) {
    els.searchResults.classList.remove("active");
  }
});

async function bootstrap() {
  await Promise.all([loadSourceStatus(), loadWatchlist(), loadNoticeStatus()]);
  await refreshLof(false);
  setInterval(() => {
    refreshLof(false);
    loadNoticeStatus();
  }, 30000);
}

bootstrap().catch((error) => {
  showErrors([error.message]);
  els.statusText.textContent = "启动失败";
});

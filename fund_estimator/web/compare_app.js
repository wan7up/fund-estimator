const state = {
  selected: [],
  strategy: "balanced",
  result: null,
  loading: false,
  ai: {
    status: null,
    models: [],
    loading: false,
    commentary: null,
    expanded: false,
    editingConfig: false,
  },
};

const DEFAULT_AI_BASE_URL = "https://api.openai.com/v1";

const STRATEGY_LABELS = {
  balanced: "稳健综合",
  aggressive: "收益进攻",
  low_cost: "低波动稳健",
};

const CONCLUSION_LABELS = {
  very_similar: "高度相似",
  same_theme_different: "可比但不同",
  not_comparable: "不可强比",
};

const THEME_MATCH_LABELS = {
  match: "匹配",
  partial: "相关",
  unmatched: "偏离",
  unknown: "待判断",
};

const SCORE_LABELS = {
  performance: "历史收益",
  ranking: "同类表现",
  scale: "规模",
  allocation: "配置风险",
  holdings: "持仓结构",
  manager: "基金经理",
  similarity: "可比性",
};

const STRATEGY_WEIGHTS = {
  balanced: {
    performance: 25,
    ranking: 20,
    scale: 13,
    allocation: 14,
    holdings: 11,
    manager: 9,
    similarity: 8,
  },
  aggressive: {
    performance: 42,
    ranking: 22,
    scale: 7,
    allocation: 10,
    holdings: 5,
    manager: 9,
    similarity: 5,
  },
  low_cost: {
    performance: 8,
    ranking: 8,
    scale: 21,
    allocation: 25,
    holdings: 18,
    manager: 8,
    similarity: 12,
  },
};

const SCORE_BASIS = {
  performance: "近1月、近3月、近6月、近1年阶段收益，结合绝对表现和候选内相对表现。",
  ranking: "基金在同类中的排名或百分位，避免跨赛道直接比收益。",
  scale: "规模过小有流动性/清盘风险，过大可能影响策略弹性，中等规模更优。",
  allocation: "股票、债券、现金仓位与当前策略口径的匹配度，也是风险暴露代理。",
  holdings: "前十大持仓占比反映集中度；过度集中会降分，缺失时中性偏低。",
  manager: "基金经理任职年限、管理规模和星级等披露信息；缺失时不直接判负。",
  similarity: "主题、类型、配置和持仓相似度，只用小权重校准可比性。",
};

const els = {
  sourceBadge: document.querySelector("#sourceBadge"),
  searchForm: document.querySelector("#searchForm"),
  searchInput: document.querySelector("#searchInput"),
  searchResults: document.querySelector("#searchResults"),
  addTypedBtn: document.querySelector("#addTypedBtn"),
  compareBtn: document.querySelector("#compareBtn"),
  strategyTabs: document.querySelectorAll("[data-strategy]"),
  themeInput: document.querySelector("#themeInput"),
  selectedFunds: document.querySelector("#selectedFunds"),
  selectionStatus: document.querySelector("#selectionStatus"),
  errorBanner: document.querySelector("#errorBanner"),
  resultSummary: document.querySelector("#resultSummary"),
  conclusionBadge: document.querySelector("#conclusionBadge"),
  conclusionTitle: document.querySelector("#conclusionTitle"),
  recommendationText: document.querySelector("#recommendationText"),
  themePanel: document.querySelector("#themePanel"),
  themeMeta: document.querySelector("#themeMeta"),
  themeSummary: document.querySelector("#themeSummary"),
  themeGrid: document.querySelector("#themeGrid"),
  rankingMeta: document.querySelector("#rankingMeta"),
  rankingRows: document.querySelector("#rankingRows"),
  fundCards: document.querySelector("#fundCards"),
  scoreMeta: document.querySelector("#scoreMeta"),
  scoreDetails: document.querySelector("#scoreDetails"),
  methodologyMeta: document.querySelector("#methodologyMeta"),
  methodologyGrid: document.querySelector("#methodologyGrid"),
  pairMeta: document.querySelector("#pairMeta"),
  pairGrid: document.querySelector("#pairGrid"),
  warningPanel: document.querySelector("#warningPanel"),
  warningList: document.querySelector("#warningList"),
  aiPanel: document.querySelector("#aiPanel"),
  aiEnableToggle: document.querySelector("#aiEnableToggle"),
  aiBody: document.querySelector("#aiBody"),
  aiStatusText: document.querySelector("#aiStatusText"),
  aiGenerateBtn: document.querySelector("#aiGenerateBtn"),
  aiDisabled: document.querySelector("#aiDisabled"),
  aiConfigSummary: document.querySelector("#aiConfigSummary"),
  aiConfigSummaryTitle: document.querySelector("#aiConfigSummaryTitle"),
  aiConfigSummaryMeta: document.querySelector("#aiConfigSummaryMeta"),
  aiModifyApiBtn: document.querySelector("#aiModifyApiBtn"),
  aiLoginForm: document.querySelector("#aiLoginForm"),
  aiPasswordModal: document.querySelector("#aiPasswordModal"),
  aiPasswordInput: document.querySelector("#aiPasswordInput"),
  aiPasswordCloseBtn: document.querySelector("#aiPasswordCloseBtn"),
  aiPasswordCancelBtn: document.querySelector("#aiPasswordCancelBtn"),
  aiPasswordError: document.querySelector("#aiPasswordError"),
  aiConfig: document.querySelector("#aiConfig"),
  aiApiFields: document.querySelector("#aiApiFields"),
  aiModelFields: document.querySelector("#aiModelFields"),
  aiBaseUrlInput: document.querySelector("#aiBaseUrlInput"),
  aiKeyInput: document.querySelector("#aiKeyInput"),
  aiHttpWarning: document.querySelector("#aiHttpWarning"),
  aiSaveFetchBtn: document.querySelector("#aiSaveFetchBtn"),
  aiFetchModelsBtn: document.querySelector("#aiFetchModelsBtn"),
  aiModelSelect: document.querySelector("#aiModelSelect"),
  aiPersonaSelect: document.querySelector("#aiPersonaSelect"),
  aiCustomPersonaWrap: document.querySelector("#aiCustomPersonaWrap"),
  aiCustomPersonaInput: document.querySelector("#aiCustomPersonaInput"),
  aiSaveConfigBtn: document.querySelector("#aiSaveConfigBtn"),
  aiError: document.querySelector("#aiError"),
  aiOutput: document.querySelector("#aiOutput"),
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

function fmtLimit(value) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? "--" : fmtMoney(value);
}

function clsForPct(value) {
  if (Number(value) > 0) return "up";
  if (Number(value) < 0) return "down";
  return "flat";
}

function rankText(snapshot) {
  if (!snapshot) return "--";
  if (snapshot.similar_rank && snapshot.similar_rank_total) {
    return `${snapshot.similar_rank}/${snapshot.similar_rank_total}`;
  }
  if (snapshot.similar_rank_percentile_pct !== null && snapshot.similar_rank_percentile_pct !== undefined) {
    return `${fmt(snapshot.similar_rank_percentile_pct, 2)}%`;
  }
  return "--";
}

function localScoreFactors() {
  return Object.entries(STRATEGY_WEIGHTS[state.strategy] || STRATEGY_WEIGHTS.balanced).map(([key, weight]) => ({
    key,
    label: SCORE_LABELS[key] || key,
    weight_pct: weight,
    basis: SCORE_BASIS[key] || "",
  }));
}

function renderMethodology(factors = null) {
  const items = Array.isArray(factors) && factors.length ? factors : localScoreFactors();
  els.methodologyMeta.textContent = STRATEGY_LABELS[state.strategy] || "当前口径";
  els.methodologyGrid.innerHTML = items
    .map(
      (item) => `
        <article class="methodology-item">
          <div>
            <strong>${escapeHtml(item.label || SCORE_LABELS[item.key] || item.key)}</strong>
            <span>${fmt(item.weight_pct, item.weight_pct % 1 === 0 ? 0 : 1)}%</span>
          </div>
          <p>${escapeHtml(item.basis || SCORE_BASIS[item.key] || "")}</p>
        </article>`
    )
    .join("");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = data.error || data.detail || { code: response.status, message: response.statusText };
    const message = Array.isArray(err) ? err.map((item) => item.msg).join("；") : `${err.code || response.status}: ${err.message || response.statusText}`;
    throw new Error(message);
  }
  return data;
}

function showErrors(errors) {
  const visibleErrors = (errors || []).filter(Boolean);
  if (!visibleErrors.length) {
    els.errorBanner.hidden = true;
    els.errorBanner.innerHTML = "";
    return;
  }
  els.errorBanner.hidden = false;
  els.errorBanner.innerHTML = visibleErrors.slice(0, 4).map((error) => `<span>${escapeHtml(error)}</span>`).join("");
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

function setAiError(message) {
  if (!message) {
    els.aiError.hidden = true;
    els.aiError.textContent = "";
    return;
  }
  els.aiError.hidden = false;
  els.aiError.textContent = message;
}

function setAiPasswordError(message) {
  if (!message) {
    els.aiPasswordError.hidden = true;
    els.aiPasswordError.textContent = "";
    return;
  }
  els.aiPasswordError.hidden = false;
  els.aiPasswordError.textContent = message;
}

function openAiPasswordModal() {
  els.aiPasswordModal.hidden = false;
  els.aiLoginForm.hidden = false;
  els.aiPasswordInput.value = "";
  setAiPasswordError("");
  setTimeout(() => els.aiPasswordInput.focus(), 0);
}

function closeAiPasswordModal() {
  els.aiPasswordModal.hidden = true;
  els.aiPasswordInput.value = "";
  setAiPasswordError("");
}

function cancelAiEnable() {
  closeAiPasswordModal();
  if (!state.ai.status?.authenticated) {
    state.ai.expanded = false;
    state.ai.editingConfig = false;
  }
  renderAiPanel();
}

function updateAiHttpWarning() {
  const value = els.aiBaseUrlInput.value.trim().toLowerCase();
  els.aiHttpWarning.hidden = !value.startsWith("http://");
}

function renderAiPersonas(personas = []) {
  if (!personas.length || els.aiPersonaSelect.options.length) return;
  els.aiPersonaSelect.innerHTML = personas
    .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`)
    .join("");
}

function renderAiModels(models = [], selected = "") {
  const unique = [];
  for (const model of [...models, selected].filter(Boolean)) {
    if (!unique.includes(model)) unique.push(model);
  }
  els.aiModelSelect.innerHTML = unique.length
    ? `<option value="">选择模型</option>` + unique.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("")
    : `<option value="">先获取模型</option>`;
  if (selected) {
    els.aiModelSelect.value = selected;
  }
}

function syncAiInputs(status) {
  renderAiPersonas(status.personas || []);
  els.aiBaseUrlInput.value = status.base_url || DEFAULT_AI_BASE_URL;
  els.aiKeyInput.value = "";
  els.aiKeyInput.placeholder = status.api_key_masked ? `已保存 ${status.api_key_masked}，留空不修改` : "API Key";
  renderAiModels(state.ai.models, status.selected_model || "");
  els.aiPersonaSelect.value = status.persona_id || "researcher";
  els.aiCustomPersonaInput.value = status.custom_persona || "";
  els.aiCustomPersonaWrap.hidden = els.aiPersonaSelect.value !== "custom";
  updateAiHttpWarning();
}

function aiPersonaLabel(status) {
  const persona = (status?.personas || []).find((item) => item.id === status?.persona_id);
  return persona?.label || status?.persona_id || "默认研究员";
}

function hasSavedAiApi(status) {
  return Boolean(status?.api_key_masked);
}

function renderAiConfigSummary(status) {
  els.aiConfigSummaryTitle.textContent = status.selected_model || (hasSavedAiApi(status) ? "API 已保存，待选择模型" : "AI 已配置");
  const parts = [
    status.base_url || DEFAULT_AI_BASE_URL,
    aiPersonaLabel(status),
    status.api_key_masked ? `Key ${status.api_key_masked}` : "Key 已保存",
  ];
  els.aiConfigSummaryMeta.textContent = parts.filter(Boolean).join(" · ");
}

function aiCompactStatus(status) {
  if (!status) return "检查配置中";
  if (!status.enabled) return "可选 AI 评价：服务器未启用";
  if (!status.authenticated) return "可选 AI 评价：需验证后使用";
  if (!hasSavedAiApi(status)) return "可选 AI 评价：待保存 API";
  if (!status.configured) return "可选 AI 评价：待配置模型";
  return `可选 AI 评价：${status.selected_model || "已配置"}`;
}

function renderAiPanel() {
  const status = state.ai.status;
  const busy = state.ai.loading;
  if (status?.enabled && !status.authenticated && state.ai.expanded) {
    state.ai.expanded = false;
  }
  const expanded = Boolean(state.ai.expanded);
  els.aiPanel.classList.toggle("ai-expanded", expanded);
  els.aiPanel.classList.toggle("ai-collapsed", !expanded);
  els.aiEnableToggle.checked = expanded;
  els.aiBody.hidden = true;
  els.aiPanel.classList.remove("ai-has-body");
  els.aiGenerateBtn.hidden = !expanded || !status?.authenticated || !status.configured;
  els.aiGenerateBtn.disabled = busy || !state.result || !status?.enabled || !status.authenticated || !status.configured;
  els.aiModifyApiBtn.hidden = true;
  els.aiDisabled.hidden = true;
  els.aiConfigSummary.hidden = true;
  els.aiConfig.hidden = true;
  els.aiApiFields.hidden = true;
  els.aiModelFields.hidden = true;
  els.aiSaveFetchBtn.disabled = busy;
  els.aiFetchModelsBtn.disabled = busy || !status?.authenticated || !hasSavedAiApi(status);
  els.aiSaveConfigBtn.disabled = busy;
  els.aiGenerateBtn.textContent = busy ? "处理中" : "生成评价";

  if (!expanded) {
    els.aiStatusText.textContent = aiCompactStatus(status);
    return;
  }

  if (!status) {
    els.aiStatusText.textContent = "检查配置中";
  } else if (!status.enabled) {
    els.aiStatusText.textContent = "未启用";
    els.aiDisabled.hidden = false;
  } else if (!status.authenticated) {
    els.aiStatusText.textContent = aiCompactStatus(status);
    els.aiGenerateBtn.hidden = true;
  } else {
    const hasApi = hasSavedAiApi(status);
    if (!hasApi) {
      state.ai.editingConfig = true;
    }
    els.aiModifyApiBtn.hidden = !hasApi || state.ai.editingConfig;
    if (hasApi && !status.configured && !state.ai.editingConfig) {
      els.aiConfigSummary.hidden = false;
      renderAiConfigSummary(status);
    }
    els.aiConfig.hidden = false;
    els.aiModelFields.hidden = false;
    if (status.configured && !state.ai.editingConfig) {
      els.aiApiFields.hidden = true;
      els.aiStatusText.textContent = `${status.selected_model || "AI"} · 已配置`;
    } else {
      els.aiApiFields.hidden = hasApi && !state.ai.editingConfig;
      els.aiStatusText.textContent = !hasApi ? "待保存 API" : status.configured ? "修改API设置" : "待选择模型";
    }
  }

  if (state.ai.commentary) {
    els.aiOutput.classList.remove("empty");
    els.aiOutput.hidden = false;
    els.aiOutput.textContent = state.ai.commentary.commentary || "";
  } else {
    els.aiOutput.classList.add("empty");
    els.aiOutput.hidden = true;
    els.aiOutput.textContent = state.result ? "可基于当前规则结果生成 AI 评价" : "完成基金对比后，可生成 AI 评价";
  }

  const hasBodyContent =
    expanded &&
    (!els.aiDisabled.hidden || !els.aiConfigSummary.hidden || !els.aiConfig.hidden || !els.aiError.hidden || !els.aiOutput.hidden);
  els.aiBody.hidden = !hasBodyContent;
  els.aiPanel.classList.toggle("ai-has-body", hasBodyContent);
}

async function loadAiStatus() {
  try {
    const status = await api("/api/compare/ai/status");
    state.ai.status = status;
    if (!status.authenticated) {
      state.ai.editingConfig = false;
    } else {
      state.ai.editingConfig = !hasSavedAiApi(status);
    }
    syncAiInputs(status);
    setAiError("");
  } catch (error) {
    setAiError(error.message);
  } finally {
    renderAiPanel();
  }
}

function collectAiConfigPayload() {
  const payload = {
    base_url: els.aiBaseUrlInput.value.trim() || DEFAULT_AI_BASE_URL,
    selected_model: els.aiModelSelect.value || null,
    persona_id: els.aiPersonaSelect.value || "researcher",
    custom_persona: els.aiCustomPersonaInput.value.trim() || null,
  };
  const key = els.aiKeyInput.value.trim();
  if (key) {
    payload.api_key = key;
  }
  return payload;
}

async function saveAiConfig(fetchAfterSave = false) {
  state.ai.loading = true;
  renderAiPanel();
  setAiError("");
  try {
    const status = await api("/api/compare/ai/config", {
      method: "PUT",
      body: JSON.stringify(collectAiConfigPayload()),
    });
    state.ai.status = status;
    state.ai.editingConfig = !hasSavedAiApi(status);
    syncAiInputs(status);
    if (fetchAfterSave) {
      const data = await api("/api/compare/ai/models");
      state.ai.models = data.models || [];
      renderAiModels(state.ai.models, state.ai.status.selected_model || "");
    }
  } catch (error) {
    setAiError(error.message);
  } finally {
    state.ai.loading = false;
    renderAiPanel();
  }
}

async function fetchAiModels() {
  state.ai.loading = true;
  renderAiPanel();
  setAiError("");
  try {
    const data = await api("/api/compare/ai/models");
    state.ai.models = data.models || [];
    renderAiModels(state.ai.models, state.ai.status?.selected_model || "");
  } catch (error) {
    setAiError(error.message);
  } finally {
    state.ai.loading = false;
    renderAiPanel();
  }
}

async function loginAi(event) {
  event.preventDefault();
  const password = els.aiPasswordInput.value.trim();
  if (!password) {
    cancelAiEnable();
    return;
  }
  state.ai.loading = true;
  renderAiPanel();
  setAiError("");
  try {
    const status = await api("/api/compare/ai/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    closeAiPasswordModal();
    state.ai.status = status;
    state.ai.expanded = true;
    state.ai.editingConfig = !hasSavedAiApi(status);
    syncAiInputs(status);
    setAiError("");
  } catch (error) {
    setAiPasswordError(error.message);
  } finally {
    state.ai.loading = false;
    renderAiPanel();
  }
}

async function generateAiCommentary() {
  if (!state.result) {
    setAiError("请先完成基金对比");
    return;
  }
  state.ai.loading = true;
  state.ai.commentary = null;
  renderAiPanel();
  setAiError("");
  try {
    state.ai.commentary = await api("/api/compare/ai/commentary", {
      method: "POST",
      body: JSON.stringify({ compare_result: state.result }),
    });
  } catch (error) {
    setAiError(error.message);
  } finally {
    state.ai.loading = false;
    renderAiPanel();
  }
}

function renderSelection() {
  els.selectionStatus.textContent = `已选择 ${state.selected.length}/4`;
  if (!state.selected.length) {
    els.selectedFunds.innerHTML = `<div class="empty">暂无候选基金</div>`;
  } else {
    els.selectedFunds.innerHTML = state.selected
      .map(
        (item) => `
          <span class="selected-chip">
            <strong>${escapeHtml(item.name || item.code)}</strong>
            <small>${escapeHtml(item.code)}</small>
            <button class="remove-chip" type="button" data-remove="${item.code}" title="移除" aria-label="移除">×</button>
          </span>`
      )
      .join("");
  }
  els.compareBtn.disabled = state.loading || state.selected.length < 2;
}

function addFund(item) {
  const code = String(item.code || "").trim();
  if (!/^\d{6}$/.test(code)) {
    showErrors(["请输入6位基金代码"]);
    return;
  }
  if (state.selected.some((entry) => entry.code === code)) {
    showErrors(["这只基金已经在候选列表中"]);
    return;
  }
  if (state.selected.length >= 4) {
    showErrors(["最多同时对比4只基金"]);
    return;
  }
  state.selected.push({
    code,
    name: item.name || code,
    fund_type: item.fund_type || null,
  });
  els.searchInput.value = "";
  els.searchResults.classList.remove("active");
  showErrors([]);
  renderSelection();
}

async function addCode(code) {
  const normalized = String(code || "").trim();
  if (!/^\d{6}$/.test(normalized)) {
    showErrors(["请输入6位基金代码"]);
    return;
  }
  try {
    const results = await api(`/api/funds/search?q=${encodeURIComponent(normalized)}`);
    const exact = results.find((item) => item.code === normalized);
    addFund(exact || { code: normalized, name: normalized });
  } catch (error) {
    showErrors([error.message]);
  }
}

function removeFund(code) {
  state.selected = state.selected.filter((item) => item.code !== code);
  state.result = null;
  renderSelection();
  renderEmptyResult();
}

async function runSearch(query) {
  const q = query.trim();
  if (!q) {
    els.searchResults.classList.remove("active");
    els.searchResults.innerHTML = "";
    return;
  }
  const results = await api(`/api/funds/search?q=${encodeURIComponent(q)}`);
  els.searchResults.innerHTML = results.length
    ? results
        .map(
          (item) => `
            <button class="search-result" type="button" data-add="${item.code}" data-name="${escapeHtml(item.name)}" data-type="${escapeHtml(item.fund_type || "")}">
              <span>${escapeHtml(item.name)}</span>
              <strong>${item.code}</strong>
            </button>`
        )
        .join("")
    : `<div class="empty">未找到匹配基金</div>`;
  els.searchResults.classList.add("active");
}

async function compareFunds() {
  if (state.selected.length < 2) {
    showErrors(["至少选择2只基金"]);
    return;
  }
  state.loading = true;
  els.compareBtn.textContent = "对比中";
  renderSelection();
  showErrors([]);
  try {
    const payload = {
      codes: state.selected.map((item) => item.code),
      strategy: state.strategy,
      theme_hint: els.themeInput.value.trim() || null,
    };
    state.result = await api("/api/compare", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.ai.commentary = null;
    updateSelectedNames(state.result.funds);
    renderResult();
  } catch (error) {
    showErrors([error.message]);
  } finally {
    state.loading = false;
    els.compareBtn.textContent = "开始对比";
    renderSelection();
  }
}

function updateSelectedNames(funds) {
  const names = new Map((funds || []).map((item) => [item.code, item.name]));
  state.selected = state.selected.map((item) => ({
    ...item,
    name: names.get(item.code) || item.name,
  }));
  renderSelection();
}

function renderEmptyResult() {
  state.ai.commentary = null;
  els.resultSummary.hidden = true;
  els.themePanel.hidden = true;
  els.themeMeta.textContent = "等待结果";
  els.themeSummary.textContent = "";
  els.themeGrid.innerHTML = "";
  els.rankingMeta.textContent = "等待结果";
  els.scoreMeta.textContent = "等待结果";
  els.pairMeta.textContent = "等待结果";
  els.rankingRows.innerHTML = `<tr><td colspan="8" class="empty">输入 2-4 只基金后开始对比</td></tr>`;
  els.fundCards.innerHTML = `<div class="empty">输入 2-4 只基金后开始对比</div>`;
  els.scoreDetails.innerHTML = `<div class="empty">暂无分项评分</div>`;
  els.pairGrid.innerHTML = `<div class="empty">暂无相似度结果</div>`;
  els.warningPanel.hidden = true;
  els.warningList.innerHTML = "";
  renderMethodology();
  renderAiPanel();
}

function renderResult() {
  const result = state.result;
  if (!result) {
    renderEmptyResult();
    return;
  }
  els.resultSummary.hidden = false;
  els.conclusionBadge.textContent = CONCLUSION_LABELS[result.conclusion] || result.conclusion;
  els.conclusionBadge.className = `conclusion-badge ${result.conclusion}`;
  els.conclusionTitle.textContent = result.conclusion_title;
  els.recommendationText.textContent = result.recommendation;
  els.rankingMeta.textContent = `${STRATEGY_LABELS[result.strategy]} · ${result.funds.length}只`;
  els.scoreMeta.textContent = STRATEGY_LABELS[result.strategy];
  els.pairMeta.textContent = `${result.pair_similarities.length}组`;
  renderRanking(result);
  renderScoreDetails(result);
  renderMethodology(result.score_factors);
  renderThemeAnalysis(result.theme_analysis);
  renderPairs(result);
  renderWarnings(result);
  renderAiPanel();
}

function renderThemeAnalysis(themeAnalysis) {
  if (!themeAnalysis || !themeAnalysis.exposures?.length) {
    els.themePanel.hidden = true;
    els.themeMeta.textContent = "未识别";
    els.themeSummary.textContent = "";
    els.themeGrid.innerHTML = "";
    return;
  }
  els.themePanel.hidden = false;
  els.themeMeta.textContent = themeAnalysis.theme_hint ? `目标：${themeAnalysis.theme_hint}` : "未填写目标板块";
  els.themeSummary.textContent = themeAnalysis.summary || "";
  els.themeGrid.innerHTML = themeAnalysis.exposures
    .map((item) => {
      const level = item.match_level || "unknown";
      const themes = item.inferred_themes?.length ? item.inferred_themes.join("、") : "暂无明确线索";
      return `
        <article class="theme-item ${level}">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <span>${escapeHtml(THEME_MATCH_LABELS[level] || level)}</span>
          </div>
          <small>${escapeHtml(item.code)} · ${escapeHtml(themes)}</small>
          <p>${escapeHtml(item.comment || "")}</p>
        </article>`;
    })
    .join("");
}

function renderRanking(result) {
  if (!result.funds.length) {
    els.rankingRows.innerHTML = `<tr><td colspan="8" class="empty">暂无评分结果</td></tr>`;
    els.fundCards.innerHTML = `<div class="empty">暂无评分结果</div>`;
    return;
  }
  els.rankingRows.innerHTML = result.funds.map(rowHtml).join("");
  els.fundCards.innerHTML = result.funds.map(cardHtml).join("");
}

function rowHtml(item) {
  const snapshot = item.snapshot || {};
  const recommend = item.recommended ? `<span class="recommend-badge">推荐</span>` : "";
  return `
    <tr>
      <td>
        <div class="fund-name">
          <strong>${item.rank || "--"}. ${escapeHtml(item.name)} ${recommend}</strong>
          <small>${item.code} · ${escapeHtml(item.fund_type || "--")}</small>
        </div>
      </td>
      <td><span class="score-value">${fmt(item.total_score, 2)}</span></td>
      <td class="${clsForPct(snapshot.one_year_pct || 0)}">${fmtPct(snapshot.one_year_pct)}<br><small>近1年</small></td>
      <td>${fmt(snapshot.current_rate_pct, 2)}%</td>
      <td>${fmtLimit(snapshot.purchase_limit_yuan)}</td>
      <td>${fmt(snapshot.scale_billion, 2)}亿</td>
      <td>${rankText(snapshot)}</td>
      <td>${fmt(snapshot.top10_weight_sum, 2)}%</td>
    </tr>`;
}

function cardHtml(item) {
  const snapshot = item.snapshot || {};
  const recommend = item.recommended ? `<span class="recommend-badge">推荐</span>` : "";
  return `
    <article class="fund-card">
      <div class="fund-card-head">
        <div>
          <strong>${item.rank || "--"}. ${escapeHtml(item.name)}</strong>
          <small>${item.code} · ${escapeHtml(item.fund_type || "--")}</small>
        </div>
        ${recommend}
      </div>
      <div class="card-metrics">
        <div><span>综合分</span><strong>${fmt(item.total_score, 2)}</strong></div>
        <div><span>近1年</span><strong class="${clsForPct(snapshot.one_year_pct || 0)}">${fmtPct(snapshot.one_year_pct)}</strong></div>
        <div><span>费率</span><strong>${fmt(snapshot.current_rate_pct, 2)}%</strong></div>
        <div><span>限购</span><strong>${fmtLimit(snapshot.purchase_limit_yuan)}</strong></div>
        <div><span>规模</span><strong>${fmt(snapshot.scale_billion, 2)}亿</strong></div>
      </div>
    </article>`;
}

function renderScoreDetails(result) {
  els.scoreDetails.innerHTML = result.funds
    .map(
      (item) => `
        <article class="score-card">
          <div class="score-card-head">
            <div>
              <strong>${escapeHtml(item.name)}</strong>
              <small>${item.code} · 综合分 ${fmt(item.total_score, 2)}</small>
            </div>
            ${item.recommended ? `<span class="recommend-badge">推荐</span>` : ""}
          </div>
          <div class="score-bars">${scoreBarsHtml(item.score_breakdown)}</div>
          <ul class="reason-list">${item.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
        </article>`
    )
    .join("");
}

function scoreBarsHtml(breakdown) {
  return Object.entries(SCORE_LABELS)
    .map(([key, label]) => {
      const value = Number(breakdown[key] ?? 0);
      return `
        <div class="score-bar">
          <span>${label}</span>
          <div class="bar-track"><i style="width: ${Math.max(0, Math.min(100, value))}%"></i></div>
          <strong>${fmt(value, 0)}</strong>
        </div>`;
    })
    .join("");
}

function renderPairs(result) {
  if (!result.pair_similarities.length) {
    els.pairGrid.innerHTML = `<div class="empty">暂无相似度结果</div>`;
    return;
  }
  const fundNames = new Map(result.funds.map((item) => [item.code, item.name]));
  els.pairGrid.innerHTML = result.pair_similarities
    .map((pair) => {
      const left = fundNames.get(pair.code_a) || pair.code_a;
      const right = fundNames.get(pair.code_b) || pair.code_b;
      return `
        <article class="pair-item">
          <div class="pair-head">
            <div>
              <strong>${escapeHtml(left)} / ${escapeHtml(right)}</strong>
              <small>${pair.code_a} · ${pair.code_b}</small>
            </div>
            <span class="relation-badge ${pair.relation}">${CONCLUSION_LABELS[pair.relation] || pair.relation}</span>
          </div>
          <div class="pair-metrics">
            <div><span>综合相似</span><strong>${fmt(pair.overall_similarity, 2)}%</strong></div>
            <div><span>持仓相似</span><strong>${fmt(pair.holdings_similarity, 2)}%</strong></div>
            <div><span>产品画像</span><strong>${fmt(pair.profile_similarity, 2)}%</strong></div>
            <div><span>主题接近</span><strong>${fmt(pair.theme_similarity, 2)}%</strong></div>
          </div>
          <ul class="pair-reasons">${pair.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
        </article>`;
    })
    .join("");
}

function renderWarnings(result) {
  const warnings = result.warnings || [];
  if (!warnings.length) {
    els.warningPanel.hidden = true;
    els.warningList.innerHTML = "";
    return;
  }
  els.warningPanel.hidden = false;
  els.warningList.innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
}

let searchTimer = null;

els.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = els.searchInput.value.trim();
  if (/^\d{6}$/.test(value)) {
    addCode(value);
    return;
  }
  runSearch(value).catch((error) => showErrors([error.message]));
});

els.searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    runSearch(els.searchInput.value).catch((error) => showErrors([error.message]));
  }, 250);
});

els.addTypedBtn.addEventListener("click", () => {
  addCode(els.searchInput.value.trim());
});

els.searchResults.addEventListener("click", (event) => {
  const target = event.target.closest("[data-add]");
  if (!target) return;
  addFund({
    code: target.dataset.add,
    name: target.dataset.name,
    fund_type: target.dataset.type,
  });
});

els.selectedFunds.addEventListener("click", (event) => {
  const target = event.target.closest("[data-remove]");
  if (!target) return;
  removeFund(target.dataset.remove);
});

for (const tab of els.strategyTabs) {
  tab.addEventListener("click", () => {
    state.strategy = tab.dataset.strategy || "balanced";
    for (const item of els.strategyTabs) {
      item.classList.toggle("active", item.dataset.strategy === state.strategy);
    }
    if (state.result && state.selected.length >= 2) {
      compareFunds();
    } else {
      renderMethodology();
    }
  });
}

els.compareBtn.addEventListener("click", () => {
  compareFunds();
});

els.aiEnableToggle.addEventListener("change", () => {
  const checked = els.aiEnableToggle.checked;
  if (!checked) {
    state.ai.expanded = false;
    closeAiPasswordModal();
    renderAiPanel();
    return;
  }
  if (state.ai.status?.enabled && !state.ai.status.authenticated) {
    state.ai.expanded = false;
    renderAiPanel();
    openAiPasswordModal();
    return;
  }
  state.ai.expanded = true;
  renderAiPanel();
});

els.aiPasswordCloseBtn.addEventListener("click", () => {
  cancelAiEnable();
});

els.aiPasswordCancelBtn.addEventListener("click", () => {
  cancelAiEnable();
});

els.aiLoginForm.addEventListener("submit", loginAi);

els.aiPasswordModal.addEventListener("click", (event) => {
  if (event.target === els.aiPasswordModal) {
    cancelAiEnable();
  }
});

els.aiModifyApiBtn.addEventListener("click", () => {
  state.ai.editingConfig = true;
  renderAiPanel();
});

els.aiBaseUrlInput.addEventListener("input", updateAiHttpWarning);

els.aiPersonaSelect.addEventListener("change", () => {
  els.aiCustomPersonaWrap.hidden = els.aiPersonaSelect.value !== "custom";
});

els.aiSaveFetchBtn.addEventListener("click", () => {
  saveAiConfig(true);
});

els.aiFetchModelsBtn.addEventListener("click", () => {
  fetchAiModels();
});

els.aiSaveConfigBtn.addEventListener("click", () => {
  saveAiConfig(false);
});

els.aiGenerateBtn.addEventListener("click", () => {
  generateAiCommentary();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".search")) {
    els.searchResults.classList.remove("active");
  }
});

loadSourceStatus();
loadAiStatus();
renderSelection();
renderEmptyResult();

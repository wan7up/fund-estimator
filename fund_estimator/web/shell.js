const TOOLS = {
  estimate: {
    path: "/estimate",
    title: "实时估值",
  },
  compare: {
    path: "/compare",
    title: "基金对比",
  },
  arbitrage: {
    path: "/arbitrage",
    title: "套利提醒",
  },
  "ai-chat": {
    path: "/ai-chat",
    title: "AI咨询",
  },
};

const DEFAULT_TOOL = "estimate";
const tabs = Array.from(document.querySelectorAll("[data-tool]"));
const panes = Array.from(document.querySelectorAll("[data-pane]"));

function toolFromLocation() {
  const hashTool = window.location.hash.replace(/^#/, "");
  if (TOOLS[hashTool]) return hashTool;

  const path = window.location.pathname.replace(/^\/+/, "").split("/")[0];
  if (path === "monitor") return "arbitrage";
  if (TOOLS[path]) return path;
  return DEFAULT_TOOL;
}

function ensureIframeLoaded(tool) {
  const pane = document.querySelector(`[data-pane="${tool}"]`);
  const iframe = pane?.querySelector("iframe");
  if (iframe && !iframe.src) {
    iframe.src = iframe.dataset.src;
  }
}

function activateTool(tool, options = {}) {
  const nextTool = TOOLS[tool] ? tool : DEFAULT_TOOL;

  for (const tab of tabs) {
    const active = tab.dataset.tool === nextTool;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-current", active ? "page" : "false");
  }

  for (const pane of panes) {
    pane.classList.toggle("active", pane.dataset.pane === nextTool);
  }

  ensureIframeLoaded(nextTool);
  document.title = `${TOOLS[nextTool].title} - 基金工具箱`;

  if (options.updateUrl !== false) {
    const target = TOOLS[nextTool].path;
    if (window.location.pathname !== target) {
      window.history.pushState({ tool: nextTool }, "", target);
    }
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => activateTool(tab.dataset.tool));
}

window.addEventListener("popstate", () => activateTool(toolFromLocation(), { updateUrl: false }));

activateTool(toolFromLocation(), { updateUrl: false });

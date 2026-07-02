from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyLeg:
    symbol: str
    weight: float
    label: str


@dataclass(frozen=True)
class CoreLof:
    code: str
    theme: str
    proxies: tuple[ProxyLeg, ...]


CORE_CROSS_BORDER_LOFS: tuple[CoreLof, ...] = (
    CoreLof("161128", "US technology", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
    CoreLof("161130", "Nasdaq 100", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
    CoreLof("160213", "Nasdaq 100", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
    CoreLof("161125", "S&P 500", (ProxyLeg("ES=F", 1.0, "S&P 500 futures"),)),
    CoreLof("050025", "S&P 500", (ProxyLeg("ES=F", 1.0, "S&P 500 futures"),)),
    CoreLof("160140", "US REIT", (ProxyLeg("VNQ", 1.0, "US REIT ETF"),)),
    CoreLof("164906", "China internet", (ProxyLeg("KWEB", 0.7, "China internet ETF"), ProxyLeg("^HSI", 0.3, "Hang Seng Index"))),
    CoreLof("160644", "Hong Kong and US internet", (ProxyLeg("KWEB", 0.6, "China internet ETF"), ProxyLeg("NQ=F", 0.3, "Nasdaq 100 futures"), ProxyLeg("^HSI", 0.1, "Hang Seng Index"))),
    CoreLof("160717", "H-share index", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("161831", "H-share index", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("160924", "H-share index", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("164705", "India", (ProxyLeg("^NSEI", 1.0, "Nifty 50 Index"),)),
    CoreLof("164824", "India", (ProxyLeg("^NSEI", 1.0, "Nifty 50 Index"),)),
    CoreLof("160416", "Oil", (ProxyLeg("CL=F", 1.0, "WTI crude futures"),)),
    CoreLof("162411", "Oil and gas", (ProxyLeg("XOP", 0.7, "US oil & gas ETF"), ProxyLeg("CL=F", 0.3, "WTI crude futures"))),
    CoreLof("160216", "Commodity", (ProxyLeg("CL=F", 0.6, "WTI crude futures"), ProxyLeg("GC=F", 0.4, "Gold futures"))),
    CoreLof("160723", "Oil", (ProxyLeg("CL=F", 1.0, "WTI crude futures"),)),
    CoreLof("161129", "Oil", (ProxyLeg("CL=F", 1.0, "WTI crude futures"),)),
    CoreLof("501018", "Oil", (ProxyLeg("CL=F", 1.0, "WTI crude futures"),)),
    CoreLof("501021", "Hong Kong", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("501025", "Hong Kong", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("501300", "Hong Kong", (ProxyLeg("^HSI", 1.0, "Hang Seng Index"),)),
    CoreLof("501310", "Overseas China", (ProxyLeg("^HSI", 0.5, "Hang Seng Index"), ProxyLeg("KWEB", 0.5, "China internet ETF"))),
    CoreLof("501312", "Overseas technology", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
)

CORE_LOF_BY_CODE = {item.code: item for item in CORE_CROSS_BORDER_LOFS}

DOMESTIC_LOF_PROXY_RULES: tuple[CoreLof, ...] = (
    CoreLof("domestic-tech-board", "科创/创新主题", (ProxyLeg("SINA:sh588000", 1.0, "科创50ETF"),)),
    CoreLof("domestic-securities", "证券公司", (ProxyLeg("SINA:sh512880", 1.0, "证券ETF"),)),
    CoreLof("domestic-ai", "人工智能", (ProxyLeg("SINA:sz159819", 1.0, "人工智能ETF"),)),
    CoreLof("domestic-nonferrous", "有色金属", (ProxyLeg("SINA:sh512400", 1.0, "有色金属ETF"),)),
    CoreLof("domestic-baijiu", "白酒", (ProxyLeg("SINA:sh512690", 1.0, "酒ETF"),)),
    CoreLof("domestic-military", "军工", (ProxyLeg("SINA:sh512660", 1.0, "军工ETF"),)),
)

DOMESTIC_LOF_PROXY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("科创", "科技创新", "创新未来", "新兴产业", "科技创新股票"), "domestic-tech-board"),
    (("证券", "券商"), "domestic-securities"),
    (("人工智能", "AI"), "domestic-ai"),
    (("有色", "有色金属"), "domestic-nonferrous"),
    (("白酒", "酒"), "domestic-baijiu"),
    (("军工",), "domestic-military"),
)

DOMESTIC_LOF_PROXY_BY_RULE = {item.code: item for item in DOMESTIC_LOF_PROXY_RULES}


def infer_domestic_lof_proxy(name: str | None, fund_type: str | None = None) -> CoreLof | None:
    text = f"{name or ''} {fund_type or ''}".upper()
    if "QDII" in text:
        return None
    for keywords, rule_code in DOMESTIC_LOF_PROXY_KEYWORDS:
        if any(keyword.upper() in text for keyword in keywords):
            return DOMESTIC_LOF_PROXY_BY_RULE.get(rule_code)
    return None

LOF_CODE_PREFIXES = ("16", "501", "502", "503", "505", "506")
CLOSED_FUND_KEYWORDS = ("定开", "封闭", "封闭式", "持有期", "战略配售")


def looks_like_lof_code(code: str) -> bool:
    normalized = str(code or "").strip()
    return normalized.isdigit() and len(normalized) == 6 and normalized.startswith(LOF_CODE_PREFIXES)


def looks_like_lof_name(name: str | None) -> bool:
    text = str(name or "").upper()
    if any(keyword in text for keyword in CLOSED_FUND_KEYWORDS):
        return False
    return "LOF" in text or "QDII" in text or "跨境" in text


def looks_like_lof_fund(code: str, name: str | None, fund_type: str | None = None) -> bool:
    if str(code or "").strip() in CORE_LOF_BY_CODE:
        return True
    text = f"{name or ''} {fund_type or ''}".upper()
    if any(keyword in text for keyword in CLOSED_FUND_KEYWORDS):
        return False
    return looks_like_lof_name(name) or looks_like_lof_name(fund_type)

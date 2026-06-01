from __future__ import annotations

from dataclasses import dataclass

from fund_estimator.services.lof_config import ProxyLeg


@dataclass(frozen=True)
class CoreEtf:
    code: str
    theme: str
    proxies: tuple[ProxyLeg, ...]


CORE_CROSS_BORDER_ETFS: tuple[CoreEtf, ...] = (
    CoreEtf("159605", "China internet", (ProxyLeg("KWEB", 0.7, "China internet ETF"), ProxyLeg("^HSI", 0.3, "Hang Seng Index"))),
    CoreEtf("159607", "China internet", (ProxyLeg("KWEB", 0.7, "China internet ETF"), ProxyLeg("^HSI", 0.3, "Hang Seng Index"))),
    CoreEtf("513050", "China internet", (ProxyLeg("KWEB", 0.7, "China internet ETF"), ProxyLeg("^HSI", 0.3, "Hang Seng Index"))),
    CoreEtf("513330", "Hang Seng internet", (ProxyLeg("KWEB", 0.5, "China internet ETF"), ProxyLeg("^HSI", 0.5, "Hang Seng Index"))),
    CoreEtf("513180", "Hang Seng technology", (ProxyLeg("^HSI", 0.7, "Hang Seng Index"), ProxyLeg("KWEB", 0.3, "China internet ETF"))),
    CoreEtf("513130", "Hang Seng technology", (ProxyLeg("^HSI", 0.7, "Hang Seng Index"), ProxyLeg("KWEB", 0.3, "China internet ETF"))),
    CoreEtf("159941", "Nasdaq 100", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
    CoreEtf("513100", "Nasdaq 100", (ProxyLeg("NQ=F", 1.0, "Nasdaq 100 futures"),)),
    CoreEtf("513500", "S&P 500", (ProxyLeg("ES=F", 1.0, "S&P 500 futures"),)),
    CoreEtf("513400", "Dow Jones", (ProxyLeg("^DJI", 1.0, "Dow Jones Industrial Average"),)),
    CoreEtf("513030", "Germany", (ProxyLeg("^GDAXI", 1.0, "DAX Index"),)),
    CoreEtf("159561", "Germany", (ProxyLeg("^GDAXI", 1.0, "DAX Index"),)),
    CoreEtf("513520", "Japan", (ProxyLeg("^N225", 1.0, "Nikkei 225"),)),
    CoreEtf("513880", "Japan", (ProxyLeg("^N225", 1.0, "Nikkei 225"),)),
    CoreEtf("520580", "Emerging Asia", (ProxyLeg("^HSI", 0.5, "Hang Seng Index"), ProxyLeg("^NSEI", 0.5, "Nifty 50 Index"))),
    CoreEtf("159822", "Emerging Asia", (ProxyLeg("^HSI", 0.5, "Hang Seng Index"), ProxyLeg("^NSEI", 0.5, "Nifty 50 Index"))),
)

CORE_ETF_BY_CODE = {item.code: item for item in CORE_CROSS_BORDER_ETFS}

CROSS_BORDER_ETF_KEYWORDS = (
    "QDII",
    "中概",
    "互联",
    "互联网",
    "海外",
    "港",
    "恒生",
    "纳指",
    "纳斯达克",
    "标普",
    "道琼斯",
    "德国",
    "法国",
    "日经",
    "日本",
    "亚洲",
    "新兴",
    "美国",
    "全球",
    "沙特",
)


def looks_like_cross_border_etf(code: str, name: str | None) -> bool:
    if code in CORE_ETF_BY_CODE:
        return True
    text = str(name or "").upper()
    return any(keyword.upper() in text for keyword in CROSS_BORDER_ETF_KEYWORDS)

from __future__ import annotations

from dataclasses import dataclass

from fund_estimator.models.schema import FundHoldings, FundProfile


THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CPO/通信": (
        "cpo",
        "光模块",
        "光通信",
        "光纤",
        "光芯",
        "通信",
        "新易盛",
        "中际旭创",
        "源杰科技",
        "腾景科技",
        "长飞光纤",
        "亨通光电",
        "中天科技",
        "永鼎股份",
    ),
    "半导体": ("半导体", "芯片", "集成电路", "晶圆", "设备", "材料"),
    "人工智能": ("人工智能", "ai", "算力", "服务器", "数据中心"),
    "互联网": ("互联网", "中概", "港股通互联网", "海外中国互联网", "腾讯", "阿里", "美团"),
    "新能源": ("新能源", "电池", "光伏", "锂电", "储能", "电动车", "新能源汽车"),
    "医药": ("医药", "医疗", "生物", "创新药", "健康"),
    "消费": ("消费", "食品", "饮料", "白酒", "家电"),
    "金融": ("金融", "银行", "证券", "保险"),
    "军工": ("军工", "国防", "航空航天"),
    "红利": ("红利", "股息", "高股息"),
    "油气": ("原油", "油气", "石油", "能源"),
    "黄金": ("黄金", "贵金属"),
    "债券": ("债", "信用债", "利率债", "可转债"),
    "科技": ("科技", "信息技术", "软件", "计算机", "云计算", "电子"),
}


THEME_RELATED: dict[str, set[str]] = {
    "科技": {"半导体", "人工智能", "互联网", "CPO/通信"},
    "半导体": {"科技", "人工智能"},
    "人工智能": {"科技", "半导体", "CPO/通信"},
    "互联网": {"科技"},
    "CPO/通信": {"科技", "人工智能"},
    "新能源": {"科技"},
}


@dataclass(frozen=True)
class ThemeProxyCandidate:
    theme: str
    proxy_code: str
    proxy_name: str
    keywords: tuple[str, ...]
    min_score: int = 3


THEME_PROXY_CANDIDATES: tuple[ThemeProxyCandidate, ...] = (
    # Recent NAV-return checks on 011370 showed 159994/515050 track the fund better than 515880.
    ThemeProxyCandidate("CPO/通信", "159994", "通信ETF银华", THEME_KEYWORDS["CPO/通信"], min_score=2),
    ThemeProxyCandidate("半导体", "512480", "半导体ETF", THEME_KEYWORDS["半导体"]),
    ThemeProxyCandidate("人工智能", "159819", "人工智能ETF", THEME_KEYWORDS["人工智能"]),
    ThemeProxyCandidate("互联网", "513050", "中概互联网ETF", THEME_KEYWORDS["互联网"]),
    ThemeProxyCandidate("新能源", "516160", "新能源ETF", THEME_KEYWORDS["新能源"]),
    ThemeProxyCandidate("医药", "512010", "医药ETF", THEME_KEYWORDS["医药"]),
    ThemeProxyCandidate("消费", "159928", "消费ETF", THEME_KEYWORDS["消费"]),
    ThemeProxyCandidate("金融", "512880", "证券ETF", THEME_KEYWORDS["金融"]),
    ThemeProxyCandidate("军工", "512660", "军工ETF", THEME_KEYWORDS["军工"]),
    ThemeProxyCandidate("红利", "510880", "红利ETF", THEME_KEYWORDS["红利"]),
    ThemeProxyCandidate("黄金", "518880", "黄金ETF", THEME_KEYWORDS["黄金"]),
    ThemeProxyCandidate("科技", "515000", "科技ETF", THEME_KEYWORDS["科技"]),
)


def theme_evidence_text(profile: FundProfile, holdings: FundHoldings | None = None) -> str:
    holdings_text = ""
    if holdings is not None:
        holdings_text = " ".join(item.stock_name for item in holdings.items if item.stock_name)
    return f"{profile.name} {profile.fund_type or ''} {holdings_text}".lower()


def infer_theme_tokens(text: str) -> set[str]:
    lowered = text.lower()
    return {
        theme
        for theme, keywords in THEME_KEYWORDS.items()
        if any(keyword.lower() in lowered for keyword in keywords)
    }


def infer_theme_proxy(profile: FundProfile, holdings: FundHoldings | None = None) -> ThemeProxyCandidate | None:
    name_text = f"{profile.name} {profile.fund_type or ''}".lower()
    holdings_text = ""
    if holdings is not None:
        holdings_text = " ".join(item.stock_name for item in holdings.items if item.stock_name).lower()

    best: tuple[int, int, ThemeProxyCandidate] | None = None
    for candidate in THEME_PROXY_CANDIDATES:
        score = _theme_proxy_score(candidate, name_text=name_text, holdings_text=holdings_text)
        if score < candidate.min_score:
            continue
        # Earlier candidates are more specific when scores tie.
        rank = -THEME_PROXY_CANDIDATES.index(candidate)
        current = (score, rank, candidate)
        if best is None or current > best:
            best = current
    return best[2] if best is not None else None


def _theme_proxy_score(candidate: ThemeProxyCandidate, *, name_text: str, holdings_text: str) -> int:
    score = 0
    matched_keywords: set[str] = set()
    for keyword in candidate.keywords:
        lowered = keyword.lower()
        if lowered in name_text:
            score += 3
            matched_keywords.add(lowered)
        if holdings_text and lowered in holdings_text:
            score += 1
            matched_keywords.add(lowered)
    if len(matched_keywords) >= 3:
        score += 1
    return score

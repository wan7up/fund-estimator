from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fund_estimator.data_sources.eastmoney import DEFAULT_HEADERS, parse_pingzhong_profile
from fund_estimator.services.http_settings import http_trust_env
from scripts.backtest_estimates import NavPoint, fetch_profile_js, parse_nav_points


DEFAULT_CANDIDATES: dict[str, list[str]] = {
    "cpo": ["159994", "515050", "515880", "159507", "159511", "159583", "159695", "515000", "159819"],
    "semiconductor": ["512480", "159995", "512760", "516640", "588200", "515000"],
    "ai": ["159819", "515070", "588400", "515000", "159994"],
}


@dataclass
class ProxyScore:
    code: str
    name: str
    sample_count: int
    mae_pct: float
    correlation: float | None
    avg_actual_pct: float
    avg_proxy_pct: float
    first_date: str
    last_date: str


async def load_nav_series(client: httpx.AsyncClient, code: str) -> tuple[str, dict[date, NavPoint]]:
    text = await fetch_profile_js(client, code)
    profile = parse_pingzhong_profile(code, text)
    points = parse_nav_points(text)
    return profile.name, {point.nav_date: point for point in points}


def correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator_x = sum((x - mean_x) ** 2 for x in xs)
    denominator_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = (denominator_x * denominator_y) ** 0.5
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def score_candidate(
    *,
    code: str,
    name: str,
    fund_points: dict[date, NavPoint],
    proxy_points: dict[date, NavPoint],
    days: int,
    min_samples: int,
) -> ProxyScore | None:
    overlap_dates = sorted(set(fund_points) & set(proxy_points))[-days:]
    if len(overlap_dates) < min_samples:
        return None
    actual = [fund_points[item].actual_change_pct for item in overlap_dates]
    proxy = [proxy_points[item].actual_change_pct for item in overlap_dates]
    errors = [abs(a - p) for a, p in zip(actual, proxy, strict=True)]
    return ProxyScore(
        code=code,
        name=name,
        sample_count=len(overlap_dates),
        mae_pct=round(statistics.fmean(errors), 4),
        correlation=correlation(actual, proxy),
        avg_actual_pct=round(statistics.fmean(actual), 4),
        avg_proxy_pct=round(statistics.fmean(proxy), 4),
        first_date=overlap_dates[0].isoformat(),
        last_date=overlap_dates[-1].isoformat(),
    )


async def select_proxy(
    fund_code: str,
    *,
    candidates: list[str],
    days: int,
    min_samples: int,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0, headers=DEFAULT_HEADERS, trust_env=http_trust_env()) as client:
        fund_name, fund_points = await load_nav_series(client, fund_code)
        scores: list[ProxyScore] = []
        skipped: list[str] = []
        for candidate in candidates:
            try:
                proxy_name, proxy_points = await load_nav_series(client, candidate)
            except Exception as exc:  # pragma: no cover - diagnostic script
                skipped.append(f"{candidate}: {exc}")
                continue
            score = score_candidate(
                code=candidate,
                name=proxy_name,
                fund_points=fund_points,
                proxy_points=proxy_points,
                days=days,
                min_samples=min_samples,
            )
            if score is None:
                skipped.append(f"{candidate}: overlapping samples < {min_samples}")
            else:
                scores.append(score)
    scores.sort(key=lambda item: (item.mae_pct, -(item.correlation or -2), -item.sample_count))
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "days": days,
        "min_samples": min_samples,
        "scores": [asdict(item) for item in scores],
        "best": asdict(scores[0]) if scores else None,
        "skipped": skipped,
        "notes": [
            "使用基金和候选ETF的官方净值日涨跌幅做历史贴合度比较。",
            "MAE越低越好；相关性越高越好；这是选择代理的证据，不代表未来一定最优。",
        ],
    }


def print_report(result: dict[str, Any]) -> None:
    print(f"# {result['fund_name']}（{result['fund_code']}）代理候选比较")
    print(f"最近净值日：{result['days']}，最少重合样本：{result['min_samples']}")
    print("| 排名 | 代码 | 名称 | 样本 | MAE | 相关性 | 候选均涨跌 | 区间 |")
    print("| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for index, row in enumerate(result["scores"], start=1):
        corr = "--" if row["correlation"] is None else f"{row['correlation']:.4f}"
        print(
            f"| {index} | {row['code']} | {row['name']} | {row['sample_count']} | "
            f"{row['mae_pct']:.4f}% | {corr} | {row['avg_proxy_pct']:.4f}% | "
            f"{row['first_date']} 至 {row['last_date']} |"
        )
    if result["skipped"]:
        print("\n跳过：")
        for item in result["skipped"]:
            print(f"- {item}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Select the closest theme ETF proxy by historical NAV returns.")
    parser.add_argument("fund_code", help="target fund code, for example 011370")
    parser.add_argument("--theme", choices=sorted(DEFAULT_CANDIDATES), default="cpo", help="built-in candidate pool")
    parser.add_argument("--candidates", nargs="*", help="override candidate fund/ETF codes")
    parser.add_argument("--days", type=int, default=20, help="recent overlapping official NAV dates")
    parser.add_argument("--min-samples", type=int, default=10, help="minimum overlapping dates")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args()

    candidates = args.candidates or DEFAULT_CANDIDATES[args.theme]
    result = await select_proxy(
        args.fund_code,
        candidates=candidates,
        days=args.days,
        min_samples=args.min_samples,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    asyncio.run(main())

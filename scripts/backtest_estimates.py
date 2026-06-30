from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fund_estimator.data_sources.eastmoney import (
    DEFAULT_HEADERS,
    EastmoneyHoldingsDataSource,
    _decode_utf8_response,
    _extract_json_var,
    infer_market,
    parse_pingzhong_profile,
    to_eastmoney_secid,
)
from fund_estimator.models.schema import FundHoldings, FundProfile
from fund_estimator.services.http_settings import http_trust_env
from fund_estimator.services.theme_proxy import infer_theme_proxy


@dataclass
class NavPoint:
    nav_date: date
    nav: float
    actual_change_pct: float


@dataclass
class BacktestRow:
    code: str
    name: str
    nav_date: str
    actual_change_pct: float
    raw_pct: float
    normalized_pct: float | None
    enhanced_pct: float | None
    raw_error_pct: float
    normalized_error_pct: float | None
    enhanced_error_pct: float | None
    top10_weight_sum: float
    proxy_theme: str | None
    proxy_code: str | None
    proxy_change_pct: float | None


async def fetch_profile_js(client: httpx.AsyncClient, code: str) -> str:
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js?v={int(time.time() * 1000)}"
    response = await client.get(url)
    response.raise_for_status()
    return _decode_utf8_response(response)


def parse_nav_points(text: str) -> list[NavPoint]:
    trend = _extract_json_var(text, "Data_netWorthTrend") or []
    points: list[NavPoint] = []
    previous_nav: float | None = None
    for item in trend:
        try:
            nav_date = datetime.fromtimestamp(int(item["x"]) / 1000).date()
            nav = float(item["y"])
        except (KeyError, TypeError, ValueError, OSError):
            continue
        actual_change = item.get("equityReturn")
        if actual_change in (None, "") and previous_nav:
            actual_change_pct = (nav / previous_nav - 1) * 100
        else:
            try:
                actual_change_pct = float(actual_change)
            except (TypeError, ValueError):
                previous_nav = nav
                continue
        points.append(NavPoint(nav_date=nav_date, nav=nav, actual_change_pct=round(actual_change_pct, 4)))
        previous_nav = nav
    return points


async def fetch_kline_pct(
    client: httpx.AsyncClient,
    code: str,
    *,
    begin: date,
    end: date,
) -> dict[date, float]:
    secid = to_eastmoney_secid(code)
    if not secid:
        return {}
    params = {
        "secid": secid,
        "klt": "101",
        "fqt": "1",
        "beg": begin.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "_": str(int(time.time() * 1000)),
    }
    response = await client.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params=params)
    response.raise_for_status()
    rows = (response.json().get("data") or {}).get("klines") or []
    parsed: dict[date, float] = {}
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 9:
            continue
        try:
            parsed[date.fromisoformat(parts[0])] = float(parts[8])
        except ValueError:
            continue
    return parsed


async def load_backtest_inputs(
    code: str,
    *,
    days: int,
) -> tuple[FundProfile, FundHoldings, list[NavPoint], dict[str, dict[date, float]], str | None]:
    async with httpx.AsyncClient(timeout=10.0, headers=DEFAULT_HEADERS, trust_env=http_trust_env()) as client:
        profile_text = await fetch_profile_js(client, code)
        profile = parse_pingzhong_profile(code, profile_text)
        nav_points = parse_nav_points(profile_text)
        if not nav_points:
            raise RuntimeError(f"{code} no nav trend")
        holdings = await EastmoneyHoldingsDataSource(timeout=10.0).get_holdings(code)
        selected_navs = [point for point in nav_points if point.nav_date >= holdings.holdings_date][-days:]
        if not selected_navs:
            selected_navs = nav_points[-days:]
        begin = selected_navs[0].nav_date - timedelta(days=7)
        end = selected_navs[-1].nav_date + timedelta(days=1)
        codes = [item.stock_code for item in holdings.items if infer_market(item.stock_code) in {"SH", "SZ", "BJ"}]
        proxy = infer_theme_proxy(profile, holdings)
        proxy_code = proxy.proxy_code if proxy else None
        if proxy_code:
            codes.append(proxy_code)
        kline_maps: dict[str, dict[date, float]] = {}
        for stock_code in sorted(set(codes)):
            kline_maps[stock_code] = await fetch_kline_pct(client, stock_code, begin=begin, end=end)
        return profile, holdings, selected_navs, kline_maps, proxy_code


def estimate_row(
    profile: FundProfile,
    holdings: FundHoldings,
    point: NavPoint,
    kline_maps: dict[str, dict[date, float]],
    proxy_code: str | None,
) -> BacktestRow | None:
    raw_pct = 0.0
    usable_weight = 0.0
    for item in holdings.items:
        pct = kline_maps.get(item.stock_code, {}).get(point.nav_date)
        if pct is None:
            continue
        raw_pct += item.weight_pct * pct / 100
        usable_weight += item.weight_pct
    if usable_weight <= 0:
        return None

    normalized_pct = raw_pct / (usable_weight / 100) if usable_weight > 0 else None
    proxy_theme = None
    proxy_change_pct = None
    enhanced_pct = None
    proxy = infer_theme_proxy(profile, holdings)
    if proxy and proxy_code:
        proxy_theme = proxy.theme
        proxy_change_pct = kline_maps.get(proxy_code, {}).get(point.nav_date)
        stock_pct = profile.details.asset_allocation.stock_pct
        if stock_pct is not None and proxy_change_pct is not None:
            residual_stock_weight = max(0.0, min(float(stock_pct), 100.0) - usable_weight)
            if residual_stock_weight >= 1:
                enhanced_pct = raw_pct + residual_stock_weight * proxy_change_pct / 100

    def err(value: float | None) -> float | None:
        return round(abs(value - point.actual_change_pct), 4) if value is not None else None

    return BacktestRow(
        code=profile.code,
        name=profile.name,
        nav_date=point.nav_date.isoformat(),
        actual_change_pct=point.actual_change_pct,
        raw_pct=round(raw_pct, 4),
        normalized_pct=round(normalized_pct, 4) if normalized_pct is not None else None,
        enhanced_pct=round(enhanced_pct, 4) if enhanced_pct is not None else None,
        raw_error_pct=round(abs(raw_pct - point.actual_change_pct), 4),
        normalized_error_pct=err(normalized_pct),
        enhanced_error_pct=err(enhanced_pct),
        top10_weight_sum=holdings.top10_weight_sum,
        proxy_theme=proxy_theme,
        proxy_code=proxy_code,
        proxy_change_pct=round(proxy_change_pct, 4) if proxy_change_pct is not None else None,
    )


def mae(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return round(statistics.fmean(usable), 4)


def best_proxy_alpha(rows: list[BacktestRow]) -> dict[str, float | None]:
    usable = [row for row in rows if row.enhanced_pct is not None]
    if not usable:
        return {"alpha": None, "mae": None}
    best_alpha = 1.0
    best_mae = float("inf")
    for index in range(0, 31):
        alpha = index / 20
        errors = []
        for row in usable:
            proxy_contribution = float(row.enhanced_pct or 0) - row.raw_pct
            predicted = row.raw_pct + alpha * proxy_contribution
            errors.append(abs(predicted - row.actual_change_pct))
        current_mae = statistics.fmean(errors)
        if current_mae < best_mae:
            best_alpha = alpha
            best_mae = current_mae
    return {"alpha": round(best_alpha, 2), "mae": round(best_mae, 4)}


async def backtest_code(code: str, *, days: int) -> dict[str, Any]:
    profile, holdings, nav_points, kline_maps, proxy_code = await load_backtest_inputs(code, days=days)
    rows = [
        row
        for point in nav_points
        if (row := estimate_row(profile, holdings, point, kline_maps, proxy_code)) is not None
    ]
    return {
        "code": code,
        "name": profile.name,
        "holdings_date": holdings.holdings_date.isoformat(),
        "top10_weight_sum": holdings.top10_weight_sum,
        "stock_pct": profile.details.asset_allocation.stock_pct,
        "proxy": asdict(infer_theme_proxy(profile, holdings)) if infer_theme_proxy(profile, holdings) else None,
        "sample_count": len(rows),
        "mae": {
            "raw": mae([row.raw_error_pct for row in rows]),
            "normalized": mae([row.normalized_error_pct for row in rows]),
            "enhanced": mae([row.enhanced_error_pct for row in rows]),
        },
        "best_proxy_alpha": best_proxy_alpha(rows),
        "rows": [asdict(row) for row in rows],
        "notes": [
            "回测使用当前披露持仓回看近期净值，不等同于当时真实持仓。",
            "enhanced=前十大披露权重贡献+剩余股票仓位的关联板块代理涨跌。",
        ],
    }


def print_report(results: list[dict[str, Any]]) -> None:
    def pct_text(value: float | None) -> str:
        return "--" if value is None else f"{value:+.2f}%"

    for result in results:
        proxy = result["proxy"]
        proxy_text = f"{proxy['theme']} / {proxy['proxy_code']}" if proxy else "无"
        print(f"\n## {result['name']}（{result['code']}）")
        print(
            f"持仓日 {result['holdings_date']}，前十占比 {result['top10_weight_sum']:.2f}%"
            f"，股票仓位 {result['stock_pct'] if result['stock_pct'] is not None else '--'}%，关联板块 {proxy_text}"
        )
        print(
            "MAE："
            f"raw={result['mae']['raw'] if result['mae']['raw'] is not None else '--'}%，"
            f"normalized={result['mae']['normalized'] if result['mae']['normalized'] is not None else '--'}%，"
            f"enhanced={result['mae']['enhanced'] if result['mae']['enhanced'] is not None else '--'}%"
        )
        alpha = result["best_proxy_alpha"]
        if alpha["alpha"] is not None:
            print(f"最近样本最优关联板块系数：{alpha['alpha']}，对应 MAE={alpha['mae']}%")
        print("| 日期 | 实际 | raw | normalized | enhanced | 板块代理 |")
        print("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in result["rows"]:
            print(
                f"| {row['nav_date']} | {pct_text(row['actual_change_pct'])} | {pct_text(row['raw_pct'])} | "
                f"{pct_text(row['normalized_pct'])} | {pct_text(row['enhanced_pct'])} | "
                f"{pct_text(row['proxy_change_pct'])} |"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest fund intraday estimate formulas against official NAV returns.")
    parser.add_argument("codes", nargs="+", help="fund codes, for example: 011370 001438")
    parser.add_argument("--days", type=int, default=20, help="recent official NAV dates to evaluate")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args()
    results = [await backtest_code(code, days=args.days) for code in args.codes]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)


if __name__ == "__main__":
    asyncio.run(main())

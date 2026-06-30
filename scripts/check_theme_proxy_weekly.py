from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fund_estimator.data_sources.eastmoney import EastmoneyHoldingsDataSource, parse_pingzhong_profile
from fund_estimator.services.theme_proxy import infer_theme_proxy
from scripts.select_theme_proxy import DEFAULT_CANDIDATES, fetch_profile_js, select_proxy


THEME_TO_CANDIDATE_POOL = {
    "CPO/通信": "cpo",
    "半导体": "semiconductor",
    "人工智能": "ai",
}


def read_watchlist_codes(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT code FROM watchlist ORDER BY code ASC").fetchall()
    return [str(row[0]) for row in rows if str(row[0]).isdigit() and len(str(row[0])) == 6]


def current_score(scores: list[dict[str, Any]], current_code: str) -> dict[str, Any] | None:
    return next((item for item in scores if item.get("code") == current_code), None)


def recommendation_for(
    *,
    current: dict[str, Any] | None,
    best: dict[str, Any] | None,
    current_code: str,
    switch_threshold_pct: float,
) -> str:
    if best is None:
        return "候选样本不足，保持当前代理"
    if best.get("code") == current_code:
        return "当前代理仍为最优，保持"
    if current is None:
        return "当前代理没有足够样本，建议人工复核"
    improvement = float(current["mae_pct"]) - float(best["mae_pct"])
    best_corr = best.get("correlation")
    current_corr = current.get("correlation")
    corr_ok = best_corr is None or current_corr is None or float(best_corr) >= float(current_corr) - 0.02
    if improvement >= switch_threshold_pct and corr_ok:
        return "新代理明显更贴近，建议切换"
    return "差异不足或相关性未改善，保持当前代理"


async def check_code(code: str, *, days: int, min_samples: int, switch_threshold_pct: float) -> dict[str, Any]:
    holdings = await EastmoneyHoldingsDataSource(timeout=10.0).get_holdings(code)
    from httpx import AsyncClient

    from fund_estimator.data_sources.eastmoney import DEFAULT_HEADERS
    from fund_estimator.services.http_settings import http_trust_env

    async with AsyncClient(timeout=10.0, headers=DEFAULT_HEADERS, trust_env=http_trust_env()) as client:
        profile_text = await fetch_profile_js(client, code)
    profile = parse_pingzhong_profile(code, profile_text)
    proxy = infer_theme_proxy(profile, holdings)
    if proxy is None:
        return {
            "code": code,
            "name": profile.name,
            "status": "skipped",
            "reason": "未识别到可用关联板块",
        }
    pool = THEME_TO_CANDIDATE_POOL.get(proxy.theme)
    if pool is None:
        return {
            "code": code,
            "name": profile.name,
            "theme": proxy.theme,
            "current_proxy": {
                "code": proxy.proxy_code,
                "name": proxy.proxy_name,
            },
            "status": "skipped",
            "reason": "该主题暂未配置候选代理池",
        }
    selection = await select_proxy(
        code,
        candidates=DEFAULT_CANDIDATES[pool],
        days=days,
        min_samples=min_samples,
    )
    best = selection.get("best")
    current = current_score(selection.get("scores") or [], proxy.proxy_code)
    return {
        "code": code,
        "name": profile.name,
        "theme": proxy.theme,
        "current_proxy": {
            "code": proxy.proxy_code,
            "name": proxy.proxy_name,
            "score": current,
        },
        "best_proxy": best,
        "recommendation": recommendation_for(
            current=current,
            best=best,
            current_code=proxy.proxy_code,
            switch_threshold_pct=switch_threshold_pct,
        ),
        "status": "checked",
        "scores": selection.get("scores") or [],
        "skipped_candidates": selection.get("skipped") or [],
    }


async def run_check(
    *,
    codes: list[str],
    days: int,
    min_samples: int,
    switch_threshold_pct: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for code in codes:
        try:
            results.append(
                await check_code(
                    code,
                    days=days,
                    min_samples=min_samples,
                    switch_threshold_pct=switch_threshold_pct,
                )
            )
        except Exception as exc:  # pragma: no cover - operational report script
            results.append({"code": code, "status": "error", "error": str(exc)})
    checked = [item for item in results if item.get("status") == "checked"]
    switch_suggestions = [item for item in checked if "建议切换" in str(item.get("recommendation"))]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": days,
        "min_samples": min_samples,
        "switch_threshold_pct": switch_threshold_pct,
        "fund_count": len(codes),
        "checked_count": len(checked),
        "switch_suggestion_count": len(switch_suggestions),
        "results": results,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly evidence check for fund theme proxy ETF choices.")
    parser.add_argument("--db", default="data/fund_estimator.sqlite3", help="SQLite database path")
    parser.add_argument("--codes", nargs="*", help="override fund codes instead of reading all watchlists")
    parser.add_argument("--days", type=int, default=20, help="recent overlapping official NAV dates")
    parser.add_argument("--min-samples", type=int, default=10, help="minimum overlapping dates")
    parser.add_argument("--switch-threshold-pct", type=float, default=0.15, help="minimum MAE improvement to suggest switching")
    parser.add_argument("--out", default="data/theme_proxy_weekly/latest.json", help="output JSON report path")
    args = parser.parse_args()

    db_path = Path(args.db)
    codes = args.codes or read_watchlist_codes(db_path)
    report = await run_check(
        codes=sorted(set(codes)),
        days=args.days,
        min_samples=args.min_samples,
        switch_threshold_pct=args.switch_threshold_pct,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

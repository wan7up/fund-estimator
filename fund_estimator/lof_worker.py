from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from fund_estimator.api.app import create_estimator_service, create_lof_monitor_service
from fund_estimator.services.lof_notifications import LofNoticeService


async def scan_command(args: argparse.Namespace) -> dict[str, Any]:
    estimator = create_estimator_service()
    notice = LofNoticeService()
    monitor = create_lof_monitor_service(estimator, notice_service=notice)
    response = await monitor.get_opportunities(
        normal_threshold_pct=args.normal_threshold_pct,
        strong_threshold_pct=args.strong_threshold_pct,
        min_turnover_yuan=args.min_turnover_yuan,
        limit=args.limit,
        refresh=True,
    )
    result: dict[str, Any] = {"scan": response.model_dump(mode="json")}
    if args.notify:
        result["notice"] = notice.notify_from_scan(response)
    return result


async def daily_summary_command(args: argparse.Namespace) -> dict[str, Any]:
    estimator = create_estimator_service()
    notice = LofNoticeService()
    monitor = create_lof_monitor_service(estimator, notice_service=notice)
    response = await monitor.get_opportunities(
        normal_threshold_pct=args.normal_threshold_pct,
        strong_threshold_pct=args.strong_threshold_pct,
        min_turnover_yuan=args.min_turnover_yuan,
        limit=args.limit,
        refresh=True,
    )
    notice_result = notice.notify_daily_summary(
        response,
        force=args.force,
        send_empty=not args.no_empty,
    )
    return {"scan": response.model_dump(mode="json"), "notice": notice_result}


async def send_test_command(_: argparse.Namespace) -> dict[str, Any]:
    estimator = create_estimator_service()
    notice = LofNoticeService()
    monitor = create_lof_monitor_service(estimator, notice_service=notice)
    item = await monitor.get_item("501312")
    return notice.send_test(item=item)


def main() -> None:
    parser = argparse.ArgumentParser(description="LOF premium monitor worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("--notify", action="store_true")
    scan.add_argument("--normal-threshold-pct", type=float, default=2.0)
    scan.add_argument("--strong-threshold-pct", type=float, default=5.0)
    scan.add_argument("--min-turnover-yuan", type=float, default=3_000_000)
    scan.add_argument("--limit", type=int, default=80)

    daily = subparsers.add_parser("daily-summary")
    daily.add_argument("--normal-threshold-pct", type=float, default=2.0)
    daily.add_argument("--strong-threshold-pct", type=float, default=5.0)
    daily.add_argument("--min-turnover-yuan", type=float, default=3_000_000)
    daily.add_argument("--limit", type=int, default=120)
    daily.add_argument("--force", action="store_true")
    daily.add_argument("--no-empty", action="store_true", help="do not send a message when there are no actionable items")

    subparsers.add_parser("send-test")
    args = parser.parse_args()
    if args.command == "scan":
        result = asyncio.run(scan_command(args))
    elif args.command == "daily-summary":
        result = asyncio.run(daily_summary_command(args))
    elif args.command == "send-test":
        result = asyncio.run(send_test_command(args))
    else:
        raise SystemExit(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

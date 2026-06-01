from __future__ import annotations

from datetime import datetime

from fund_estimator.models.schema import WatchlistItem
from fund_estimator.services.cache import SQLiteCache
from fund_estimator.services.exceptions import AppError


class WatchlistService:
    def __init__(self, cache: SQLiteCache) -> None:
        self.cache = cache

    def list_items(self, device_id: str = "default") -> list[WatchlistItem]:
        return [
            WatchlistItem(
                code=row["code"],
                name=row["name"],
                added_at=datetime.fromisoformat(row["added_at"]),
                sort_order=int(row["sort_order"] or 0),
            )
            for row in self.cache.list_watchlist(device_id)
        ]

    def add(self, code: str, name: str | None = None, device_id: str = "default") -> WatchlistItem:
        self.cache.add_watchlist(code, name, device_id)
        for item in self.list_items(device_id):
            if item.code == code:
                return item
        raise RuntimeError("watchlist insert failed")

    def delete(self, code: str, device_id: str = "default") -> bool:
        return self.cache.delete_watchlist(code, device_id)

    def reorder(self, codes: list[str], device_id: str = "default") -> list[WatchlistItem]:
        if not self.cache.reorder_watchlist(codes, device_id):
            raise AppError(
                "WATCHLIST_REORDER_INVALID",
                "自选基金排序数据与当前列表不一致，请刷新后重试",
                status_code=422,
                details={"codes": codes},
            )
        return self.list_items(device_id)

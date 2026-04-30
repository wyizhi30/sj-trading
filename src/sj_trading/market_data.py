"""MarketData 模組。

模組職責：
- 即時 Tick 訂閱（Shioaji）
- 歷史 K 棒下載（Shioaji kbars）
- CSV 載入（回測用）
- 回測 replay（透過 callback 推送 KBar）

設計約束：
- 僅依賴內部 schemas（以及 Python 標準函式庫）；不得包含 Strategy/Broker 邏輯。
- 所有輸出資料必須轉換為內部 schema 物件。
- callback 是唯一資料輸出方式（即時與 replay 一致）。
"""

from __future__ import annotations

import csv
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

from .schemas import KBar, Tick

try:  # 匯入時可選；僅 live mode 需要。
    import shioaji as sj  # type: ignore
except Exception:  # pragma: no cover
    sj = None  # type: ignore


class MarketData:
    """行情資料閘道（同時支援 live 與 backtest 模式）。

    - live mode：建構時傳入已登入的 Shioaji `api`
    - backtest mode：`api=None`（預設），搭配 `load_csv()` + `replay()` 使用
    """

    def __init__(self, api: Optional[Any] = None):
        """建立 MarketData 實例。

        Args:
            api: Shioaji API 實例（live mode）。backtest mode 請傳入 None。

        Notes:
            為避免 decorator 重複註冊，Tick callback handler 在每個 instance
            最多只會註冊一次。
        """

        self.api = api

        self._tick_callbacks: Dict[str, Callable[[Tick], None]] = {}
        self._subscribed_codes: set[str] = set()
        self._tick_handler_registered: bool = False

        if self.api is not None:
            self._register_tick_handler_once()

    # ----------------------- Live: Tick subscription -----------------------

    def subscribe_tick(self, code: str, callback: Callable[[Tick], None]) -> None:
        """訂閱指定股票代碼的即時 Tick。

        這個方法會：
        - 只註冊一次 Shioaji tick handler（每個 instance 一次）
        - 透過內部 callback mapping 支援多標的訂閱
        - 過濾試撮資料（`simtrade == 1`）
        - 將原始 tick 轉換為內部 :class:`~sj_trading.schemas.Tick`

        Args:
            code: 股票代碼，例如 "2330"。
            callback: 每筆 Tick 觸發時呼叫，參數為內部 Tick。

        Raises:
            RuntimeError: backtest mode（api=None）時呼叫會拋出。
        """

        self._require_live_api("subscribe_tick")
        code = str(code).strip()
        if not code:
            raise ValueError("code must be a non-empty string")

        self._tick_callbacks[code] = callback

        if code in self._subscribed_codes:
            return

        contract, _kind = self._find_contract(code)
        self.api.quote.subscribe(
            contract,
            quote_type=sj.constant.QuoteType.Tick,
            version=sj.constant.QuoteVersion.v1,
        )
        self._subscribed_codes.add(code)

    def unsubscribe(self, code: str) -> None:
        """取消訂閱指定股票代碼的即時 Tick。

        Args:
            code: 股票代碼，例如 "2330"。

        Raises:
            RuntimeError: backtest mode（api=None）時呼叫會拋出。
        """

        self._require_live_api("unsubscribe")
        code = str(code).strip()
        if not code:
            raise ValueError("code must be a non-empty string")

        self._tick_callbacks.pop(code, None)

        if code not in self._subscribed_codes:
            return

        contract, _kind = self._find_contract(code)
        self.api.quote.unsubscribe(
            contract,
            quote_type=sj.constant.QuoteType.Tick,
            version=sj.constant.QuoteVersion.v1,
        )
        self._subscribed_codes.discard(code)

    # ----------------------- Live: Historical kbars ------------------------

    def get_history_kbar(self, code: str, start: str, end: str) -> list[KBar]:
        """透過 Shioaji API 下載歷史 K 棒。

        Args:
            code: 股票代碼，例如 "2330"。
            start: 起始日期（YYYY-MM-DD）。
            end: 結束日期（YYYY-MM-DD）。

        Returns:
            KBar 列表。若 API 回傳空資料則回傳 []。

        Raises:
            RuntimeError: backtest mode（api=None）時呼叫會拋出。
        """

        self._require_live_api("get_history_kbar")
        code = str(code).strip()
        if not code:
            raise ValueError("code must be a non-empty string")

        contract, _kind = self._find_contract(code)
        kbars = self.api.kbars(contract, start=start, end=end)
        if not kbars:
            return []

        rows = self._iter_kbars_rows(kbars)
        out: list[KBar] = []
        for row in rows:
            try:
                out.append(self._convert_kbar_row(row, code=code))
            except Exception:
                # 上游若回傳不完整/不合法資料，採 fail-fast，避免默默污染回測結果。
                raise
        return out

    # -------------------------- Backtest helpers --------------------------

    def load_csv(self, file_path: str, code: str) -> list[KBar]:
        """從 CSV 載入歷史 K 棒（回測用）。

        CSV 欄位（大小寫需一致）必須包含：
        - ts, Open, High, Low, Close, Volume, Amount

        Args:
            file_path: CSV 檔案路徑。
            code: 用於填入 KBar.code 的股票代碼。

        Returns:
            KBar 列表。

        Raises:
            ValueError: CSV 格式不正確時拋出。
        """

        code = str(code).strip()
        if not code:
            raise ValueError("code must be a non-empty string")

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"CSV file not found: {file_path}")

        required = ("ts", "Open", "High", "Low", "Close", "Volume", "Amount")
        kbars: list[KBar] = []

        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    raise ValueError("CSV missing header row")

                missing = [c for c in required if c not in reader.fieldnames]
                if missing:
                    raise ValueError(f"CSV missing required columns: {missing}")

                for idx, row in enumerate(reader, start=2):
                    try:
                        kbars.append(self._convert_kbar_row(row, code=code))
                    except ValueError as e:
                        raise ValueError(f"CSV row {idx} invalid: {e}") from e
        except UnicodeDecodeError as e:
            raise ValueError("CSV encoding must be UTF-8") from e

        return kbars

    def resample_kbars(self, kbars: list[KBar], freq: str = "1D") -> list[KBar]:
        """將 KBar 重新取樣成較大的時間框架。

        支援的頻率：
        - "1H", "2H", "4H"... : 小時級別（數字 + H）
        - "1D"                  : 日級別（預設）
        - "W"                   : 週級別（每週一開始）
        - "M"                   : 月級別

        Args:
            kbars: KBar 列表。
            freq: 頻率字符串，例如 "1D", "4H", "W", "M"。

        Returns:
            重新取樣後的 KBar 列表。

        Raises:
            ValueError: 頻率格式不正確或不支援時拋出。
            NotImplementedError: 某些頻率尚未實作時拋出。
        """

        freq = str(freq).strip().upper()
        if not kbars:
            return []

        # 解析頻率
        num_periods, unit = self._parse_frequency(freq)

        # 排序
        ordered = sorted(kbars, key=lambda b: self._parse_dt(b.ts))

        # 分組（使用 bucket_end_dt 作為 key 以簡化聚合）
        grouped: Dict[datetime, list[KBar]] = {}
        for bar in ordered:
            bucket_end_dt = self._get_bucket_end_dt(bar.ts, unit, num_periods)
            grouped.setdefault(bucket_end_dt, []).append(bar)

        # 聚合
        out: list[KBar] = []
        for bucket_end_dt in sorted(grouped.keys()):
            group = grouped[bucket_end_dt]
            first_bar = group[0]

            out.append(
                KBar(
                    code=first_bar.code,
                    ts=bucket_end_dt.isoformat(),
                    Open=float(first_bar.Open),
                    High=max(float(bar.High) for bar in group),
                    Low=min(float(bar.Low) for bar in group),
                    Close=float(group[-1].Close),
                    Volume=sum(int(bar.Volume) for bar in group),
                    Amount=sum(int(bar.Amount) for bar in group),
                    interval=freq,
                )
            )

        return out

    def _parse_frequency(self, freq: str) -> Tuple[int, str]:
        """解析頻率字串，回傳 (數字, 單位)。

        支援格式：
        - "1H", "2H", "4H" 等 -> (1, "H"), (2, "H"), (4, "H")
        - "1D" -> (1, "D")
        - "W" -> (1, "W")
        - "M" -> (1, "M")

        Args:
            freq: 頻率字符串。

        Returns:
            (num_periods, unit)，例如 ("4", "H")。

        Raises:
            ValueError: 格式不正確時拋出。
        """

        freq = str(freq).strip().upper()

        # 嘗試匹配 "1H", "4H" 等格式
        match = re.match(r"^(\d+)([HDWM])$", freq)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if num <= 0:
                raise ValueError(f"frequency number must be > 0, got {num}")
            if unit not in ("H", "D", "W", "M"):
                raise ValueError(f"unsupported unit {unit!r}, must be H/D/W/M")
            return num, unit

        # 嘗試匹配 "W", "M" 等單純單位
        if freq in ("W", "M"):
            return 1, freq
        if freq == "D":
            return 1, "D"

        raise ValueError(
            f"invalid frequency {freq!r}; "
            "supported formats: '1H', '4H', '1D', 'W', 'M', etc."
        )

    def _get_bucket_end_dt(self, ts: str, unit: str, num_periods: int) -> datetime:
        """根據時間戳和時間單位，計算時間桶的結束時間（UTC）。

        Args:
            ts: ISO 8601 時間戳。
            unit: "H", "D", "W", "M"。
            num_periods: 時間單位倍數（如 4 表示 4H）。

        Returns:
            時間桶的結束時間（datetime，UTC）。
        """

        dt = self._parse_dt(ts)

        if unit == "H":
            # 小時級別：計算所在桶的結束時間
            # 例如 4H：0-3時 -> 3:59:59, 4-7時 -> 7:59:59, ...
            bucket_idx = dt.hour // num_periods
            bucket_end_hour = (bucket_idx + 1) * num_periods - 1
            # 若超過 23，則進位到隔天
            if bucket_end_hour > 23:
                bucket_end_dt = dt.replace(hour=23, minute=59, second=59) + timedelta(days=1)
                bucket_end_dt = bucket_end_dt.replace(hour=bucket_end_hour - 24)
            else:
                bucket_end_dt = dt.replace(hour=bucket_end_hour, minute=59, second=59)
            return bucket_end_dt.replace(microsecond=0)

        elif unit == "D":
            # 日級別：該天的 23:59:59
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)

        elif unit == "W":
            # 週級別：該週的週日 23:59:59（ISO week）
            iso_year, iso_week, iso_day = dt.isocalendar()
            # 週一是 1，週日是 7
            week_start = datetime.fromisocalendar(iso_year, iso_week, 1)  # 週一
            week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
            return week_end.replace(tzinfo=timezone.utc, microsecond=0)

        elif unit == "M":
            # 月級別：該月的最後一天 23:59:59
            if dt.month == 12:
                month_end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
            else:
                month_end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
            return month_end.replace(hour=23, minute=59, second=59, microsecond=0)

        else:
            raise ValueError(f"unsupported unit {unit!r}")

    def replay(self, kbars: list[KBar], callback: Callable[[KBar], None], speed: float = 0) -> None:
        """依時間順序回放 K 棒。

        Args:
            kbars: KBar 列表（方法內部會依時間排序）。
            callback: 每根 K 棒推送時呼叫一次。
            speed: 0 表示不 sleep、以最快速度回放；
                   >0 表示使用 sleep 模擬時間，1.0 約等於真實速度。

        Raises:
            ValueError: speed < 0 時拋出。
        """

        if speed < 0:
            raise ValueError("speed must be >= 0")

        ordered = sorted(kbars, key=lambda b: self._parse_dt(b.ts))

        last_dt: Optional[datetime] = None
        for bar in ordered:
            current_dt = self._parse_dt(bar.ts)
            if speed > 0 and last_dt is not None:
                delta = (current_dt - last_dt).total_seconds()
                if delta > 0:
                    time.sleep(delta / speed)
            callback(bar)
            last_dt = current_dt

    # ----------------------------- Internals ------------------------------

    def _register_tick_handler_once(self) -> None:
        """註冊 Shioaji tick handler（每個 instance 只註冊一次）。"""

        if self._tick_handler_registered:
            return
        if self.api is None:
            return
        if sj is None:
            raise RuntimeError("shioaji is required for live mode")

        @self.api.on_tick_stk_v1()
        def _on_tick_stk_v1(exchange: Any, tick: Any) -> None:
            try:
                simtrade = int(getattr(tick, "simtrade", 0) or 0)
            except Exception:
                simtrade = 0
            if simtrade == 1:
                return

            internal = self._convert_tick(tick)
            cb = self._tick_callbacks.get(internal.code)
            if cb is not None:
                cb(internal)

        # 期貨/選擇權行情 callback（此專案先以期貨為主，沿用同一 Tick schema）。
        try:
            @self.api.on_tick_fop_v1()
            def _on_tick_fop_v1(exchange: Any, tick: Any) -> None:
                try:
                    simtrade = int(getattr(tick, "simtrade", 0) or 0)
                except Exception:
                    simtrade = 0
                if simtrade == 1:
                    return

                internal = self._convert_tick(tick)
                cb = self._tick_callbacks.get(internal.code)
                if cb is not None:
                    cb(internal)
        except Exception:
            # 部分環境/版本可能無 on_tick_fop_v1；保留股票功能不中斷。
            pass

        self._tick_handler_registered = True

    def _find_contract(self, code: str) -> tuple[Any, str]:
        """尋找合約並回傳 (contract, kind)。

        kind:
            - "STK": 股票
            - "FUT": 期貨
        """
    
        if self.api is None:
            raise RuntimeError("live mode requires api")

        code = str(code).strip()
        if not code:
            raise ValueError("code must be a non-empty string")

        # 1) 股票
        if code[0].isdigit():
            contract = self.api.Contracts.Stocks[code]
            return contract, "STK"
        else:
            # 2) 期貨：掃描 api.Contracts.Futures.*
            futures_root = getattr(self.api.Contracts, "Futures", None)
            if futures_root is not None:
                for name in dir(futures_root):
                    group = getattr(futures_root, name, None)
                    if group is None:
                        continue

                    # Contracts.Futures.TXF.TXFR1
                    try:
                        contract = getattr(group, code)
                        if contract is not None:
                            return contract, "FUT"
                    except Exception:
                        pass
        
        raise ValueError(f"contract not found for code={code}")

    def _require_live_api(self, method_name: str) -> None:
        if self.api is None:
            raise RuntimeError(f"{method_name} is only available in live mode (api is None)")
        if sj is None:
            raise RuntimeError("shioaji is required for live mode")

    def _convert_tick(self, tick: Any) -> Tick:
        """將 Shioaji TickSTKv1 轉換為內部 Tick。"""

        code = str(getattr(tick, "code", "")).strip()
        if not code:
            raise ValueError("tick.code is empty")

        raw_dt = getattr(tick, "datetime", None)
        dt_iso = self._to_iso_datetime(raw_dt)

        price = float(getattr(tick, "close", getattr(tick, "price", 0.0)) or 0.0)
        volume = int(getattr(tick, "volume", 0) or 0)
        total_volume = int(getattr(tick, "total_volume", 0) or 0)
        simtrade = int(getattr(tick, "simtrade", 0) or 0)

        return Tick(
            code=code,
            datetime=dt_iso,
            price=price,
            volume=volume,
            total_volume=total_volume,
            simtrade=simtrade,
        )

    def _convert_kbar_row(self, row: Any, *, code: str) -> KBar:
        """將單筆 K 棒資料（dict-like/tuple）轉換為內部 KBar。"""

        # 同時支援 dict-like（CSV DictReader）與 tuple/list（由多欄位陣列 zip 而來）。
        if isinstance(row, dict):
            ts = row.get("ts")
            open_ = row.get("Open")
            high = row.get("High")
            low = row.get("Low")
            close = row.get("Close")
            volume = row.get("Volume")
            amount = row.get("Amount")
            interval = row.get("interval")
        elif isinstance(row, (tuple, list)) and len(row) == 8:
            ts, open_, high, low, close, volume, amount, interval = row
        elif isinstance(row, (tuple, list)) and len(row) == 7:
            ts, open_, high, low, close, volume, amount = row
            interval = None
        else:
            raise ValueError("unsupported kbar row type")

        ts_iso = self._to_iso_datetime(ts)
        interval_str = str(interval).strip() if interval not in (None, "") else "1D"

        try:
            return KBar(
                code=code,
                ts=ts_iso,
                Open=float(open_),
                High=float(high),
                Low=float(low),
                Close=float(close),
                Volume=int(float(volume)),
                Amount=int(float(amount)),
                interval=interval_str,
            )
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid numeric fields: {e}") from e

    def _iter_kbars_rows(self, kbars: Any) -> Iterable[Tuple[Any, Any, Any, Any, Any, Any, Any, Any]]:
        """將 Shioaji `api.kbars()` 回傳結果轉為逐列 tuple 迭代。

        Shioaji 通常回傳 dict-like 物件，可用 `{**kbars}` 展開。
        這裡刻意不依賴 pandas，以保持模組輕量。
        """

        try:
            data = dict(kbars)
        except Exception:
            try:
                data = {**kbars}
            except Exception:
                data = None

        if not data:
            return []

        ts_list = data.get("ts")
        open_list = data.get("Open")
        high_list = data.get("High")
        low_list = data.get("Low")
        close_list = data.get("Close")
        vol_list = data.get("Volume")
        amt_list = data.get("Amount")
        interval_list = data.get("interval")

        if not ts_list:
            return []

        if interval_list is None:
            interval_list = ["1D"] * len(ts_list)

        return zip(ts_list, open_list, high_list, low_list, close_list, vol_list, amt_list, interval_list)

    def _to_iso_datetime(self, value: Any) -> str:
        """將各種時間表示法正規化為 ISO 8601 字串。"""

        if value is None:
            # 上游缺時間欄位時，仍回傳可用字串。
            return datetime.now(timezone.utc).isoformat()

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).isoformat()
            return value.isoformat()

        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()

        if isinstance(value, (int, float)):
            # 經驗法則：秒 / 毫秒 / 奈秒
            v = float(value)
            if v > 1e18:  # ns with extra precision
                v = v / 1e9
            elif v > 1e14:  # ns
                v = v / 1e9
            elif v > 1e11:  # ms
                v = v / 1e3
            dt = datetime.fromtimestamp(v, tz=timezone.utc)
            return dt.isoformat()

        if isinstance(value, str):
            s = value.strip()
            if not s:
                return datetime.now(timezone.utc).isoformat()
            # 支援 'Z'
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                # 無法解析時：保留原字串。
                return value

        return str(value)

    def _parse_dt(self, ts: str) -> datetime:
        """將 ISO 時間字串解析為 datetime（用於排序/回放）。"""

        s = (ts or "").strip()
        if not s:
            return datetime.min.replace(tzinfo=timezone.utc)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            # 無法解析時採 best-effort：視為最小時間。
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

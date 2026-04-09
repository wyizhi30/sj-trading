"""Broker 模組（MVP）。

依照 `docs/broker.md` 實作最小可用版本，提供：
- 實盤 Broker：封裝 Shioaji 下單/撤單/回報，並以 callback 通知外部
- 回測 MockBroker：本地模擬成交（不連線），支援 `process_kbar`

注意：
- 僅依賴 `sj_trading.schemas`（資料結構）與 Python 標準函式庫。
- 不包含 Strategy / MarketData 邏輯。
- Order.status 使用：Inactive/Submitted/Filled/Cancelled/Failed。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .schemas import Account, KBar, Order, Position, Signal

try:  # 匯入時可選；僅實盤 Broker 需要。
    import shioaji as sj  # type: ignore
except Exception:  # pragma: no cover
    sj = None  # type: ignore


OrderCallback = Callable[[Order], None]
PositionCallback = Callable[[Position], None]
AccountCallback = Callable[[Account], None]
ConnectionLostCallback = Callable[[], None]


class BaseBroker:
    """Broker 對外介面（MVP）。"""

    def place_signal(self, signal: Signal) -> Order:
        """送出交易訊號並回傳內部 Order。"""

        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        """撤單；成功回傳 True，否則 False。"""

        raise NotImplementedError

    def get_position(self, code: str) -> int:
        """取得指定標的持倉張數（正數=多單，0=空手）。"""

        raise NotImplementedError

    def on_order_update(self, callback: OrderCallback) -> None:
        raise NotImplementedError

    def on_position_update(self, callback: PositionCallback) -> None:
        raise NotImplementedError

    def on_account_update(self, callback: AccountCallback) -> None:
        raise NotImplementedError

    def on_connection_lost(self, callback: ConnectionLostCallback) -> None:
        raise NotImplementedError


class Broker(BaseBroker):
    """實盤 Broker（連接 Shioaji）。

    建議用法：傳入已完成 login 的 Shioaji api。
    """

    def __init__(self, api: Any) -> None:
        if sj is None:
            raise RuntimeError("shioaji 未安裝，無法使用實盤 Broker")
        self.api = api

        self._order_callbacks: List[OrderCallback] = []
        self._position_callbacks: List[PositionCallback] = []
        self._account_callbacks: List[AccountCallback] = []
        self._connection_lost_callbacks: List[ConnectionLostCallback] = []

        self._order_by_id: Dict[str, Order] = {}
        self._order_callback_registered: bool = False

        self._register_order_callback_once()

    # -------------------------- Callback 註冊介面 --------------------------

    def on_order_update(self, callback: OrderCallback) -> None:
        """訂閱訂單更新事件。"""

        self._order_callbacks.append(callback)

    def on_position_update(self, callback: PositionCallback) -> None:
        """訂閱持倉更新事件。"""

        self._position_callbacks.append(callback)

    def on_account_update(self, callback: AccountCallback) -> None:
        """訂閱帳戶更新事件。"""

        self._account_callbacks.append(callback)

    def on_connection_lost(self, callback: ConnectionLostCallback) -> None:
        """訂閱連線中斷事件（MVP：僅保留介面）。"""

        self._connection_lost_callbacks.append(callback)

    # ------------------------------ 主要功能 ------------------------------

    def place_signal(self, signal: Signal) -> Order:
        """接收 Signal 並送出 Shioaji 下單。

        支援：
        - 現股：api.Contracts.Stocks[code]
        - 期貨：掃描 api.Contracts.Futures.* 尋找 code
        """

        contract, kind = self._find_contract(signal.code)
        sj_order = self._build_sj_order(signal, kind=kind)

        try:
            trade = self.api.place_order(contract, sj_order)
        except Exception as e:
            now = self._now_iso()
            order = Order(
                order_id="",
                seqno="",
                strategy_id=signal.strategy_id,
                code=signal.code,
                action=signal.action,
                order_type=signal.order_type,
                price=signal.price,
                quantity=signal.quantity,
                status="Failed",
                filled_price=None,
                filled_quantity=0,
                created_at=now,
                updated_at=now,
                error_message=str(e),
            )
            self._emit_order(order)
            return order

        order = self._convert_trade_to_order(trade, signal)
        self._order_by_id[order.order_id] = order
        self._emit_order(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤單（MVP）。

        Shioaji 撤單通常需要 trade 物件。此處採最小可用：
        - 先 update_status
        - list_trades 找到對應 id
        - 呼叫 cancel_order
        """

        order_id = str(order_id).strip()
        if not order_id:
            return False

        try:
            # 更新交易所狀態（現股/期貨都可能需要）
            if getattr(self.api, "stock_account", None) is not None:
                self.api.update_status(self.api.stock_account)
            if getattr(self.api, "futopt_account", None) is not None:
                self.api.update_status(self.api.futopt_account)
            trades = self.api.list_trades()
            trade = next((t for t in trades if getattr(getattr(t, "order", None), "id", "") == order_id), None)
            if trade is None:
                return False
            self.api.cancel_order(trade)
            return True
        except Exception:
            return False

    def get_position(self, code: str) -> int:
        """查詢指定標的持倉（MVP）。

        現股：list_positions(stock_account) 逐筆累加。
        期貨：list_positions(futopt_account) 逐筆累加（若 API 支援）。

        Returns:
            以「買進為正、賣出為負」的張數/口數（MVP：不做更細緻拆分）。
        """

        code = str(code).strip()
        if not code:
            return 0

        qty = 0
        try:
            if getattr(self.api, "stock_account", None) is not None:
                positions = self.api.list_positions(self.api.stock_account)
                for p in positions:
                    if str(getattr(p, "code", "")) != code:
                        continue
                    direction = str(getattr(p, "direction", "Buy")).lower()
                    q = int(getattr(p, "quantity", 0) or 0)
                    qty += q if direction == "buy" else -q
        except Exception:
            pass

        try:
            if getattr(self.api, "futopt_account", None) is not None:
                positions = self.api.list_positions(self.api.futopt_account)
                for p in positions:
                    if str(getattr(p, "code", "")) != code:
                        continue
                    direction = str(getattr(p, "direction", "Buy")).lower()
                    q = int(getattr(p, "quantity", 0) or 0)
                    qty += q if direction == "buy" else -q
        except Exception:
            pass

        return int(qty)

    def refresh_positions(self) -> List[Position]:
        """主動刷新持倉並觸發 position callbacks（MVP）。"""

        out: List[Position] = []
        out.extend(self._fetch_positions(account=getattr(self.api, "stock_account", None)))
        out.extend(self._fetch_positions(account=getattr(self.api, "futopt_account", None)))
        for p in out:
            self._emit_position(p)
        return out

    def refresh_account(self) -> Optional[Account]:
        """主動刷新帳戶並觸發 account callbacks（MVP）。"""

        try:
            balance = self.api.account_balance()
            acc_balance = float(getattr(balance, "acc_balance", 0.0) or 0.0)
        except Exception:
            acc_balance = 0.0

        # MVP：已實現/未實現損益不強制計算；保留欄位供上層擴充。
        now = self._now_iso()
        account = Account(
            acc_balance=acc_balance,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            total_pnl=0.0,
            equity=acc_balance,
            updated_at=now,
        )
        self._emit_account(account)
        return account

    # ------------------------------ 內部工具 ------------------------------

    def _register_order_callback_once(self) -> None:
        if self._order_callback_registered:
            return

        @self.api.on_order_callback
        def _on_order_callback(stat: Any, msg: dict) -> None:  # type: ignore[no-redef]
            try:
                order = self._convert_order_callback_msg(msg)
                if order.order_id:
                    self._order_by_id[order.order_id] = order
                self._emit_order(order)
            except Exception:
                # MVP：不讓 callback 例外中斷回報處理。
                return

        self._order_callback_registered = True

    def _emit_order(self, order: Order) -> None:
        for cb in list(self._order_callbacks):
            try:
                cb(order)
            except Exception:
                continue

    def _emit_position(self, position: Position) -> None:
        for cb in list(self._position_callbacks):
            try:
                cb(position)
            except Exception:
                continue

    def _emit_account(self, account: Account) -> None:
        for cb in list(self._account_callbacks):
            try:
                cb(account)
            except Exception:
                continue

    def _find_contract(self, code: str) -> tuple[Any, str]:
        """尋找合約並回傳 (contract, kind)。

        kind 可能為："STK" / "FUT"。
        """

        code = str(code).strip()
        if not code:
            raise ValueError("code 不可為空")

        # 1) 現股
        try:
            contract = self.api.Contracts.Stocks[code]
            return contract, "STK"
        except Exception:
            pass

        # 2) 期貨：掃描 api.Contracts.Futures.*
        futures_root = getattr(self.api.Contracts, "Futures", None)
        if futures_root is not None:
            for name in dir(futures_root):
                if name.startswith("_"):
                    continue
                group = getattr(futures_root, name, None)
                if isinstance(group, dict) and code in group:
                    return group[code], "FUT"

        raise ValueError(f"找不到合約 code={code}")

    def _build_sj_order(self, signal: Signal, *, kind: str) -> Any:
        """Signal -> Shioaji Order（現股/期貨）。"""

        action = sj.constant.Action.Buy if signal.action == "Buy" else sj.constant.Action.Sell
        if kind == "STK":
            if signal.order_type == "LIMIT":
                price_type = sj.constant.StockPriceType.LMT
            else:
                price_type = sj.constant.StockPriceType.MKT
            return self.api.Order(
                price=signal.price or 0,
                quantity=signal.quantity,
                action=action,
                price_type=price_type,
                order_type=sj.constant.OrderType.ROD,
                order_lot=sj.constant.StockOrderLot.Common,
                account=self.api.stock_account,
            )

        # FUT
        if signal.order_type == "LIMIT":
            price_type = sj.constant.FuturesPriceType.LMT
        else:
            price_type = sj.constant.FuturesPriceType.MKT
        return self.api.Order(
            price=signal.price or 0,
            quantity=signal.quantity,
            action=action,
            price_type=price_type,
            order_type=sj.constant.OrderType.ROD,
            octype=sj.constant.FuturesOCType.Auto,
            account=self.api.futopt_account,
        )

    def _convert_trade_to_order(self, trade: Any, signal: Signal) -> Order:
        """將 Shioaji trade 轉為內部 Order（MVP）。"""

        order_obj = getattr(trade, "order", None)
        order_id = str(getattr(order_obj, "id", "") or "")
        seqno = str(getattr(order_obj, "seqno", "") or "")

        now = self._now_iso()
        status = "Submitted" if order_id else "Inactive"
        return Order(
            order_id=order_id,
            seqno=seqno,
            strategy_id=signal.strategy_id,
            code=signal.code,
            action=signal.action,
            order_type=signal.order_type,
            price=signal.price,
            quantity=signal.quantity,
            status=status,
            filled_price=None,
            filled_quantity=0,
            created_at=now,
            updated_at=now,
            error_message=None,
        )

    def _convert_order_callback_msg(self, msg: dict) -> Order:
        """將 Shioaji order callback 訊息轉為內部 Order（MVP）。"""

        order_msg = msg.get("order") or {}
        status_msg = msg.get("status") or {}
        contract_msg = msg.get("contract") or {}
        operation = msg.get("operation") or {}

        order_id = str(order_msg.get("id") or status_msg.get("id") or "")
        seqno = str(order_msg.get("seqno") or "")
        code = str(contract_msg.get("code") or "")
        action = str(order_msg.get("action") or "")
        if action not in ("Buy", "Sell"):
            action = "Buy" if action.lower() == "buy" else "Sell"

        qty = int(order_msg.get("quantity") or status_msg.get("order_quantity") or 0)
        price_val = order_msg.get("price")
        price = float(price_val) if price_val is not None else None

        # 狀態映射（MVP：用最基本欄位推斷）
        status = self._map_status(operation=operation, status_msg=status_msg)

        ts = status_msg.get("exchange_ts")
        updated_at = self._to_iso(ts) if ts is not None else self._now_iso()

        prev = self._order_by_id.get(order_id)
        created_at = prev.created_at if prev is not None else updated_at
        filled_price = prev.filled_price if prev is not None else None
        filled_qty = prev.filled_quantity if prev is not None else 0

        # 若 callback 中帶有成交量/價格，嘗試更新（欄位名稱依版本不同，採 best-effort）。
        deal_qty = status_msg.get("deal_quantity")
        if deal_qty is None:
            deal_qty = status_msg.get("filled_quantity")
        if deal_qty is not None:
            try:
                filled_qty = int(deal_qty)
            except Exception:
                pass
        deal_price = status_msg.get("deal_price")
        if deal_price is None:
            deal_price = status_msg.get("filled_price")
        if deal_price is not None:
            try:
                filled_price = float(deal_price)
            except Exception:
                pass

        return Order(
            order_id=order_id,
            seqno=seqno,
            strategy_id="",  # MVP：回報訊息不一定含 strategy_id
            code=code,
            action=action,
            order_type="",  # MVP：回報訊息不一定含 order_type
            price=price,
            quantity=qty,
            status=status,
            filled_price=filled_price,
            filled_quantity=filled_qty,
            created_at=created_at,
            updated_at=updated_at,
            error_message=None if operation.get("op_code") in (None, "00") else str(operation.get("op_msg") or ""),
        )

    @staticmethod
    def _map_status(*, operation: dict, status_msg: dict) -> str:
        op_code = str(operation.get("op_code") or "")
        if op_code and op_code != "00":
            return "Failed"

        # cancel_quantity == order_quantity -> Cancelled
        try:
            oq = int(status_msg.get("order_quantity") or 0)
            cq = int(status_msg.get("cancel_quantity") or 0)
            if oq > 0 and cq >= oq:
                return "Cancelled"
        except Exception:
            pass

        # filled
        for key in ("deal_quantity", "filled_quantity"):
            if key in status_msg:
                try:
                    if int(status_msg.get(key) or 0) > 0:
                        return "Filled"
                except Exception:
                    pass

        return "Submitted"

    def _fetch_positions(self, account: Any) -> List[Position]:
        if account is None:
            return []
        out: List[Position] = []
        try:
            positions = self.api.list_positions(account)
            for p in positions:
                code = str(getattr(p, "code", ""))
                if not code:
                    continue
                out.append(
                    Position(
                        code=code,
                        direction=str(getattr(p, "direction", "Buy")),
                        quantity=int(getattr(p, "quantity", 0) or 0),
                        price=float(getattr(p, "price", 0.0) or 0.0),
                        last_price=float(getattr(p, "last_price", 0.0) or 0.0),
                        pnl=float(getattr(p, "pnl", 0.0) or 0.0),
                        yd_quantity=int(getattr(p, "yd_quantity", 0) or 0),
                    )
                )
        except Exception:
            return []
        return out

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_iso(value: Any) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).isoformat()
            return value.isoformat()
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                return value
        return str(value)


class MockBroker(BaseBroker):
    """回測用 MockBroker（MVP）。

    不連接 Shioaji，僅依規格用 KBar 模擬成交。
    """

    def __init__(self, initial_cash: float = 1_000_000.0) -> None:
        self._order_callbacks: List[OrderCallback] = []
        self._position_callbacks: List[PositionCallback] = []
        self._account_callbacks: List[AccountCallback] = []
        self._connection_lost_callbacks: List[ConnectionLostCallback] = []

        self._orders: Dict[str, Order] = {}
        self._pending_ids: List[str] = []
        self._seq: int = 0

        self._positions: Dict[str, int] = {}
        self._cash: float = float(initial_cash)

        now = self._now_iso()
        self._account = Account(
            acc_balance=self._cash,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            total_pnl=0.0,
            equity=self._cash,
            updated_at=now,
        )

    # -------------------------- Callback 註冊介面 --------------------------

    def on_order_update(self, callback: OrderCallback) -> None:
        self._order_callbacks.append(callback)

    def on_position_update(self, callback: PositionCallback) -> None:
        self._position_callbacks.append(callback)

    def on_account_update(self, callback: AccountCallback) -> None:
        self._account_callbacks.append(callback)

    def on_connection_lost(self, callback: ConnectionLostCallback) -> None:
        self._connection_lost_callbacks.append(callback)

    # ------------------------------ 主要功能 ------------------------------

    def place_signal(self, signal: Signal) -> Order:
        """建立委託單並加入 pending（MVP）。"""

        self._seq += 1
        order_id = f"mock-{self._seq:08d}"
        now = self._now_iso()
        order = Order(
            order_id=order_id,
            seqno=str(self._seq),
            strategy_id=signal.strategy_id,
            code=signal.code,
            action=signal.action,
            order_type=signal.order_type,
            price=signal.price,
            quantity=signal.quantity,
            status="Submitted",
            filled_price=None,
            filled_quantity=0,
            created_at=now,
            updated_at=now,
            error_message=None,
        )
        self._orders[order_id] = order
        self._pending_ids.append(order_id)
        self._emit_order(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤單（MVP）。"""

        order_id = str(order_id).strip()
        if order_id not in self._orders:
            return False
        order = self._orders[order_id]
        if order.status in ("Filled", "Cancelled", "Failed"):
            return False

        now = self._now_iso()
        new_order = replace(order, status="Cancelled", updated_at=now)
        self._orders[order_id] = new_order
        if order_id in self._pending_ids:
            self._pending_ids.remove(order_id)
        self._emit_order(new_order)
        return True

    def get_position(self, code: str) -> int:
        return int(self._positions.get(str(code).strip(), 0))

    def process_kbar(self, kbar: KBar) -> None:
        """推入一根 K 棒，嘗試撮合所有 pending 訂單（MVP）。

        模擬成交規則（依 `docs/broker.md`）：
        - MARKET：下一根 K 棒直接成交，成交價=下一根 Open
        - LIMIT 買單：Low <= 委託價 → 成交價=委託價
        - LIMIT 賣單：High >= 委託價 → 成交價=委託價
        """

        # 只處理對應標的的 pending
        pending = list(self._pending_ids)
        for order_id in pending:
            order = self._orders.get(order_id)
            if order is None or order.status != "Submitted":
                continue
            if order.code != kbar.code:
                continue

            fill_price = self._match_order(order, kbar)
            if fill_price is None:
                continue

            filled_qty = int(order.quantity)
            now = str(kbar.ts)
            new_order = replace(
                order,
                status="Filled",
                filled_price=float(fill_price),
                filled_quantity=filled_qty,
                updated_at=now,
            )
            self._orders[order_id] = new_order
            if order_id in self._pending_ids:
                self._pending_ids.remove(order_id)

            # 更新持倉（MVP：僅做淨持倉）
            pos = int(self._positions.get(order.code, 0))
            if order.action == "Buy":
                pos += filled_qty
                self._cash -= float(fill_price) * float(filled_qty)
            else:
                pos -= filled_qty
                self._cash += float(fill_price) * float(filled_qty)
            self._positions[order.code] = pos

            self._emit_order(new_order)
            self._emit_position(
                Position(
                    code=order.code,
                    direction="Buy" if pos >= 0 else "Sell",
                    quantity=abs(int(pos)),
                    price=float(fill_price),
                    last_price=float(kbar.Close),
                    pnl=0.0,
                    yd_quantity=0,
                )
            )
            self._update_account(ts=now)

    # ------------------------------ 內部工具 ------------------------------

    def _match_order(self, order: Order, kbar: KBar) -> Optional[float]:
        if order.order_type == "MARKET":
            return float(kbar.Open)

        # LIMIT
        if order.price is None:
            return None
        px = float(order.price)
        if order.action == "Buy":
            return px if float(kbar.Low) <= px else None
        return px if float(kbar.High) >= px else None

    def _update_account(self, ts: str) -> None:
        self._account = replace(
            self._account,
            acc_balance=float(self._cash),
            equity=float(self._cash),
            updated_at=str(ts),
        )
        self._emit_account(self._account)

    def _emit_order(self, order: Order) -> None:
        for cb in list(self._order_callbacks):
            try:
                cb(order)
            except Exception:
                continue

    def _emit_position(self, position: Position) -> None:
        for cb in list(self._position_callbacks):
            try:
                cb(position)
            except Exception:
                continue

    def _emit_account(self, account: Account) -> None:
        for cb in list(self._account_callbacks):
            try:
                cb(account)
            except Exception:
                continue

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

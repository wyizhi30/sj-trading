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

    def __init__(self, initial_cash: float = 10_000_000.0, commission_rate: float = 0.001425, tax_rate: float = 0.003, slippage_per_unit: float = 0.0) -> None:
        self._order_callbacks: List[OrderCallback] = []
        self._position_callbacks: List[PositionCallback] = []
        self._account_callbacks: List[AccountCallback] = []
        self._connection_lost_callbacks: List[ConnectionLostCallback] = []

        self._initial_cash: float = float(initial_cash)
        self.commission_rate: float = float(commission_rate)
        self.tax_rate: float = float(tax_rate)
        self.slippage_per_unit: float = float(slippage_per_unit)
        self._orders: Dict[str, Order] = {}
        self._pending_ids: List[str] = []
        self._seq: int = 0

        self._positions: Dict[str, int] = {}
        self._avg_price_by_code: Dict[str, float] = {}
        self._last_price_by_code: Dict[str, float] = {}
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
        """建立委託單並加入 pending（MVP）。

        使用 signal 的 timestamp 作為訂單建立時間，而非系統當前時間，
        以確保回測模式下訂單時間與 KBar 時間一致。
        """

        self._seq += 1
        order_id = f"mock-{self._seq:08d}"
        # 使用 signal 的 timestamp 而不是系統時間（回測模式下應使用 KBar 時間）
        ts = str(signal.timestamp or "").strip() if signal.timestamp else self._now_iso()
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
            created_at=ts,
            updated_at=ts,
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

        self._last_price_by_code[str(kbar.code)] = float(kbar.Close)

        # 只處理對應標的的 pending
        emitted_position_codes: set[str] = set()
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

            exec_price = self._apply_slippage(float(fill_price), order.action)

            filled_qty = int(order.quantity)
            now = str(kbar.ts)
            new_order = replace(
                order,
                status="Filled",
                filled_price=float(exec_price),
                filled_quantity=filled_qty,
                updated_at=now,
            )
            self._orders[order_id] = new_order
            if order_id in self._pending_ids:
                self._pending_ids.remove(order_id)

            # 更新持倉與平均成本（MVP：以淨持倉 + 平均成本追蹤未實現損益）
            try:
                pos, avg_price = self._apply_fill(
                    code=order.code,
                    action=order.action,
                    quantity=filled_qty,
                    fill_price=float(exec_price),
                )
            except ValueError as e:
                # 現金不足等例外：標記訂單失敗並記錄錯誤訊息
                failed_order = replace(
                    new_order,
                    status="Failed",
                    filled_price=None,
                    filled_quantity=0,
                    error_message=str(e),
                )
                self._orders[order_id] = failed_order
                self._emit_order(failed_order)
                continue
            
            self._positions[order.code] = pos
            if avg_price is None:
                self._avg_price_by_code.pop(order.code, None)
            else:
                self._avg_price_by_code[order.code] = float(avg_price)

            self._emit_order(new_order)
            last_price = float(kbar.Close)
            self._emit_position_snapshot(code=order.code, last_price=last_price)
            emitted_position_codes.add(str(order.code).strip())

        # 即使沒有成交，也要回報該標的的最新持倉損益，避免持倉明細落後於帳戶區塊。
        code_key = str(kbar.code).strip()
        if code_key and code_key in self._positions and code_key not in emitted_position_codes:
            self._emit_position_snapshot(code=code_key, last_price=float(kbar.Close))

        # 即使沒有成交，也要根據最新收盤價重算帳戶淨值與未實現損益。
        self._update_account(ts=str(kbar.ts))

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

    def _apply_slippage(self, fill_price: float, action: str) -> float:
        slip = float(self.slippage_per_unit)
        if slip <= 0:
            return float(fill_price)
        if str(action) == "Buy":
            return max(0.0, float(fill_price) + slip)
        return max(0.0, float(fill_price) - slip)

    def _trade_costs(self, code: str, action: str, trade_value: float) -> tuple[float, float, float]:
        commission = float(trade_value) * float(self.commission_rate)
        tax = 0.0
        if str(code).strip().isdigit() and str(action) == "Sell":
            tax = float(trade_value) * float(self.tax_rate)
        return float(commission), float(tax), float(commission + tax)

    def _get_contract_multiplier(self, code: str) -> float:
        """根據標的代碼取得合約乘數。

        - 股票（純數字代碼）：1 張 = 1000 股，損益須乘 1000
        - 期貨（含字母）：暫不定義，先回傳 1（留待未來擴充）
        
        Note: 此方法在計算現金與損益時使用，以確保金額單位正確。
        """
        code_str = str(code).strip()
        if code_str.isdigit():
            return 1000.0  # 股票：1 張 = 1000 股
        # 期貨、選擇權等：暫時回傳 1（未來可加入 TXF/MXF 等的明確定義）
        return 1.0

    def _update_account(self, ts: str) -> None:
        unrealized_pnl, total_equity, realized_pnl = self._mark_to_market()
        self._account = replace(
            self._account,
            acc_balance=float(self._cash),
            unrealized_pnl=float(unrealized_pnl),
            realized_pnl=float(realized_pnl),
            total_pnl=float(total_equity - self._initial_cash),
            equity=float(total_equity),
            updated_at=str(ts),
        )
        self._emit_account(self._account)

    def _mark_to_market(self) -> tuple[float, float, float]:
        unrealized_pnl = 0.0
        market_value = 0.0

        for code, qty in self._positions.items():
            if qty == 0:
                continue
            qty_signed = int(qty)
            avg_price = float(self._avg_price_by_code.get(code, 0.0) or 0.0)
            last_price = float(self._last_price_by_code.get(code, avg_price) or avg_price)
            multiplier = self._get_contract_multiplier(code)
            unrealized_pnl += float(qty_signed) * (float(last_price) - float(avg_price)) * float(multiplier)
            market_value += float(qty_signed) * float(last_price) * float(multiplier)

        total_equity = float(self._cash) + float(market_value)
        total_pnl = float(total_equity) - float(self._initial_cash)
        realized_pnl = float(total_pnl - unrealized_pnl)
        return float(unrealized_pnl), float(total_equity), float(realized_pnl)

    def _position_unrealized_pnl(self, code: str, last_price: float) -> float:
        qty = int(self._positions.get(str(code).strip(), 0))
        if qty == 0:
            return 0.0
        avg_price = float(self._avg_price_by_code.get(str(code).strip(), 0.0) or 0.0)
        multiplier = self._get_contract_multiplier(code)
        return float(qty) * (float(last_price) - float(avg_price)) * float(multiplier)

    def _emit_position_snapshot(self, *, code: str, last_price: float) -> None:
        code_key = str(code).strip()
        qty = int(self._positions.get(code_key, 0))
        avg_price = float(self._avg_price_by_code.get(code_key, last_price) or last_price)
        unrealized_pnl = self._position_unrealized_pnl(code_key, float(last_price))
        self._emit_position(
            Position(
                code=code_key,
                direction="Buy" if qty >= 0 else "Sell",
                quantity=abs(int(qty)),
                price=float(avg_price),
                last_price=float(last_price),
                pnl=float(unrealized_pnl),
                yd_quantity=0,
            )
        )

    def _apply_fill(self, *, code: str, action: str, quantity: int, fill_price: float) -> tuple[int, Optional[float]]:
        current_qty = int(self._positions.get(code, 0))
        signed_fill = int(quantity) if action == "Buy" else -int(quantity)

        # 現金按成交金額變動，維持與實際成交方向一致。
        multiplier = self._get_contract_multiplier(code)
        trade_value = float(fill_price) * float(abs(signed_fill)) * float(multiplier)
        commission, tax, fees = self._trade_costs(code, action, trade_value)
        if signed_fill > 0:
            cost = trade_value + fees
            if self._cash < cost:
                raise ValueError(
                    f"Insufficient cash: need {cost:,.0f} TWD, "
                    f"but have {self._cash:,.0f} TWD (code={code}, qty={abs(signed_fill)})"
                )
            self._cash -= cost
        else:
            self._cash += trade_value - fees

        if current_qty == 0:
            new_qty = signed_fill
            return new_qty, (float(fill_price) if new_qty != 0 else None)

        current_sign = 1 if current_qty > 0 else -1
        fill_sign = 1 if signed_fill > 0 else -1

        # 同方向加碼：更新加權平均成本
        if current_sign == fill_sign:
            new_qty = current_qty + signed_fill
            current_avg = float(self._avg_price_by_code.get(code, fill_price) or fill_price)
            new_avg = (
                abs(current_qty) * current_avg + abs(signed_fill) * float(fill_price)
            ) / float(abs(new_qty))
            return new_qty, float(new_avg)

        # 反方向減碼/反手：先消掉原部位，再視剩餘量決定是否翻倉
        abs_current = abs(current_qty)
        abs_fill = abs(signed_fill)

        if abs_fill < abs_current:
            # 部分平倉，平均成本維持不變
            new_qty = current_qty + signed_fill
            return new_qty, float(self._avg_price_by_code.get(code, fill_price) or fill_price)

        if abs_fill == abs_current:
            # 完全平倉
            return 0, None

        # 反手：剩餘量以成交價作為新部位平均成本
        new_qty = current_qty + signed_fill
        return new_qty, float(fill_price)

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

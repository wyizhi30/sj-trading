"""Backtest 模組（MVP）。

依照 `docs/backtest.md` 實作最小可用版本，目標是跑通回測閉環：

KBar（CSV / API）
  → Strategy.on_kbar()
  → Signal
  → MockBroker.place_signal()
  → MockBroker.process_kbar() 撮合成交
  → 產出 TradeResult 與 PerformanceReport

注意：
- 本模組不做策略優化、不加入交易成本與滑價（MVP）。
- 指標計算假設資料以「日」為主（Sharpe 年化係數 252）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

from .broker import MockBroker
from .market_data import MarketData
from .schemas import KBar, Order, PerformanceReport, TradeResult


@dataclass
class _OpenPosition:
    code: str
    qty: int
    avg_price: float
    entry_time: str
    entry_commission_total: float = 0.0


class Backtester:
    """回測器（MVP）。

    Args:
        strategy: 策略物件，需實作 `on_kbar(kbar)` 並回傳 `Signal | None`。
        initial_cash: 初始資金。
    """

    def __init__(self, strategy: Any, initial_cash: float = 10_000_000.0, commission_rate: float = 0.001425, tax_rate: float = 0.003, slippage_per_unit: float = 0.0):
        self.strategy = strategy
        self.initial_cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.tax_rate = float(tax_rate)
        self.slippage_per_unit = float(slippage_per_unit)

        # 若策略未帶 broker，回測時自動建立 MockBroker。
        broker = getattr(self.strategy, "broker", None)
        if broker is None:
            broker = MockBroker(
                initial_cash=self.initial_cash,
                commission_rate=self.commission_rate,
                tax_rate=self.tax_rate,
                slippage_per_unit=self.slippage_per_unit,
            )
            setattr(self.strategy, "broker", broker)
        self.broker: MockBroker = broker

        self.commission_rate = float(getattr(self.broker, "commission_rate", self.commission_rate))
        self.tax_rate = float(getattr(self.broker, "tax_rate", self.tax_rate))
        self.slippage_per_unit = float(getattr(self.broker, "slippage_per_unit", self.slippage_per_unit))

        self._trade_history: List[TradeResult] = []
        self._equity_curve: List[float] = []
        self._equity_timestamps: List[str] = []

        self._cash: float = self.initial_cash
        self._equity: float = self.initial_cash
        self._last_price: float = 0.0
        self._open_pos: Optional[_OpenPosition] = None

        # 訂閱 broker 事件以追蹤成交與現金
        self.broker.on_order_update(self._on_order_update)
        # 讓策略也能接收 order update（更新內部持倉追蹤；若策略未實作也不影響）
        self.broker.on_order_update(self._try_strategy_on_order_update)
        self.broker.on_account_update(self._on_account_update)

    # ------------------------------ 對外方法 ------------------------------

    def run(self, kbars: List[KBar]) -> PerformanceReport:
        """執行回測並回傳績效報告。"""

        self._reset_state()

        if hasattr(self.strategy, "start"):
            try:
                self.strategy.start()
            except Exception:
                pass

        # 依時間排序（保護呼叫端）
        ordered = sorted(kbars, key=lambda b: str(b.ts))
        for kbar in ordered:
            self._last_price = float(kbar.Close)

            # 先撮合上一根 bar 產生的委託（MARKET 規則：下一根 Open）
            self.broker.process_kbar(kbar)

            # 再用這根 bar 產生新訊號並送單
            signal = self.strategy.on_kbar(kbar)
            if signal is not None:
                self.broker.place_signal(signal)

            # 直接使用 broker 回報的 account.equity，避免與實際乘數/持倉估值脫鉤。
            equity = float(self._equity)
            self._equity_curve.append(equity)
            self._equity_timestamps.append(str(kbar.ts))

        if hasattr(self.strategy, "stop"):
            try:
                self.strategy.stop()
            except Exception:
                pass

        return self._build_report()

    def run_from_csv(self, file_path: str, code: str) -> PerformanceReport:
        """從 CSV 載入 KBar 後執行回測。"""

        md = MarketData(api=None)
        kbars = md.load_csv(file_path, code=code)
        return self.run(kbars)

    def run_from_api(self, api: Any, code: str, start: str, end: str) -> PerformanceReport:
        """透過 Shioaji API 下載歷史 KBar 後執行回測。"""

        md = MarketData(api=api)
        kbars = md.get_history_kbar(code=code, start=start, end=end)
        return self.run(kbars)

    # ---------------------------- 事件處理/追蹤 ---------------------------

    def _try_strategy_on_order_update(self, order: Order) -> None:
        handler = getattr(self.strategy, "on_order_update", None)
        if callable(handler):
            try:
                handler(order)
            except Exception:
                return

    def _on_account_update(self, account: Any) -> None:
        # schemas.Account.acc_balance
        try:
            self._cash = float(getattr(account, "acc_balance", self._cash) or self._cash)
            self._equity = float(getattr(account, "equity", self._equity) or self._equity)
        except Exception:
            return

    def _on_order_update(self, order: Order) -> None:
        # 只在成交時記錄交易
        if str(order.status) != "Filled":
            return
        if not order.code:
            return

        qty = int(order.filled_quantity or order.quantity or 0)
        if qty <= 0:
            return
        if order.filled_price is None:
            return
        fill_price = float(order.filled_price)
        fill_time = str(order.updated_at)

        if order.action == "Buy":
            commission = self._calc_commission(code=order.code, price=fill_price, qty=qty)
            self._open_or_add_position(code=order.code, qty=qty, price=fill_price, ts=fill_time, commission=commission)
        elif order.action == "Sell":
            self._close_position(code=order.code, qty=qty, price=fill_price, ts=fill_time)

    def _open_or_add_position(self, code: str, qty: int, price: float, ts: str, commission: float = 0.0) -> None:
        if self._open_pos is None:
            self._open_pos = _OpenPosition(code=code, qty=qty, avg_price=price, entry_time=ts, entry_commission_total=float(commission))
            return

        if self._open_pos.code != code:
            # MVP：只支援單一標的持倉追蹤（若不同標的，忽略）。
            return

        new_qty = self._open_pos.qty + qty
        if new_qty <= 0:
            return
        new_avg = (self._open_pos.avg_price * self._open_pos.qty + price * qty) / float(new_qty)
        self._open_pos.qty = new_qty
        self._open_pos.avg_price = float(new_avg)
        self._open_pos.entry_commission_total += float(commission)

    def _close_position(self, code: str, qty: int, price: float, ts: str) -> None:
        if self._open_pos is None or self._open_pos.code != code:
            return

        open_qty = int(self._open_pos.qty)
        close_qty = min(int(qty), open_qty)
        if close_qty <= 0:
            return

        entry_price = float(self._open_pos.avg_price)
        multiplier = self._get_contract_multiplier(code)
        gross_pnl = (price - entry_price) * float(close_qty) * float(multiplier)
        entry_commission = 0.0
        if open_qty > 0:
            entry_commission = float(self._open_pos.entry_commission_total) * (float(close_qty) / float(open_qty))
        exit_commission = self._calc_commission(code=code, price=price, qty=close_qty)
        exit_tax = self._calc_tax(code=code, action="Sell", price=price, qty=close_qty)
        pnl = gross_pnl - entry_commission - exit_commission - exit_tax
        pnl_pct = 0.0
        if entry_price != 0:
            base_value = entry_price * float(close_qty) * float(multiplier)
            if base_value != 0:
                pnl_pct = float(pnl) / float(base_value) * 100.0

        trade = TradeResult(
            code=code,
            entry_price=entry_price,
            exit_price=float(price),
            quantity=int(close_qty),
            entry_time=str(self._open_pos.entry_time),
            exit_time=str(ts),
            pnl=float(pnl),
            pnl_pct=float(pnl_pct),
            is_win=bool(pnl > 0),
        )
        self._trade_history.append(trade)

        self._open_pos.qty -= close_qty
        self._open_pos.entry_commission_total = max(0.0, float(self._open_pos.entry_commission_total) - float(entry_commission))
        if self._open_pos.qty <= 0:
            self._open_pos = None

    @staticmethod
    def _get_contract_multiplier(code: str) -> float:
        """回測報表用合約乘數，需與 broker 邏輯一致。"""
        code_str = str(code).strip()
        if code_str.isdigit():
            return 1000.0
        return 1.0

    def _calc_commission(self, *, code: str, price: float, qty: int) -> float:
        trade_value = float(price) * float(qty) * float(self._get_contract_multiplier(code))
        return float(trade_value) * float(self.commission_rate)

    def _calc_tax(self, *, code: str, action: str, price: float, qty: int) -> float:
        if not str(code).strip().isdigit():
            return 0.0
        if str(action) != "Sell":
            return 0.0
        trade_value = float(price) * float(qty) * float(self._get_contract_multiplier(code))
        return float(trade_value) * float(self.tax_rate)

    # ---------------------------- 指標計算/輸出 ---------------------------

    def _build_report(self) -> PerformanceReport:
        equity_curve = self._equity_curve if self._equity_curve else [self.initial_cash]
        initial = float(equity_curve[0])
        final = float(equity_curve[-1])

        total_return_pct = 0.0
        if initial != 0:
            total_return_pct = (final - initial) / initial * 100.0

        total_trades = len(self._trade_history)
        winning_trades = sum(1 for t in self._trade_history if t.is_win)
        losing_trades = total_trades - winning_trades
        win_rate_pct = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        max_drawdown_pct = self._max_drawdown_pct(equity_curve)
        sharpe_ratio = self._sharpe_ratio(equity_curve)

        avg_win_pct = 0.0
        avg_loss_pct = 0.0
        profit_factor = 0.0
        if total_trades > 0:
            wins = [t.pnl_pct for t in self._trade_history if t.pnl > 0]
            losses = [t.pnl_pct for t in self._trade_history if t.pnl < 0]
            if wins:
                avg_win_pct = sum(wins) / float(len(wins))
            if losses:
                avg_loss_pct = sum(losses) / float(len(losses))

            gross_profit = sum(t.pnl for t in self._trade_history if t.pnl > 0)
            gross_loss = -sum(t.pnl for t in self._trade_history if t.pnl < 0)
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss

        return PerformanceReport(
            total_return_pct=float(total_return_pct),
            win_rate_pct=float(win_rate_pct),
            max_drawdown_pct=float(max_drawdown_pct),
            sharpe_ratio=float(sharpe_ratio),
            total_trades=int(total_trades),
            winning_trades=int(winning_trades),
            losing_trades=int(losing_trades),
            avg_win_pct=float(avg_win_pct),
            avg_loss_pct=float(avg_loss_pct),
            profit_factor=float(profit_factor),
            equity_curve=list(map(float, equity_curve)),
            trade_history=list(self._trade_history),
            equity_timestamps=list(self._equity_timestamps),
        )

    @staticmethod
    def _max_drawdown_pct(equity_curve: List[float]) -> float:
        peak = -float("inf")
        max_dd = 0.0
        for v in equity_curve:
            val = float(v)
            if val > peak:
                peak = val
            if peak > 0:
                dd = (peak - val) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd * 100.0

    @staticmethod
    def _sharpe_ratio(equity_curve: List[float]) -> float:
        if len(equity_curve) < 2:
            return 0.0

        returns: List[float] = []
        for i in range(1, len(equity_curve)):
            prev = float(equity_curve[i - 1])
            cur = float(equity_curve[i])
            if prev <= 0:
                continue
            returns.append(cur / prev - 1.0)

        if len(returns) < 2:
            return 0.0

        mean_r = sum(returns) / float(len(returns))
        var = sum((r - mean_r) ** 2 for r in returns) / float(len(returns) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0

        # 年化：252 個交易日；無風險利率 1.5%（年化）
        annual_mean = mean_r * 252.0
        annual_std = std * math.sqrt(252.0)
        rf = 0.015
        if annual_std == 0:
            return 0.0
        return (annual_mean - rf) / annual_std

    def _reset_state(self) -> None:
        self._trade_history = []
        self._equity_curve = []
        self._equity_timestamps = []
        self._cash = self.initial_cash
        self._last_price = 0.0
        self._open_pos = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

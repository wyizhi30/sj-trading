from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import BaseStrategy
from ..schemas import KBar, Signal


@dataclass
class MACrossStrategy(BaseStrategy):
    """均線交叉策略（MVP）。

    規則：
    - 短均線從下往上穿越長均線（黃金交叉）→ Buy
    - 短均線從上往下穿越長均線（死亡交叉）→ Sell

    防呆：
    - Buy 前確認目前無持倉（position == 0）
    - Sell 前確認目前有持倉（position > 0）
    """

    strategy_id: str
    code: str
    
    short_window: int = 5
    long_window: int = 20
    quantity: int = 1
    order_type: str = "MARKET"

    _closes: list[float] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if int(self.quantity) <= 0:
            raise ValueError("quantity 必須大於 0")

        if int(self.short_window) <= 0:
            raise ValueError("short_window 必須大於 0")

        if int(self.long_window) <= 0:
            raise ValueError("long_window 必須大於 0")

        if int(self.short_window) >= int(self.long_window):
            raise ValueError("short_window 必須小於 long_window")

        order_type = str(self.order_type).strip().upper()
        if order_type not in ("MARKET", "LIMIT"):
            raise ValueError("order_type 必須是 MARKET 或 LIMIT")
        self.order_type = order_type

        super().__init__(self.strategy_id, self.code)

    # ----------------------------
    # core logic
    # ----------------------------
    def on_kbar(self, kbar: KBar) -> Optional[Signal]:
        """接收 KBar，判斷是否產生 Buy/Sell 訊號。

        Args:
        kbar: 內部 KBar（欄位名稱首字大寫，例如 `Close`）。

        Returns:
        有訊號時回傳 Signal，否則回傳 None。
        """
        
        if not self.is_running:
            return None

        if kbar.code != self.code:
            return None

        self._closes.append(float(kbar.Close))

        max_len = self.long_window * 2
        if len(self._closes) > max_len:
            self._closes = self._closes[-max_len:]

        # 需要至少 long_window + 1 根才能計算「今日」與「昨日」長均線。
        if len(self._closes) < self.long_window + 1:
            return None

        short_today = self._ma(self._closes, self.short_window)
        long_today = self._ma(self._closes, self.long_window)
        short_yesterday = self._ma(self._closes[:-1], self.short_window)
        long_yesterday = self._ma(self._closes[:-1], self.long_window)

        action = None

        if short_today > long_today and short_yesterday <= long_yesterday:
            action = "Buy"

        elif short_today < long_today and short_yesterday >= long_yesterday:
            action = "Sell"

        else:
            return None

        position = self.get_current_position()

        if action == "Buy" and position > 0:
            return None
        if action == "Sell" and position == 0:
            return None

        signal = Signal(
            strategy_id=self.strategy_id,
            code=self.code,
            action=action,
            order_type=self.order_type,
            price=None if self.order_type == "MARKET" else float(kbar.Close),
            quantity=self.quantity,
            timestamp=str(kbar.ts),
        )

        if not self.risk_guard.validate_signal(signal):
            return None

        return signal

    # ----------------------------
    # indicator helper (local OK)
    # ----------------------------
    @staticmethod
    def _ma(values: list[float], window: int) -> float:
        """計算簡單移動平均（SMA）。"""
        
        tail = values[-window:]
        return sum(tail) / float(window)
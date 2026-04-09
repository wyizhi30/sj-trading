"""Strategy 模組骨架

模組職責:
- 接收 MARKET_TICK / MARKET_KBAR 等事件
- 產生 SIGNAL_CREATED 事件給 Broker
- 不可直接操作 Broker 內部狀態

事件介面與 payload 規範:
- 訂閱事件: MARKET_TICK
  payload 範例:
  {
    "symbol": "2330.TW",
    "ts": "2026-04-08T09:00:00.123456",
    "close": 625.0,
    "volume": 3,
    "bid": 624.5,
    "ask": 625.0,
    "tick_type": 1
  }

- 訂閱事件: MARKET_KBAR
  payload 範例:
  {
    "symbol": "2330.TW",
    "start_ts": "2026-04-08T09:00:00",
    "end_ts": "2026-04-08T09:01:00",
    "open": 624.5,
    "high": 625.5,
    "low": 624.0,
    "close": 625.0,
    "volume": 120
  }

- 訂閱事件: ORDER_UPDATED / POSITION_UPDATED / ACCOUNT_UPDATED
  payload 請對應 broker_interface.py 的 Order/Position/Account

- 發送事件: SIGNAL_CREATED
  payload 範例:
  {
    "signal": {
      "symbol": "2330.TW",
      "action": "BUY",
      "price": 625.0,
      "volume": 1,
      "strategy_id": "ma_cross_v1",
      "reason": "ma5_cross_up_ma20"
    }
  }

- 發送事件 (可選): UI_REFRESH
  payload 範例:
  {
    "warning": "risk guard blocked signal",
    "signal": {"symbol": "2330.TW", "action": "BUY"}
  }
"""

from __future__ import annotations

from dataclasses import dataclass

from .broker_interface import Account, KBar, Position, Signal, Tick


@dataclass
class RiskGuard:
    """風控檢查骨架。"""

    max_single_order_value: float = 200000.0

    def validate_signal(self, signal: Signal, position: Position, account: Account) -> bool:
        raise NotImplementedError


class BaseStrategy:
    """策略基底骨架。"""

    strategy_id: str = "base_strategy"

    def on_tick(self, tick: Tick) -> Signal | None:
        raise NotImplementedError

    def on_kbar(self, kbar: KBar) -> Signal | None:
        raise NotImplementedError

    def generate_signal(self, context: dict) -> Signal | None:
        raise NotImplementedError

    def emit_signal_event(self, signal: Signal) -> None:
        """包裝 SIGNAL_CREATED EventEnvelope 並發送到事件總線。"""
        raise NotImplementedError


class MovingAverageCrossStrategy(BaseStrategy):
    """MA 交叉策略骨架。"""

    strategy_id: str = "ma_cross_v1"

    def on_tick(self, tick: Tick) -> Signal | None:
        raise NotImplementedError

    def on_kbar(self, kbar: KBar) -> Signal | None:
        raise NotImplementedError

    def generate_signal(self, context: dict) -> Signal | None:
        raise NotImplementedError

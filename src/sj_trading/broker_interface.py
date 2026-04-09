"""BrokerInterface 模組骨架

模組職責:
- 定義實盤與回測共用的 Broker 介面 (BrokerInterface)
- 統一事件封包 EventEnvelope 與常用 payload 結構註解

事件介面與 payload 規範:
- 訂閱事件: SIGNAL_CREATED
  payload 範例:
  {
    "signal": {
      "symbol": "2330.TW",
      "action": "BUY",
      "price": 625.0,
      "volume": 1,
      "strategy_id": "ma_cross_v1",
      "reason": "ma_cross_up"
    }
  }

- 發送事件: ORDER_SUBMITTED
  payload 範例:
  {
    "order_id": "uuid-string",
    "symbol": "2330.TW",
    "action": "BUY",
    "price": 625.0,
    "volume": 1,
    "status": "SUBMITTED"
  }

- 發送事件: ORDER_UPDATED
  payload 範例:
  {
    "order_id": "uuid-string",
    "status": "PART_FILLED",
    "filled_price": 625.0,
    "filled_volume": 1,
    "raw": {"op_code": "00", "seqno": "267677", "ordno": "IM394"}
  }

- 發送事件: POSITION_UPDATED
  payload 範例:
  {
    "symbol": "2330.TW",
    "avg_price": 625.0,
    "volume": 1,
    "unrealized_pnl": 0.0
  }

- 發送事件: ACCOUNT_UPDATED
  payload 範例:
  {
    "cash": 1000000,
    "equity": 1005000,
    "PnL": 5000,
    "available_margin": 800000
  }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol


@dataclass
class Signal:
    symbol: str
    action: str
    price: float
    volume: int
    strategy_id: str
    reason: str = ""
    ts: str = ""


@dataclass
class Order:
    order_id: str
    symbol: str
    action: str
    price: float
    volume: int
    status: str
    filled_price: float = 0.0
    filled_volume: int = 0
    timestamp: str = ""
    broker_ref: str = ""


@dataclass
class Position:
    symbol: str
    avg_price: float
    volume: int
    unrealized_pnl: float = 0.0


@dataclass
class Account:
    cash: float
    equity: float
    pnl: float
    available_margin: float = 0.0


@dataclass
class Tick:
    symbol: str
    ts: str
    close: float
    volume: int
    bid: float = 0.0
    ask: float = 0.0
    tick_type: int = 0


@dataclass
class KBar:
    symbol: str
    start_ts: str
    end_ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class PerformanceMetrics:
    return_rate: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    trade_count: int


@dataclass
class EventEnvelope:
    event_id: str
    event_type: str
    source: str
    ts: str
    symbol: str
    payload: Dict[str, Any]
    trace_id: str = ""


class BrokerInterface(Protocol):
    """實盤與回測共用介面。

    注意:
    - BacktestEngine 與 Live Runtime 都要依賴這個介面。
    - Strategy 只發送 SIGNAL_CREATED，不應直接改 Broker 內部狀態。
    """

    def initialize(self, simulation: bool = True) -> None:
        raise NotImplementedError

    def submit_signal(self, signal: Signal) -> Order:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> Order:
        raise NotImplementedError

    def update_order(self, order_id: str, price: float | None, qty: int | None) -> Order:
        raise NotImplementedError

    def sync_status(self) -> list[Order]:
        raise NotImplementedError

    def on_order_callback(self, stat: str, msg: dict) -> None:
        raise NotImplementedError


class LiveBrokerGateway:
    """實盤 Broker 骨架 (例如接 Shioaji)。"""

    def initialize(self, simulation: bool = False) -> None:
        raise NotImplementedError

    def submit_signal(self, signal: Signal) -> Order:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> Order:
        raise NotImplementedError

    def update_order(self, order_id: str, price: float | None, qty: int | None) -> Order:
        raise NotImplementedError

    def sync_status(self) -> list[Order]:
        raise NotImplementedError

    def on_order_callback(self, stat: str, msg: dict) -> None:
        raise NotImplementedError


class SimulatedBrokerGateway:
    """回測/模擬 Broker 骨架。

    與 LiveBrokerGateway 共用同一組 BrokerInterface 方法，確保實盤與回測一致。
    """

    def initialize(self, simulation: bool = True) -> None:
        raise NotImplementedError

    def submit_signal(self, signal: Signal) -> Order:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> Order:
        raise NotImplementedError

    def update_order(self, order_id: str, price: float | None, qty: int | None) -> Order:
        raise NotImplementedError

    def sync_status(self) -> list[Order]:
        raise NotImplementedError

    def on_order_callback(self, stat: str, msg: dict) -> None:
        raise NotImplementedError

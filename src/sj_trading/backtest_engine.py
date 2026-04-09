"""BacktestEngine 模組骨架

模組職責:
- 使用歷史 Tick/KBar 重播策略
- 透過 BrokerInterface 執行模擬成交 (不得繞過 broker 直接改資金/倉位)
- 計算績效並發送 BACKTEST_FINISHED

關鍵一致性:
- 回測與實盤都依賴同一個 BrokerInterface
- Signal -> Order 轉換流程保持一致

事件介面與 payload 規範:
- 訂閱事件: SIGNAL_CREATED
  payload 範例:
  {
    "signal": {
      "symbol": "2330.TW",
      "action": "BUY",
      "price": 625.0,
      "volume": 1,
      "strategy_id": "ma_cross_v1"
    }
  }

- 訂閱事件: MARKET_KBAR
  payload 請對應 KBar 結構

- 發送事件: ORDER_UPDATED / POSITION_UPDATED / ACCOUNT_UPDATED
  payload 請對應 Order / Position / Account 結構

- 發送事件: BACKTEST_FINISHED
  payload 範例:
  {
    "metrics": {
      "return_rate": 0.12,
      "win_rate": 0.58,
      "max_drawdown": 0.09,
      "sharpe_ratio": 1.34,
      "trade_count": 178
    },
    "final_account": {
      "cash": 1050000,
      "equity": 1120000,
      "PnL": 120000
    }
  }
"""

from __future__ import annotations

from .broker_interface import BrokerInterface, KBar, Order, PerformanceMetrics, Tick


class ExecutionSimulator:
    """模擬成交骨架。"""

    def simulate_fill(self, order: Order, market: KBar | Tick) -> Order:
        raise NotImplementedError


class MetricsCalculator:
    """績效計算骨架。"""

    def compute_metrics(self, equity_curve: list[float], trades: list[Order]) -> PerformanceMetrics:
        raise NotImplementedError


class BacktestEngine:
    """回測引擎骨架。

    注意:
    - 透過 constructor 注入 BrokerInterface，確保與實盤共用相同 broker 介面。
    """

    def __init__(self, broker: BrokerInterface) -> None:
        self.broker = broker

    def run(self, data: list[KBar] | list[Tick]) -> dict:
        raise NotImplementedError

    def on_signal_created(self, event: dict) -> None:
        """處理 SIGNAL_CREATED 事件。"""
        raise NotImplementedError

    def on_market_kbar(self, event: dict) -> None:
        """處理 MARKET_KBAR 事件。"""
        raise NotImplementedError

    def publish_backtest_finished(self, metrics: PerformanceMetrics, final_account: dict) -> None:
        """發送 BACKTEST_FINISHED 事件。"""
        raise NotImplementedError

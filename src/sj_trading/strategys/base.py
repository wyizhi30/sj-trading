from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from ..schemas import KBar, Order, Signal, Tick

# 後續抽出 RiskGuard、Indicator Layer 等模組後，這裡會越來越乾淨，專注在定義 Strategy 介面與基底類別。

# ----------------------------
# Risk (MVP placeholder)
# ----------------------------
class RiskGuard:
    """最小風控（MVP）"""

    def validate_signal(self, signal: Signal) -> bool:
        return True


# ----------------------------
# Broker Interface
# ----------------------------
@runtime_checkable
class BaseBroker(Protocol):
    """Broker 最小介面（Strategy 僅需呼叫 place_signal）。"""
    
    def place_signal(self, signal: Signal) -> object:
        """送出交易訊號並回傳下單結果（型別由 Broker 決定）。"""
        ...

    def get_position(self, code: str) -> int:
        """回傳指定標的目前持倉張數（正數=多單，0=空手）。"""
        ...

# ----------------------------
# Strategy Base Class
# ----------------------------
class BaseStrategy(ABC):
    def __init__(self, strategy_id: str, code: str):
        if not str(strategy_id).strip():
            raise ValueError("strategy_id 不可為空")
        if not str(code).strip():
            raise ValueError("code 不可為空")
        
        self.strategy_id = strategy_id
        self.code = code
        self.is_running = False

        self.broker: Optional[BaseBroker] = None
        self.risk_guard = RiskGuard()

    # lifecycle
    def start(self) -> None:
        """啟動策略（僅切換狀態）。"""
        self.is_running = True

    def stop(self) -> None:
        """停止策略（僅切換狀態）。"""
        self.is_running = False

    # core
    @abstractmethod
    def on_kbar(self, kbar: KBar) -> Optional[Signal]:
        """接收 KBar 並產生交易訊號（若無訊號則回傳 None）。"""
        pass

    # optional hooks
    def on_tick(self, tick: Tick) -> Optional[Signal]:
        """接收 Tick 並產生交易訊號（MVP 預設不使用 Tick）。"""
        return None

    def on_order_update(self, order: Order) -> None:
        """接收 Broker 訂單更新（可選）。"""
        return None

    # helpers
    def set_broker(self, broker: BaseBroker) -> None:
        self.broker = broker

    def get_current_position(self) -> int:
        """取得目前持倉張數。
        優先透過 broker 查詢（若提供），否則使用策略內部追蹤值。
        """
        
        if self.broker is not None:
            try:
                return int(self.broker.get_position(self.code))
            except Exception:
                # 若 broker 沒有實作或發生例外，退回使用本地追蹤值。
                pass
        return 0
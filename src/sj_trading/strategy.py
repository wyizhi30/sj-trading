"""Strategy 模組。

模組職責：
- 接收 MarketData 推送的 Tick / KBar
- 根據策略邏輯產生 Signal（交由外部或 Broker 執行下單）

重要原則：
- Strategy **不可直接操作 Broker 內部狀態**。
- 本檔案只包含最基本的 MVP 策略實作，不做額外優化。

本專案的資料結構以 `sj_trading.schemas` 為準。
"""

from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .schemas import KBar, Order, Signal, Tick


@dataclass
class RiskGuard:
  """最小風控檢查（MVP）。

  目前僅保留介面，預設全部放行。
  """

  max_single_order_value: float = 200000.0

  def validate_signal(self, signal: Signal) -> bool:
    """檢查訊號是否允許送出。

    Args:
      signal: 欲送出的交易訊號。

    Returns:
      True 表示允許；False 表示阻擋。
    """

    return True


@runtime_checkable
class BaseBroker(Protocol):
  """Broker 最小介面（Strategy 僅需呼叫 place_signal）。"""

  def place_signal(self, signal: Signal) -> object:
    """送出交易訊號並回傳下單結果（型別由 Broker 決定）。"""

  def get_position(self, code: str) -> int:
    """回傳指定標的目前持倉張數（正數=多單，0=空手）。"""


class BaseStrategy:
  """策略基底類別（MVP）。"""

  strategy_id: str
  code: str
  broker: Optional[BaseBroker]
  is_running: bool

  def on_kbar(self, kbar: KBar) -> Optional[Signal]:
    """接收 KBar 並產生交易訊號（若無訊號則回傳 None）。"""

    raise NotImplementedError

  def on_tick(self, tick: Tick) -> Optional[Signal]:
    """接收 Tick 並產生交易訊號（MVP 預設不使用 Tick）。"""

    return None

  def on_order_update(self, order: Order) -> None:
    """接收 Broker 訂單更新（可選）。"""

    return None

  def start(self) -> None:
    """啟動策略（僅切換狀態）。"""

    self.is_running = True

  def stop(self) -> None:
    """停止策略（僅切換狀態）。"""

    self.is_running = False

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
  broker: Optional[BaseBroker] = None
  short_window: int = 5
  long_window: int = 20
  quantity: int = 1
  order_type: str = "MARKET"
  is_running: bool = False
  risk_guard: RiskGuard = field(default_factory=RiskGuard)

  _closes: list[float] = field(default_factory=list, init=False, repr=False)
  _position_qty: int = field(default=0, init=False, repr=False)

  def get_current_position(self) -> int:
    """取得目前持倉張數。

    - 若 broker 提供 `get_position()`，優先使用 broker 資訊。
    - 否則回傳策略內部追蹤值（由 `on_order_update()` 更新）。
    """

    if self.broker is not None:
      try:
        return int(self.broker.get_position(self.code))
      except Exception:
        pass
    return int(self._position_qty)

  def on_order_update(self, order: Order) -> None:
    """接收訂單更新並更新策略內部持倉（MVP）。

    只處理已成交（Filled）的訂單：
    - Buy：持倉 += quantity
    - Sell：持倉 -= quantity（不做額外下限保護，由上游確保正確）
    """

    if getattr(order, "code", None) != self.code:
      return

    status = str(getattr(order, "status", "")).strip().lower()
    if status != "filled":
      return

    action = str(getattr(order, "action", "")).strip().lower()
    qty = int(getattr(order, "quantity", 0) or 0)
    if qty <= 0:
      return

    if action == "buy":
      self._position_qty += qty
    elif action == "sell":
      self._position_qty -= qty

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
    max_len = max(self.long_window * 2, self.long_window + 1)
    if len(self._closes) > max_len:
      self._closes = self._closes[-max_len:]

    # 需要至少 long_window + 1 根才能計算「今日」與「昨日」長均線。
    if len(self._closes) < self.long_window + 1:
      return None
    if self.short_window <= 0 or self.long_window <= 0:
      return None
    if self.short_window >= self.long_window:
      # MVP：不做參數優化；若短期不小於長期，直接不產生訊號。
      return None

    short_today = self._ma(self._closes, self.short_window)
    long_today = self._ma(self._closes, self.long_window)
    short_yesterday = self._ma(self._closes[:-1], self.short_window)
    long_yesterday = self._ma(self._closes[:-1], self.long_window)

    action: Optional[str] = None
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
      price=None if self.order_type.upper() == "MARKET" else float(kbar.Close),
      quantity=int(self.quantity),
      timestamp=str(kbar.ts),
    )

    if not self.risk_guard.validate_signal(signal):
      return None
    return signal

  @staticmethod
  def _ma(values: list[float], window: int) -> float:
    """計算簡單移動平均（SMA）。"""

    tail = values[-window:]
    return sum(tail) / float(window)


# 舊名稱相容：避免外部仍使用舊 class 名稱。
MovingAverageCrossStrategy = MACrossStrategy

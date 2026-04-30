# Strategy 模組文件

## 模組職責

`strategy.py` 負責**根據市場資料自動判斷買賣時機**，產生 Signal 交給 Broker 執行。

包含：
- `BaseStrategy`：所有策略的共同介面
- `MACrossStrategy`：均線交叉範例策略

> ⚠️ **重要原則**：Strategy **不可直接操作 Broker 內部狀態**，只能透過 `broker.place_signal(signal)` 下單。

---

## 模組依賴關係

```
schemas.py     ←┐
market_data.py  ├─ strategy.py
broker.py      ←┘（只呼叫 place_signal）
```

---

## BaseStrategy 介面

```python
class BaseStrategy:
    strategy_id: str          # 策略名稱（唯一識別碼）
    code: str                 # 交易標的代碼，例如 "2330"
    broker: BaseBroker        # Broker 參考
    is_running: bool          # 是否正在運行

    def on_kbar(self, kbar: KBar) -> Optional[Signal]: ...   # 必須實作
    def on_tick(self, tick: Tick) -> Optional[Signal]: ...   # 選擇性實作
    def on_order_update(self, order: Order) -> None: ...     # 選擇性實作
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def get_current_position(self) -> int: ...  # 正數=持有張數，0=空手
```

---

## MACrossStrategy（均線交叉策略）

### 策略邏輯

- 計算**短期均線**（預設 5 根）與**長期均線**（預設 20 根）
- 短均線**從下往上穿越**長均線（黃金交叉）→ 發出 `Buy` Signal
- 短均線**從上往下穿越**長均線（死亡交叉）→ 發出 `Sell` Signal

### 初始化參數

```python
MACrossStrategy(
    strategy_id: str,           # 例如 "ma_cross_v1"
    code: str,                  # 例如 "2330"（不帶 .TW）
    broker: BaseBroker,
    short_window: int = 5,
    long_window: int = 20,
    quantity: int = 1,          # 每次交易張數
    order_type: str = "MARKET"
)
```

### 參數驗證規則

`MACrossStrategy` 在建立物件時會立即檢查參數，避免無效設定進入回測或實盤流程：

| 參數 | 規則 | 錯誤行為 |
|------|------|---------|
| `strategy_id` | 不能為空字串 | 拋出 `ValueError` |
| `code` | 不能為空字串 | 拋出 `ValueError` |
| `quantity` | 必須大於 0 | 拋出 `ValueError` |
| `short_window` | 必須大於 0 | 拋出 `ValueError` |
| `long_window` | 必須大於 0 | 拋出 `ValueError` |
| `short_window` / `long_window` | `short_window < long_window` | 拋出 `ValueError` |
| `order_type` | 只能是 `MARKET` 或 `LIMIT` | 拋出 `ValueError` |

補充：
- `order_type` 會在初始化時轉成大寫，確保後續邏輯一致。
- 這些限制與 `strategy.py` 的 `__post_init__` 驗證一致。

---

## on_kbar 方法

**Input：**
```python
KBar(
    code="2330",
    ts="2026-04-08T09:00:00",
    Open=624.0, High=629.0, Low=622.0, Close=627.0,
    Volume=32000, Amount=20064000,
    interval="1d"
)
```

**Output（有交叉訊號）：**
```python
Signal(
    strategy_id="ma_cross_v1",
    code="2330",          # 純數字，不帶 .TW
    action="Buy",         # "Buy" 或 "Sell"，首字大寫
    order_type="MARKET",
    price=None,
    quantity=1,
    timestamp="2026-04-08T09:00:00"
)
```

**Output（無訊號）：** `None`

### 內部邏輯

```
收到 KBar
    ↓
將 Close 加入價格序列（只保留最近 long_window × 2 筆）
    ↓
若序列長度 < long_window → 回傳 None（資料不足）
    ↓
計算今日短均線、長均線
計算昨日短均線、長均線
    ↓
今日短 > 長 且 昨日短 < 長 → 黃金交叉 → BUY
今日短 < 長 且 昨日短 > 長 → 死亡交叉 → SELL
否則 → None
    ↓
防呆：BUY 前確認 get_current_position() == 0
     SELL 前確認 get_current_position() > 0
```

---

## 防呆機制

避免重複下單或空手賣出：

```python
if signal.action == "Buy" and self.get_current_position() > 0:
    # 已有持倉，忽略 BUY 訊號
    logger.warning("已有持倉，忽略 Buy 訊號")
    return None

if signal.action == "Sell" and self.get_current_position() == 0:
    # 空手，忽略 SELL 訊號
    logger.warning("空手狀態，忽略 Sell 訊號")
    return None
```

---

## 自訂策略範例

```python
from strategy import BaseStrategy
from schemas import KBar, Signal
from typing import Optional

class RSIStrategy(BaseStrategy):
    """RSI 超買超賣策略"""

    def __init__(self, strategy_id, code, broker, rsi_period=14):
        super().__init__(strategy_id, code, broker)
        self.rsi_period = rsi_period
        self.prices = []

    def on_kbar(self, kbar: KBar) -> Optional[Signal]:
        self.prices.append(kbar.Close)   # 注意 KBar 欄位大寫

        if len(self.prices) < self.rsi_period + 1:
            return None

        rsi = self._calc_rsi()

        if rsi < 30 and self.get_current_position() == 0:
            return Signal(
                strategy_id=self.strategy_id,
                code=self.code,       # 純數字，例如 "2330"
                action="Buy",
                order_type="MARKET",
                price=None,
                quantity=1,
                timestamp=kbar.ts
            )
        elif rsi > 70 and self.get_current_position() > 0:
            return Signal(
                strategy_id=self.strategy_id,
                code=self.code,
                action="Sell",
                order_type="MARKET",
                price=None,
                quantity=1,
                timestamp=kbar.ts
            )
        return None
```

---

## 範例程式碼：實盤啟動

```python
import shioaji as sj
from broker import Broker
from strategy import MACrossStrategy
from market_data import MarketData

# 初始化 Shioaji
api = sj.Shioaji(simulation=True)
api.login(api_key="YOUR_KEY", secret_key="YOUR_SECRET", contracts_timeout=10000)

# 建立 Broker
broker = Broker(api)

# 建立策略（code 用純數字）
strategy = MACrossStrategy(
    strategy_id="ma_cross_v1",
    code="2330",         # ✅ 不帶 .TW
    broker=broker,
    short_window=5,
    long_window=20,
    quantity=1
)

# 訂閱 Broker 事件
broker.on_order_update(strategy.on_order_update)

# 建立 MarketData，訂閱 K 棒
md = MarketData(api)
md.subscribe_kbar("2330", strategy.on_kbar)

strategy.start()
```

---

## 錯誤處理

| 情況 | 處理方式 |
|------|---------|
| K 棒資料不足 | 回傳 `None`，不下單 |
| 下單回報 Failed | 記錄 log，策略繼續運行 |
| on_kbar 計算例外 | 捕捉例外，記錄 log，不中斷策略 |
| 重複買進/賣出 | 防呆機制忽略，記錄 warning |

---

## 注意事項

1. `code` 欄位使用**純數字**，例如 `"2330"`
2. `action` 值使用**首字大寫**：`"Buy"` / `"Sell"`
3. KBar 欄位名稱**首字大寫**：`Open`、`High`、`Low`、`Close`、`Volume`
4. `on_kbar` 回傳 Signal 後，由外部（`main.py` 或 `Backtest`）呼叫 `broker.place_signal()`
5. 收盤價序列只保留最近 `long_window × 2` 筆，避免記憶體無限增長
6. 實盤與回測使用完全相同的 Strategy 程式碼，差別只在傳入的 `broker` 是 `Broker` 還是 `MockBroker`

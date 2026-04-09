# UI 模組文件

## 模組職責

`ui.py` 提供**桌面視窗介面**，讓使用者可以：

- 即時查看帳戶資金與損益（來自 `Account.acc_balance`）
- 監控所有訂單狀態（Submitted、Filled、Cancelled 等）
- 查看持倉與未實現損益（來自 `Position.pnl`）
- 瀏覽回測績效報告與淨值曲線

UI 完全透過**訂閱 Broker 事件**接收更新，不直接呼叫任何 Shioaji API。

---

## 模組依賴關係

```
schemas.py  ←  ui.py
broker.py（訂閱事件，不修改狀態）
backtest.py（顯示 PerformanceReport）
```

---

## 技術選擇

| 元件 | 用途 |
|------|------|
| `tkinter` | 視窗框架、標籤、表格（Python 內建） |
| `tkinter.ttk` | Treeview 表格元件 |
| `matplotlib` | 淨值曲線圖 |
| `matplotlib.backends.backend_tkagg` | 嵌入 tkinter |

---

## 視窗佈局

```
┌─────────────────────────────────────────────────────┐
│  sj-trading  [模擬模式]          最後更新：10:32:15  │
├─────────────────────────────────────────────────────┤
│  帳戶餘額：$980,000   總資產估算：$989,000           │
│  未實現損益：+$9,000  已實現損益：+$2,000            │
├────────────────────┬────────────────────────────────┤
│  即時訂單          │  持倉明細                       │
│  [訂單 Treeview]   │  [持倉 Treeview]                │
├────────────────────┴────────────────────────────────┤
│  [即時監控] [回測績效]                               │
│  [淨值曲線圖 / 回測報告]                             │
└─────────────────────────────────────────────────────┘
```

---

## 主要類別與方法

### 初始化

```python
TradingUI(
    broker: BaseBroker,
    mode: str = "live"   # "live" 或 "backtest"
)
```

### `start()`

啟動視窗，進入主事件迴圈（blocking）。

### `show_backtest_report(report: PerformanceReport)`

在「回測績效」頁籤顯示報告與淨值曲線圖。

---

## 各面板規格

### 帳戶總覽

顯示 `Account` 物件的欄位（來自 Shioaji `AccountBalance` + `list_positions`）：

| 顯示項目 | 來源欄位 | 顏色 |
|---------|---------|------|
| 帳戶餘額 | `Account.acc_balance` | 白色 |
| 未實現損益 | `Account.unrealized_pnl` | 正綠負紅 |
| 已實現損益 | `Account.realized_pnl` | 正綠負紅 |
| 總資產估算 | `Account.equity` | 白色 |

### 訂單列表欄位

| 欄位 | 說明 |
|------|------|
| 時間 | `Order.created_at` |
| 代碼 | `Order.code`（純數字） |
| 動作 | `Order.action`（Buy / Sell） |
| 類型 | `Order.order_type` |
| 委託價 | `Order.price` |
| 成交價 | `Order.filled_price` |
| 數量 | `Order.quantity` |
| 狀態 | `Order.status` |

**狀態顏色（對應 Shioaji Status）：**

| 狀態 | 顏色 |
|------|------|
| `Filled` | 綠色 |
| `Submitted` / `Inactive` | 黃色 |
| `Cancelled` | 灰色 |
| `Failed` | 紅色 |

### 持倉列表欄位

| 欄位 | 來源 |
|------|------|
| 代碼 | `Position.code` |
| 方向 | `Position.direction`（Buy / Sell） |
| 均價 | `Position.price` |
| 張數 | `Position.quantity` |
| 目前價 | `Position.last_price` |
| 未實現損益 | `Position.pnl` |

---

## 即時更新機制

Broker 事件可能從其他執行緒觸發，tkinter 只能在主執行緒更新 UI，需使用 queue：

```python
# Broker 事件（任何執行緒）→ 放入 queue
# tkinter 主執行緒每 100ms 讀取 queue → 更新 UI

def _poll_queue(self):
    while not self._queue.empty():
        event_type, data = self._queue.get()
        if event_type == "order":
            self._refresh_order_table(data)
        elif event_type == "account":
            self._refresh_account_panel(data)
        elif event_type == "position":
            self._refresh_position_table(data)
    self.root.after(100, self._poll_queue)
```

---

## 範例程式碼

### 實盤監控視窗

```python
import shioaji as sj
from broker import Broker
from strategy import MACrossStrategy
from market_data import MarketData
from ui import TradingUI

api = sj.Shioaji(simulation=True)
api.login(api_key="YOUR_KEY", secret_key="YOUR_SECRET", contracts_timeout=10000)

broker = Broker(api)

strategy = MACrossStrategy(
    strategy_id="ma_cross_v1",
    code="2330",     # 純數字
    broker=broker,
    short_window=5,
    long_window=20
)
broker.on_order_update(strategy.on_order_update)

md = MarketData(api)
md.subscribe_kbar("2330", strategy.on_kbar)

ui = TradingUI(broker=broker, mode="live")
strategy.start()
ui.start()  # 阻塞，視窗關閉後繼續執行
api.logout()
```

### 回測後顯示報告

```python
from broker import MockBroker
from strategy import MACrossStrategy
from backtest import Backtester
from ui import TradingUI

mock_broker = MockBroker()
strategy = MACrossStrategy("ma_cross_v1", "2330", mock_broker)
backtester = Backtester(strategy, initial_cash=1_000_000)
report = backtester.run_from_csv("data/2330_1d.csv", code="2330")

ui = TradingUI(broker=mock_broker, mode="backtest")
ui.show_backtest_report(report)
ui.start()
```

---

## 錯誤處理

| 情況 | 處理方式 |
|------|---------|
| Broker 斷線 | 狀態列顯示「連線中斷」，資料停止更新但不清除 |
| 更新頻率過高 | queue 上限 500 筆，超過時丟棄最舊事件 |
| matplotlib 繪圖錯誤 | 顯示錯誤訊息，不讓視窗崩潰 |
| 視窗關閉 | 呼叫 `strategy.stop()`，並觸發 `api.logout()` |

---

## 注意事項

1. `ui.start()` 為**阻塞式**，必須放在程式最後一行
2. 金額顯示加入千分位格式（`1,000,000` 而非 `1000000`）
3. 淨值曲線超過 1000 點建議降採樣後再繪製
4. 帳戶欄位名稱使用 `acc_balance`（對應 Shioaji `AccountBalance.acc_balance`），不是 `cash`

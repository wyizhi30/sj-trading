# Schemas 模組文件

## 模組職責

`schemas.py` 是整個系統的**資料格式定義中心**。所有模組（Broker、Strategy、MarketData、Backtest、UI）都必須使用這裡定義的資料結構，確保模組之間傳遞的資料格式一致。

> ⚠️ **重要原則**：此模組**不包含任何邏輯**，只定義資料長什麼樣子。任何模組都可以 import，但此模組不應 import 任何其他自訂模組。

---

## 與 Shioaji 原生型別的關係

本系統定義自己的內部 Schema，在 Broker 層負責與 Shioaji 原生型別互相轉換。

| 本系統 Schema | Shioaji 原生型別 |
|-------------|----------------|
| `Signal` | 無對應（系統自訂） |
| `Order` | `Trade`（含 `contract`、`order`、`status`） |
| `Position` | `StockPosition` / `FuturePosition` |
| `Account` | `AccountBalance`（現股）/ `Margin`（期貨） |
| `Tick` | `TickSTKv1` / `TickFOPv1` |
| `KBar` | `Kbars` DataFrame |

---

## 重要：股票代碼格式

Shioaji API 使用**純數字或代碼**，不帶任何後綴：

```python
# ✅ 正確
code = "2330"        # 台積電
code = "TXFA5"       # 台指期近月

# ❌ 錯誤（Shioaji 不認識此格式）
code = "2330.TW"
```

---

## 資料結構總覽

| 類別 | 用途 | 主要流向 |
|------|------|---------|
| `Signal` | Strategy 發出的交易指令 | Strategy → Broker |
| `Order` | Broker 建立的正式訂單 | Broker ↔ UI |
| `Position` | 目前持倉狀態 | Broker → UI |
| `Account` | 帳戶資金狀態 | Broker → UI |
| `Tick` | 最新一筆成交資料 | MarketData → Strategy |
| `KBar` | K 線資料（OHLCV） | MarketData → Strategy / Backtest |
| `TradeResult` | 單筆交易損益（回測用） | Backtest |
| `PerformanceReport` | 回測績效報告 | Backtest → UI |

---

## Signal（交易訊號）

由 Strategy 產生，傳給 Broker 的唯一溝通介面。

```python
@dataclass
class Signal:
    strategy_id: str        # 策略名稱，例如 "ma_cross_v1"
    code: str               # 股票代碼，例如 "2330"（不帶 .TW）
    action: str             # "Buy" 或 "Sell"（對應 Shioaji Action）
    order_type: str         # "MARKET" 或 "LIMIT"
    price: float | None     # 限價單填價格；市價單填 None
    quantity: int           # 數量（台股：張；期貨：口）
    timestamp: str          # 訊號時間，ISO 8601
```

**範例：**
```json
{
  "strategy_id": "ma_cross_v1",
  "code": "2330",
  "action": "Buy",
  "order_type": "LIMIT",
  "price": 625.0,
  "quantity": 1,
  "timestamp": "2026-04-08T09:05:00"
}
```

**驗證規則：**
- `action`：只允許 `"Buy"` 或 `"Sell"`
- `order_type` 為 `"LIMIT"` 時，`price` 不可為 `None`
- `quantity` 必須 > 0

---

## Order（訂單）

對應 Shioaji `Trade` 物件，由 Broker 轉換建立。

```python
@dataclass
class Order:
    order_id: str           # Shioaji order.id（8 碼十六進位，例如 "de616839"）
    seqno: str              # Shioaji 委託序號（例如 "500009"）
    strategy_id: str        # 來源策略
    code: str               # 股票代碼
    action: str             # "Buy" 或 "Sell"
    order_type: str         # "MARKET" 或 "LIMIT"
    price: float | None     # 委託價格
    quantity: int           # 委託數量
    status: str             # 訂單狀態（見下方）
    filled_price: float | None  # 成交價
    filled_quantity: int    # 已成交數量（初始 0）
    created_at: str         # 建立時間
    updated_at: str         # 最後更新時間
    error_message: str | None
```

**狀態對應 Shioaji Status：**

| 本系統 | Shioaji | 說明 |
|--------|---------|------|
| `Inactive` | `Status.Inactive` | 非阻塞模式下，尚未送達交易所 |
| `Submitted` | `Status.Submitted` | 已送至交易所 |
| `Filled` | `Status.Filled` | 完全成交 |
| `Cancelled` | `Status.Cancelled` | 已撤單 |
| `Failed` | `Status.Failed` | 失敗 |

---

## Position（持倉）

對應 Shioaji `StockPosition`（現股）或 `FuturePosition`（期貨）。

```python
@dataclass
class Position:
    code: str           # 股票代碼（對應 StockPosition.code）
    direction: str      # "Buy" 或 "Sell"（對應 StockPosition.direction）
    quantity: int       # 持有張數（對應 StockPosition.quantity）
    price: float        # 平均成本（對應 StockPosition.price）
    last_price: float   # 目前市價（對應 StockPosition.last_price）
    pnl: float          # 未實現損益（對應 StockPosition.pnl）
    yd_quantity: int    # 昨日庫存（對應 StockPosition.yd_quantity）
```

**欄位對照：**
```
StockPosition.code         → Position.code
StockPosition.direction    → Position.direction
StockPosition.quantity     → Position.quantity
StockPosition.price        → Position.price      (平均成本，非市價)
StockPosition.last_price   → Position.last_price
StockPosition.pnl          → Position.pnl
StockPosition.yd_quantity  → Position.yd_quantity
```

---

## Account（帳戶）

由 Broker 組合多個 Shioaji API 結果計算（無單一 API 直接回傳總資產）。

```python
@dataclass
class Account:
    acc_balance: float      # 交割帳戶餘額（來自 api.account_balance().acc_balance）
    unrealized_pnl: float   # 未實現損益加總（從 api.list_positions() 計算）
    realized_pnl: float     # 已實現損益加總（從 api.list_profit_loss() 計算）
    total_pnl: float        # 總損益 = unrealized_pnl + realized_pnl
    equity: float           # 總資產估算 = acc_balance + unrealized_pnl
    updated_at: str         # 資料更新時間
```

**Shioaji 查詢來源：**
```python
api.account_balance()               # → acc_balance
api.list_positions(stock_account)   # → sum of pnl → unrealized_pnl
api.list_profit_loss(stock_account) # → sum of pnl → realized_pnl
```

---

## Tick（即時成交）

對應 Shioaji `TickSTKv1`（現股）或 `TickFOPv1`（期貨）。

```python
@dataclass
class Tick:
    code: str           # 股票代碼（對應 tick.code）
    datetime: str       # 成交時間（對應 tick.datetime，ISO 8601）
    price: float        # 成交價格（對應 tick.close，Shioaji 用 close 表示成交價）
    volume: int         # 本筆成交量（對應 tick.volume）
    total_volume: int   # 今日累計量（對應 tick.total_volume）
    simtrade: int       # 0=正常成交，1=試撮（策略應過濾 simtrade==1）
```

> ⚠️ **策略必須過濾 `simtrade == 1`**，試撮資料不代表真實成交，會造成錯誤訊號。

---

## KBar（K 線資料）

`api.kbars()` 回傳 `Kbars` 物件，可轉為 DataFrame。欄位名稱**首字大寫**，與 Shioaji 一致。

```python
@dataclass
class KBar:
    code: str       # 股票代碼
    ts: str         # 時間（pd.to_datetime 轉換後的字串，ISO 8601）
    Open: float     # 開盤價（注意大寫 O）
    High: float     # 最高價
    Low: float      # 最低價
    Close: float    # 收盤價
    Volume: int     # 成交量
    Amount: int     # 成交金額
    interval: str   # K 棒週期，例如 "1H"、"4H"、"1D"、"W"、"M"
```

**轉換範例：**
```python
kbars = api.kbars(contract, start="2025-01-01", end="2025-12-31")
df = pd.DataFrame({**kbars})
df.ts = pd.to_datetime(df.ts)
```

---

## TradeResult / PerformanceReport（回測用）

```python
@dataclass
class TradeResult:
    code: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    is_win: bool

@dataclass
class PerformanceReport:
    total_return_pct: float
    win_rate_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    equity_curve: list[float]
    trade_history: list[TradeResult]
    equity_timestamps: list[str]
```

---

## 注意事項

1. **代碼格式**：純數字，不帶 `.TW`，例如 `"2330"`
2. **action 首字大寫**：`"Buy"` / `"Sell"`，與 Shioaji `Action` enum 一致
3. **KBar 欄位大寫**：`Open`、`High`、`Low`、`Close`、`Volume` 與 Shioaji DataFrame 一致
4. **時間統一** ISO 8601：`"YYYY-MM-DDTHH:MM:SS"`
5. **simtrade 過濾**：`simtrade=1` 為開盤前試撮，策略邏輯應跳過

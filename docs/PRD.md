# PRD — sj-trading v1.1

**文件版本**：1.1（依 Shioaji 官方文件修正）  
**最後更新**：2026-04-09  
**系統名稱**：sj-trading  
**市場**：台灣股票市場（台股）、台灣期貨市場  
**串接券商**：永豐金證券（Shioaji API）

---

## 一、背景與目標

### 1.1 背景

台股投資人在手動操盤時，常面臨以下問題：
- 錯過進出場時機（人不在、反應太慢）
- 情緒影響決策（追高殺低）
- 難以同時監控多檔標的
- 策略邏輯無法客觀驗證

### 1.2 目標

sj-trading 旨在建立一套**完全自動化**的台股交易系統，讓使用者可以：

1. 將交易策略程式化，系統自動執行，不需人工盯盤
2. 在實際下單前，用歷史資料驗證策略是否有效（回測）
3. 透過桌面視窗即時掌握帳戶狀況與策略表現

### 1.3 範圍

**納入：** 台股現貨自動下單、台指期貨自動下單、策略回測、桌面即時監控

**排除：** 美股、選擇權交易、多帳號管理

---

## 二、使用者故事

| 使用者 | 需求 | 目的 |
|--------|------|------|
| 投資人 | 設定策略後系統自動買賣 | 不需整天盯盤 |
| 投資人 | 回測過去 2 年的策略表現 | 確認策略潛力 |
| 投資人 | 開啟視窗即看到帳戶損益與訂單 | 隨時掌握資金動向 |
| 開發者 | 自行撰寫策略邏輯並接入系統 | 擴充交易策略 |

---

## 三、系統架構

### 3.1 模組總覽

```
┌────────────────────────────────────────────────────┐
│                   sj-trading                        │
│                                                     │
│  ┌──────────────┐      ┌──────────────────────┐    │
│  │  MarketData  │─────▶│      Strategy        │    │
│  │ api.kbars()  │      │   自動判斷買賣時機    │    │
│  │ quote.sub()  │      └──────────┬───────────┘    │
│  └──────────────┘                 │ Signal          │
│         ▲                         ▼                 │
│  sj.Shioaji(                ┌──────────────┐        │
│   simulation=True)          │    Broker    │        │
│  api.login()                │ place_order  │        │
│                             └──────┬───────┘        │
│                                    │ 事件            │
│                       ┌────────────┴──────────┐     │
│                       ▼                       ▼     │
│               ┌────────────┐         ┌──────────┐  │
│               │  Strategy  │         │    UI    │  │
│               │ (回報接收) │         │ 桌面視窗 │  │
│               └────────────┘         └──────────┘  │
│                                                     │
│  ─ ─ ─ ─ ─ 回測模式 ─ ─ ─ ─ ─                     │
│  歷史資料 → Backtester → MockBroker                 │
│                └─▶ PerformanceReport                │
└────────────────────────────────────────────────────┘
```

### 3.2 模組依賴規則

```
schemas.py          ← 最底層，不依賴任何自訂模組
market_data.py      ← 只依賴 schemas
broker.py           ← 只依賴 schemas
strategy.py         ← 依賴 schemas、broker（只呼叫 place_signal）
backtest.py         ← 依賴 schemas、market_data、broker（MockBroker）、strategy
ui.py               ← 依賴 schemas、broker（只訂閱事件）
```

**禁止：**
- ❌ `strategy.py` 不可直接操作 `broker.py` 的內部狀態
- ❌ `ui.py` 不可呼叫 `broker.py` 的下單方法
- ❌ `schemas.py` 不可 import 任何其他自訂模組

---

## 四、Shioaji API 規格（官方文件依據）

### 4.1 初始化與登入

```python
import shioaji as sj

# simulation=True 傳入建構子，不是 login()
api = sj.Shioaji(simulation=True)

api.login(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    contracts_timeout=10000,   # 等待商品檔 10 秒
    subscribe_trade=True       # 自動訂閱委託/成交回報
)
```

### 4.2 股票代碼格式

Shioaji 使用**純數字代碼**，不帶任何後綴：

```python
api.Contracts.Stocks["2330"]     # ✅ 正確
api.Contracts.Stocks["2330.TW"]  # ❌ 錯誤
```

### 4.3 下單 API

**現股：**
```python
contract = api.Contracts.Stocks["2330"]
order = api.Order(
    price=625.0,
    quantity=1,
    action=sj.constant.Action.Buy,
    price_type=sj.constant.StockPriceType.LMT,
    order_type=sj.constant.OrderType.ROD,
    order_lot=sj.constant.StockOrderLot.Common,
    account=api.stock_account
)
trade = api.place_order(contract, order)
```

**期貨：**
```python
contract = api.Contracts.Futures.TXF["TXFA5"]
order = api.Order(
    price=20000,
    quantity=1,
    action=sj.constant.Action.Buy,
    price_type=sj.constant.FuturesPriceType.LMT,
    order_type=sj.constant.OrderType.ROD,
    octype=sj.constant.FuturesOCType.Auto,
    account=api.futopt_account
)
trade = api.place_order(contract, order)
```

### 4.4 行情訂閱 API

```python
@api.on_tick_stk_v1()
def on_tick(exchange, tick):
    if tick.simtrade == 1:   # 過濾試撮
        return
    # tick.code, tick.close（成交價）, tick.volume, tick.datetime

api.quote.subscribe(
    api.Contracts.Stocks["2330"],
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1
)
```

### 4.5 歷史 K 棒 API

```python
import pandas as pd

kbars = api.kbars(
    api.Contracts.Stocks["2330"],
    start="2025-01-01",
    end="2025-12-31"
)
df = pd.DataFrame({**kbars})
df.ts = pd.to_datetime(df.ts)
# 欄位：ts, Open, High, Low, Close, Volume, Amount（首字大寫）
```

### 4.6 帳務查詢 API

```python
balance   = api.account_balance()              # balance.acc_balance
positions = api.list_positions(api.stock_account)  # StockPosition 列表
pnl_list  = api.list_profit_loss(api.stock_account,
                                  begin_date="2026-04-09",
                                  end_date="2026-04-09")
```

### 4.7 成交回報

```python
@api.on_order_callback
def on_order(stat, msg):
    order_id = msg['order']['id']     # 例如 "de616839"
    seqno    = msg['order']['seqno']  # 例如 "500009"
    # status: Inactive / Submitted / Filled / Cancelled / Failed
```

---

## 五、資料結構規格（Schemas）

### Signal

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

| 欄位 | 說明 |
|------|------|
| `code` | 純數字，例如 `"2330"`（不帶 `.TW`） |
| `action` | `"Buy"` 或 `"Sell"`（首字大寫） |
| `order_type` | `"MARKET"` 或 `"LIMIT"` |

---

### Order（對應 Shioaji Trade）

| 欄位 | 來源 | 說明 |
|------|------|------|
| `order_id` | `trade.order.id` | 例如 `"de616839"` |
| `seqno` | `trade.order.seqno` | 例如 `"500009"` |
| `status` | `trade.status.status` | Inactive / Submitted / Filled / Cancelled / Failed |

---

### Position（對應 Shioaji StockPosition）

| 欄位 | 對應 Shioaji 欄位 |
|------|----------------|
| `code` | `StockPosition.code` |
| `direction` | `StockPosition.direction`（Buy / Sell） |
| `quantity` | `StockPosition.quantity` |
| `price` | `StockPosition.price`（平均成本） |
| `last_price` | `StockPosition.last_price` |
| `pnl` | `StockPosition.pnl` |
| `yd_quantity` | `StockPosition.yd_quantity` |

---

### Account（組合多個 API）

| 欄位 | 來源 |
|------|------|
| `acc_balance` | `api.account_balance().acc_balance` |
| `unrealized_pnl` | `sum(p.pnl for p in api.list_positions())` |
| `realized_pnl` | `sum(p.pnl for p in api.list_profit_loss())` |
| `equity` | `acc_balance + unrealized_pnl`（自行計算） |

---

### KBar（對應 Shioaji Kbars DataFrame）

欄位首字大寫：`Open`、`High`、`Low`、`Close`、`Volume`、`Amount`、`ts`

---

## 六、API 速率限制（官方規定）

| 操作 | 限制 |
|------|------|
| 行情查詢（kbars、ticks、snapshots 共用） | 5 秒內上限 **50 次** |
| 盤中查詢 ticks | 每日上限 **10 次** |
| 盤中查詢 kbars | 每日上限 **270 次** |
| 下單（place/cancel/update） | 10 秒內上限 **250 次** |
| 帳務查詢（positions、balance 等） | 5 秒內上限 **25 次** |
| 訂閱數 | 上限 **200 個** |
| 連線數（同一 person_id） | 最多 **5 個** |
| 登入次數 | 每日上限 **1000 次** |

---

## 七、錯誤處理規範

| 情況 | 處理方式 |
|------|---------|
| 登入失敗 | 拋出 `RuntimeError`，記錄 log |
| Shioaji 斷線 | 自動重連 3 次；失敗後停止策略，通知 UI |
| 下單失敗 | Order status 設 `Failed`，記錄錯誤訊息 |
| kbars 回傳空值（流量超限） | 記錄 warning，回傳空列表 |
| CSV 格式錯誤 | 拋出 `ValueError` |
| 帳務查詢超速 | 等待 5 秒後重試 |

---

## 八、系統限制

| 項目 | 限制 |
|------|------|
| 訂閱標的上限 | **200 個** |
| 連線數 | 同一帳號最多 **5 個** |
| 回測交易成本 | 預設不計算 |
| 作業系統 | Windows / macOS / Linux |

---

## 九、開發優先順序

| 階段 | 模組 | 重點 |
|------|------|------|
| 1 | `schemas.py` | 定好欄位名稱，後續不變 |
| 2 | `market_data.py` | 先做 CSV 載入與 replay |
| 3 | `broker.py` | 先做 MockBroker |
| 4 | `strategy.py` | 接 MockBroker，確認訊號 |
| 5 | `backtest.py` | 跑通完整回測 |
| 6 | `broker.py` | 接真實 Shioaji API |
| 7 | `ui.py` | 最後整合 |

---

## 十、相依套件

```
shioaji      # pip install shioaji
matplotlib   # pip install matplotlib
pandas       # pip install pandas
tkinter      # Python 內建
```

---

## 十一、常見問題

**Q：`simulation=True` 要放哪裡？**  
A：`sj.Shioaji(simulation=True)`，不是 `login()`。

**Q：股票代碼要不要帶 `.TW`？**  
A：不要。使用純數字，例如 `"2330"`。

**Q：`action` 大小寫？**  
A：首字大寫：`"Buy"` / `"Sell"`。

**Q：KBar 欄位為何首字大寫？**  
A：Shioaji `api.kbars()` 轉 DataFrame 後欄位本身就是首字大寫。

**Q：Account 沒有 `cash` 欄位？**  
A：Shioaji 用 `acc_balance`，本系統保持一致。總資產由 `acc_balance + unrealized_pnl` 自行計算。

**Q：訂閱上限幾個？**  
A：`api.quote.subscribe()` 上限 **200 個**。

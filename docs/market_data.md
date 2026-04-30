# MarketData 模組文件

## 模組職責

`market_data.py` 封裝所有 Shioaji 行情相關 API，提供：

- **即時資料**：訂閱現股 Tick（`TickSTKv1`）與 K 棒，即時推送給 Strategy
- **歷史資料**：透過 `api.kbars()` 下載，或從本地 CSV 讀取
- **模式切換**：實盤連接 Shioaji；回測改為資料回放（replay），Strategy 程式碼不需改變

---

## 模組依賴關係

```
schemas.py  ←  market_data.py  ←  strategy.py
                                ←  backtest.py
```

---

## 運作模式

### 實盤模式

```
Shioaji API WebSocket
    ↓ api.quote.subscribe(contract, QuoteType.Tick, QuoteVersion.v1)
TickSTKv1 callback
    ↓ 轉換為內部 Tick schema
Strategy.on_tick()
```

### 回測模式

```
CSV 或 api.kbars() 歷史資料
    ↓ replay()（按時間順序逐根推送）
Strategy.on_kbar()  ← 與實盤相同的介面
```

---

## 初始化

```python
import shioaji as sj

# 實盤模式：simulation=True 為模擬交易（預設）
api = sj.Shioaji(simulation=True)
api.login(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    contracts_timeout=10000   # 等待商品檔下載，10 秒
)
```

> ⚠️ **注意**：`simulation=True` 傳入 `sj.Shioaji()` 建構子，**不是** `login()`。

---

## 主要方法

### `subscribe_tick(code, callback)`

訂閱現股即時 Tick，每筆成交觸發 callback。

| 參數 | 說明 |
|------|------|
| `code` | 股票代碼，例如 `"2330"` |
| `callback` | `Callable[[Tick], None]` |

**Shioaji 底層實作：**
```python
contract = api.Contracts.Stocks[code]

@api.on_tick_stk_v1()
def tick_callback(exchange, tick):
    if tick.simtrade == 1:   # 過濾試撮資料
        return
    internal_tick = _convert_tick(tick)
    callback(internal_tick)

api.quote.subscribe(
    contract,
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1
)
```

---

> 目前 `MarketData` 沒有提供獨立的 `subscribe_kbar()` 包裝；實盤以 `subscribe_tick()` 為主，K 棒可由 Tick 自行組成，或使用歷史資料搭配 `replay()` / `resample_kbars()`。

---

### `unsubscribe(code)`

取消訂閱。

```python
api.quote.unsubscribe(
    api.Contracts.Stocks[code],
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1
)
```

---

### `get_history_kbar(code, start, end)`

透過 Shioaji API 下載歷史 K 棒。

| 參數 | 型別 | 說明 |
|------|------|------|
| `code` | `str` | 股票代碼，例如 `"2330"` |
| `start` | `str` | 起始日期，格式 `"2025-01-01"` |
| `end` | `str` | 結束日期，格式 `"2025-12-31"` |

**Shioaji 底層實作：**
```python
contract = api.Contracts.Stocks[code]
kbars = api.kbars(contract, start=start, end=end)
df = pd.DataFrame({**kbars})
df.ts = pd.to_datetime(df.ts)
```

**回傳：** `list[KBar]`

**API 限制：**
- 盤中查詢 `kbars` 次數：每日上限 **270 次**
- 5 秒內查詢上限：**50 次**（與 ticks、snapshots 共用額度）

---

### `load_csv(file_path, code)`

從本地 CSV 載入歷史 K 棒（回測用，不需連線）。

**CSV 格式：**
```csv
ts,Open,High,Low,Close,Volume,Amount
2025-01-02T09:00:00,620.0,628.0,619.0,625.0,35000,21875000
2025-01-03T09:00:00,625.0,631.0,623.0,629.0,28000,17612000
```

> ⚠️ 欄位名稱大寫（`Open`、`High`、`Low`、`Close`、`Volume`），與 Shioaji `Kbars` DataFrame 一致。

命名規則建議：`data/{代碼}_1d.csv`，例如 `data/2330_1d.csv`

如果 CSV 沒有 `interval` 欄位，系統會預設為 `1D`。

**回傳：** `list[KBar]`

---

### `replay(kbars, callback, speed=0)`

回測模式下按時間順序模擬推送 K 棒。

| 參數 | 說明 |
|------|------|
| `kbars` | 歷史 K 棒列表（需依時間排序） |
| `callback` | 每根 K 棒推送時呼叫 |
| `speed` | `0` = 最快速度（回測用）；`1.0` = 原始速度 |

---

### `resample_kbars(kbars, freq="1D")`

將 K 棒重新取樣成較大的時間框架。支援多種時間單位。

| 參數 | 型別 | 說明 |
|------|------|------|
| `kbars` | `list[KBar]` | 原始 K 棒列表 |
| `freq` | `str` | 目標頻率，預設 `"1D"`（日級別） |

**支援的頻率：**

| 頻率 | 說明 |
|------|------|
| `"1H"`, `"2H"`, `"4H"`, ... | 小時級別（數字 + H） |
| `"1D"` | 日級別（預設） |
| `"W"` | 週級別 |
| `"M"` | 月級別 |

**聚合規則：**
- **Open**：該時間桶的第一根 K 棒的開盤價
- **Close**：該時間桶的最後一根 K 棒的收盤價
- **High**：該時間桶內所有 K 棒的最高價的最高值
- **Low**：該時間桶內所有 K 棒的最低價的最低值
- **Volume**：該時間桶內所有 K 棒的成交量加總
- **Amount**：該時間桶內所有 K 棒的成交金額加總

**輸出規則：**
- `KBar.interval` 會設為目標頻率字串，例如 `1H`、`4H`、`1D`、`W`、`M`
- 輸出時間戳會落在時間桶的結束時間，並以 UTC ISO 8601 字串表示
---

## API 速率限制（官方規定）

| 類型 | 限制 |
|------|------|
| 行情查詢（kbars、ticks、snapshots 等共用） | 5 秒內上限 **50 次** |
| 盤中查詢 `ticks` | 每日上限 **10 次** |
| 盤中查詢 `kbars` | 每日上限 **270 次** |
| 訂閱數（`api.quote.subscribe`） | 上限 **200 個** |
| 每日流量（成交金額 0） | **500MB** |
| 每日流量（成交金額 1~1億） | **2GB** |

> ⚠️ 流量超過限制後，行情查詢（ticks、snapshots、kbars）回傳空值，下單功能不受影響。

---

## 範例程式碼

### 實盤訂閱 Tick

```python
import shioaji as sj
from schemas import Tick

api = sj.Shioaji(simulation=True)
api.login(api_key="YOUR_KEY", secret_key="YOUR_SECRET", contracts_timeout=10000)

def handle_tick(tick: Tick):
    if tick.simtrade == 1:
        return  # 過濾試撮
    print(f"{tick.code} 成交價：{tick.price}，量：{tick.volume}")

# 設定 callback
@api.on_tick_stk_v1()
def on_tick(exchange, tick):
    handle_tick(Tick(
        code=tick.code,
        datetime=str(tick.datetime),
        price=float(tick.close),
        volume=tick.volume,
        total_volume=tick.total_volume,
        simtrade=tick.simtrade
    ))

# 訂閱台積電
api.quote.subscribe(
    api.Contracts.Stocks["2330"],
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1
)
```

### 下載歷史 K 棒

```python
import pandas as pd

contract = api.Contracts.Stocks["2330"]
kbars = api.kbars(contract, start="2025-01-01", end="2025-12-31")

df = pd.DataFrame({**kbars})
df.ts = pd.to_datetime(df.ts)
print(df.head())
#          ts    Open    High     Low   Close  Volume       Amount
# 0 2025-01-02  620.0  628.0  619.0  625.0   35000  21875000
```

### 回測模式（CSV + replay）

```python
from market_data import MarketData

md = MarketData()
kbars = md.load_csv("data/2330_1d.csv", code="2330")

def on_kbar(kbar):
    print(f"{kbar.ts}  收盤：{kbar.Close}")

md.replay(kbars, callback=on_kbar, speed=0)
```

---

## 錯誤處理

| 情況 | 處理方式 |
|------|---------|
| 登入失敗 | 拋出 `RuntimeError`，記錄 log |
| WebSocket 斷線 | 自動重連最多 3 次；失敗後呼叫 `on_connection_lost` callback |
| kbars 回傳空值（流量超限） | 記錄警告 log，回傳空列表 |
| CSV 欄位格式錯誤 | 拋出 `ValueError`，標明出錯行 |
| 訂閱超過 200 個 | 記錄警告，不拋出例外 |

---

## 注意事項

1. 回測模式**不需呼叫** `api.login()`
2. `api.kbars()` 回傳的 `ts` 欄位為原始整數，需用 `pd.to_datetime()` 轉換
3. 實盤 K 棒訂閱需確認 Shioaji 是否支援當前版本的 K 棒推送，建議以 Tick 資料自行組 K 棒
4. 商品檔（`api.Contracts`）需要在 `login()` 後等待下載完成才能使用，建議設定 `contracts_timeout=10000`

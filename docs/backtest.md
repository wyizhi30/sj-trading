# Backtest 模組文件

## 模組職責

`backtest.py` 負責用歷史資料測試策略表現：

- 載入歷史 K 棒（CSV 或 `api.kbars()`），逐根推送給 Strategy
- 使用 `MockBroker` 模擬成交（不連網路，已包含基本手續費 / 交易稅 / 滑價）
- 計算績效指標：總報酬率、勝率、最大回撤、Sharpe Ratio

---

## 回測流程

```
1. 建立 MockBroker（設定初始資金）
2. 建立 Strategy（傳入 MockBroker）
3. 載入歷史 KBar 列表
4. 先用 MockBroker.process_kbar() 消化上一根 K 棒帶來的掛單成交
5. 再推送 Strategy.on_kbar()
6. 若有 Signal → broker.place_signal()
7. 由 broker 回報更新 TradeResult、持倉與 account equity
8. 計算並輸出 PerformanceReport
```

---

## 主要方法

### 初始化

```python
Backtester(
    strategy: BaseStrategy,
    initial_cash: float = 10_000_000,
    commission_rate: float = 0.001425,
    tax_rate: float = 0.003,
    slippage_per_unit: float = 0.0,
)
```

### `run(kbars: list[KBar]) -> PerformanceReport`

執行回測。

### `run_from_csv(file_path, code) -> PerformanceReport`

```python
report = backtester.run_from_csv("data/2330_1d.csv", code="2330")
```

### `run_from_api(api, code, start, end) -> PerformanceReport`

```python
kbars_raw = api.kbars(api.Contracts.Stocks["2330"], start="2025-01-01", end="2025-12-31")
df = pd.DataFrame({**kbars_raw})
df.ts = pd.to_datetime(df.ts)
# 轉換後交給 Backtester
```

---

## 績效指標計算

### 總報酬率
```
(最終資產 - 初始資金) / 初始資金 × 100%
```

### 勝率
```
獲利筆數 / 總交易筆數 × 100%
```

### 最大回撤
```
max((歷史高點 - 各時點淨值) / 歷史高點) × 100%
```

### Sharpe Ratio
```
(年化平均日報酬 - 1.5%) / 年化日報酬標準差
年化係數：252（台股年交易日）
```

---

## 範例程式碼

### 完整回測

```python
import shioaji as sj
import pandas as pd
from broker import MockBroker
from strategy import MACrossStrategy
from backtest import Backtester

# 建立 MockBroker 和策略
mock_broker = MockBroker()
strategy = MACrossStrategy(
    strategy_id="ma_cross_v1",
    code="2330",    # 純數字，不帶 .TW
    broker=mock_broker,
    short_window=5,
    long_window=20,
    quantity=1
)

backtester = Backtester(strategy=strategy, initial_cash=10_000_000)

# 從 CSV 執行
report = backtester.run_from_csv("data/2330_1d.csv", code="2330")

print(f"總報酬率：{report.total_return_pct:.2f}%")
print(f"勝率：    {report.win_rate_pct:.2f}%")
print(f"最大回撤：{report.max_drawdown_pct:.2f}%")
print(f"Sharpe：  {report.sharpe_ratio:.2f}")
```

### 從 Shioaji API 下載後回測

```python
api = sj.Shioaji(simulation=True)
api.login(api_key="YOUR_KEY", secret_key="YOUR_SECRET", contracts_timeout=10000)

contract = api.Contracts.Stocks["2330"]
kbars_raw = api.kbars(contract, start="2024-01-01", end="2025-12-31")

df = pd.DataFrame({**kbars_raw})
df.ts = pd.to_datetime(df.ts)

# 注意：KBar 欄位名稱首字大寫（與 Shioaji DataFrame 一致）
kbars = [
    KBar(code="2330", ts=str(row.ts), Open=row.Open, High=row.High,
         Low=row.Low, Close=row.Close, Volume=row.Volume,
         Amount=row.Amount, interval="1D")
    for row in df.itertuples()
]

report = backtester.run(kbars)
```

### 參數優化

```python
results = []
for short in [3, 5, 10]:
    for long in [15, 20, 30]:
        mb = MockBroker()
        st = MACrossStrategy(f"ma_{short}_{long}", "2330", mb,
                             short_window=short, long_window=long)
        bt = Backtester(st)
        r = bt.run_from_csv("data/2330_1d.csv", code="2330")
        results.append({"short": short, "long": long,
                        "return": r.total_return_pct, "sharpe": r.sharpe_ratio})

best = max(results, key=lambda x: x["sharpe"])
print(f"最佳：短均線 {best['short']}，長均線 {best['long']}")
```

---

## MockBroker 成交規則

| 訂單類型 | 成交條件 | 成交價格 |
|---------|---------|---------|
| `MARKET` | 下一根 K 棒直接成交 | 下一根 K 棒 `Open` |
| `LIMIT` 買單 | K 棒 `Low` ≤ 委託價 | 委託價 |
| `LIMIT` 賣單 | K 棒 `High` ≥ 委託價 | 委託價 |

---

## 回測限制

1. **已加入基本交易成本**
    - 手續費：買賣皆依成交金額 × 費率計算（預設 0.1425%）
    - 交易稅：股票賣出時計算（預設 0.3%）
2. **已加入固定滑價模型**
    - Market 單會依買/賣方向調整成交價
3. **回測過擬合風險**：同一資料調參容易過擬合，建議預留樣本外測試區間
4. `api.kbars()` 盤中查詢每日上限 **270 次**，大量下載請注意

---

## 注意事項

1. KBar 欄位首字大寫（`Open`、`High`、`Low`、`Close`、`Volume`），與 Shioaji 一致
2. `code` 純數字，例如 `"2330"`
3. `api.kbars()` 的 `ts` 欄位需用 `pd.to_datetime()` 轉換後再使用
4. 回測速度不限，一般數千根 K 棒在數秒內完成

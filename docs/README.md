# sj-trading 系統文件總覽

## 專案簡介

sj-trading 是一套台股與期貨**自動交易系統**，支援：
- 策略自動產生訊號並下單（使用永豐 Shioaji API）
- 歷史資料回測與績效分析
- 桌面視窗即時監控

---

## 文件目錄

| 文件 | 說明 |
|------|------|
| [schemas.md](schemas.md) | 資料格式定義（Signal、Order、帳戶等） |
| [market_data.md](market_data.md) | 即時與歷史資料模組 |
| [broker.md](broker.md) | 下單執行與事件通知模組 |
| [strategy.md](strategy.md) | 策略邏輯（含均線交叉範例） |
| [backtest.md](backtest.md) | 回測執行與績效計算 |
| [ui.md](ui.md) | 桌面視窗介面 |

---

## 系統架構圖

```
┌────────────────────────────────────────────────────┐
│                   sj-trading                        │
│                                                     │
│  ┌──────────────┐      ┌──────────────────────┐    │
│  │  MarketData  │─────▶│      Strategy        │    │
│  │              │      │  (MACrossStrategy)   │    │
│  │  ・即時 Tick │      │                      │    │
│  │  ・歷史 K棒  │      │  on_kbar() → Signal  │    │
│  └──────────────┘      └──────────┬───────────┘    │
│         ▲                         │ place_signal()  │
│         │                         ▼                 │
│  Shioaji API              ┌───────────────┐         │
│  (永豐券商)               │    Broker     │         │
│                           │               │         │
│                           │  ・下單/撤單  │         │
│                           │  ・持倉管理   │         │
│                           │  ・事件通知   │         │
│                           └──────┬────────┘         │
│                                  │ 事件              │
│                    ┌─────────────┴──────────┐        │
│                    ▼                        ▼        │
│             ┌────────────┐         ┌──────────────┐ │
│             │  Strategy  │         │      UI      │ │
│             │ (事件接收) │         │  (桌面視窗)  │ │
│             └────────────┘         └──────────────┘ │
│                                                     │
│  ─ ─ ─ ─ ─ ─ ─ 回測模式 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│                                                     │
│  ┌──────────┐   ┌────────────┐   ┌────────────┐    │
│  │ 歷史資料 │──▶│ Backtester │──▶│ MockBroker │    │
│  │ CSV/API  │   │            │   │            │    │
│  └──────────┘   └─────┬──────┘   └────────────┘    │
│                        │                            │
│                        ▼                            │
│               ┌─────────────────┐                   │
│               │ PerformanceReport│                   │
│               │ ・報酬率         │                   │
│               │ ・勝率           │                   │
│               │ ・最大回撤       │                   │
│               │ ・Sharpe Ratio   │                   │
│               └─────────────────┘                   │
└────────────────────────────────────────────────────┘
```

---

## 模組依賴規則

```
schemas.py          ← 最底層，不依賴任何自訂模組
market_data.py      ← 只依賴 schemas
broker.py           ← 只依賴 schemas
strategy.py         ← 依賴 schemas、market_data（唯讀）、broker（只呼叫 place_signal）
backtest.py         ← 依賴 schemas、market_data、broker（使用 MockBroker）、strategy
ui.py               ← 依賴 schemas、broker（只訂閱事件）、backtest（顯示報告）
```

**禁止的依賴關係：**
- ❌ `schemas.py` 不可 import 任何其他模組
- ❌ `strategy.py` 不可 import `broker.py` 的內部實作
- ❌ `ui.py` 不可呼叫 `broker.place_signal()` 以外的修改方法

---

## 事件流程總覽

### 實盤下單流程

```
1. MarketData 收到新 K 棒（來自 Shioaji）
2. 呼叫 Strategy.on_kbar(kbar)
3. Strategy 計算均線，判斷發生交叉
4. Strategy 回傳 Signal（BUY）
5. 主程式呼叫 broker.place_signal(signal)
6. Broker 建立 Order，送至 Shioaji
7. Shioaji 回報成交
8. Broker 更新 Order 狀態 → FILLED
9. Broker 觸發事件：
   ├── on_order_update(order) → Strategy 更新持倉記錄
   ├── on_position_update(position) → UI 更新持倉面板
   └── on_account_update(account) → UI 更新帳戶面板
```

### 回測流程

```
1. Backtester 讀取歷史 K 棒（CSV 或 API）
2. 逐根 K 棒呼叫 Strategy.on_kbar(kbar)
3. Strategy 產生 Signal（邏輯與實盤完全相同）
4. Backtester 呼叫 MockBroker.place_signal(signal)
5. MockBroker 在下一根 K 棒判斷成交條件
6. 成交後記錄 TradeResult
7. 所有 K 棒處理完畢
8. Backtester 計算績效，輸出 PerformanceReport
```

---

## 檔案結構

```
sj-trading/
├── README.md               ← 本文件
├── main.py                 ← 程式進入點（實盤）
├── run_backtest.py         ← 回測進入點
│
├── schemas.py              ← 資料格式定義
├── market_data.py          ← 即時與歷史資料
├── broker.py               ← 下單執行（含 MockBroker）
├── strategy.py             ← 策略邏輯
├── backtest.py             ← 回測執行與績效計算
├── ui.py                   ← 桌面視窗
│
├── data/                   ← 歷史資料 CSV 放這裡
│   ├── 2330_1d.csv
│   └── ...
│
├── logs/                   ← 自動產生的 log 檔
│   └── trading_YYYYMMDD.log
│
└── docs/                   ← 各模組詳細文件
    ├── schemas.md
    ├── market_data.md
    ├── broker.md
    ├── strategy.md
    ├── backtest.md
    └── ui.md
```

---

## 開發順序建議

依照以下順序開發，可以在每個階段驗證功能正確：

```
階段 1：schemas.py
    └── 定義所有資料結構，確認欄位完整

階段 2：market_data.py
    └── 先實作 load_csv() 和 replay()
    └── 確認 KBar 格式正確

階段 3：broker.py（MockBroker 優先）
    └── 先實作 MockBroker 和事件系統
    └── 確認事件通知機制運作正常

階段 4：strategy.py
    └── 實作 MACrossStrategy
    └── 與 MockBroker 串接，確認 Signal 正確產生

階段 5：backtest.py
    └── 整合 MarketData + MockBroker + Strategy
    └── 確認績效指標計算正確

階段 6：broker.py（真實 Broker）
    └── 接入 Shioaji API
    └── 確認實盤下單正常

階段 7：ui.py
    └── 最後整合所有模組
    └── 先做靜態顯示，再做即時更新
```

---

## 相依套件

```
shioaji          # 永豐 API（pip install shioaji）
matplotlib       # 圖表（pip install matplotlib）
pandas           # 資料處理，選用（pip install pandas）
```

tkinter 為 Python 內建，不需安裝。

---

## 常見問題

**Q：回測結果和實盤表現差很多，正常嗎？**
A：正常。回測未計算手續費、交易稅、滑價，實際成本會讓獲利降低。建議在回測結果上打七折估算。

**Q：Shioaji simulation=True 是什麼意思？**
A：模擬交易模式，下單流程完整執行，但不會真正買賣股票。適合開發測試使用。

**Q：可以同時跑多個策略嗎？**
A：可以，建立多個 Strategy 實例，各自訂閱 MarketData 事件即可。Broker 會記錄每筆訂單的 `strategy_id`。

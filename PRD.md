你是一個專業 Python 開發助理。請依照下列 PRD 與 Markdown 規格，生成 `sj-trading` 系統的模組代碼，保持資料結構與事件接口一致，並附簡單範例程式碼。

---

# PRD 摘要

系統名稱：sj-trading  
市場：台股與期貨  
功能：
1. Strategy 自動生成 Signal 並下單（完全自動執行）
2. Broker 接收 Signal，下單/撤單/帳戶更新
3. Market Data 提供即時及歷史資料
4. Schemas 統一資料結構（Signal, Order, Position, Account）
5. Backtest 計算績效指標（報酬率、勝率、最大回撤、Sharpe Ratio）
6. UI 顯示帳戶、訂單、績效，即時更新

---

# 生成規則

1. **模組**：
   - Schemas
   - Broker
   - Strategy
   - Market Data
   - UI

2. **資料結構範例**：

Signal:
{
  "symbol": "2330.TW",
  "action": "BUY" | "SELL",
  "price": 625.0,
  "volume": 10
}

Order:
{
  "order_id": "uuid-string",
  "symbol": "2330.TW",
  "action": "BUY",
  "price": 625.0,
  "volume": 10,
  "status": "PENDING" | "FILLED" | "CANCELLED",
  "filled_price": 625.0,
  "filled_volume": 10,
  "timestamp": "2026-04-08T15:04:05"
}

Position:
{
  "symbol": "2330.TW",
  "avg_price": 625.0,
  "volume": 10
}

Account:
{
  "cash": 1000000,
  "equity": 1005000,
  "PnL": 5000
}

---

# 輸出要求

1. 每個模組生成單獨 Python 檔案，並加上 Markdown 註解說明：
   - 模組職責
   - 主要類別與方法
   - Input / Output JSON
   - 範例程式碼
2. 保持 Strategy 不可直接操作 Broker 內部狀態
3. Broker 模組需支援事件通知，當 Order 狀態更新時，Strategy 與 UI 可接收
4. 回測模組需提供績效計算方法（報酬率、勝率、最大回撤、Sharpe Ratio）
5. UI 模組需提供簡單圖形化介面，顯示帳戶、訂單、績效，並可即時更新
6. 範例程式碼需展示：
   - Broker 初始化與下單
   - Strategy 生成 Signal
   - Market Data 更新 Tick / KBar
   - 回測績效計算
   - UI 即時顯示

---

# 附加說明
- 系統需完全自動下單
- 所有模組事件接口需一致
- 回測與實盤策略行為需一致
- 使用 Shioaji API 進行模擬交易(simulation=True)
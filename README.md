
# sj-trading

## DEMO（回測 + UI）

此專案提供一個最小的整合 DEMO：回測時由 `MockBroker` 觸發訂單/持倉/帳戶事件，`TradingUI` 訂閱事件並顯示；回測完成後同時顯示 `PerformanceReport` 與淨值曲線。

### 執行

1. 安裝依賴（專案已在 `pyproject.toml` 宣告）：

	- `matplotlib`
	- `pandas`
	- `python-dotenv`
	- `shioaji`

2. 直接跑 DEMO 腳本（含範例 CSV）：

	`python demo_backtest_ui.py --csv data/2330_demo_1d.csv --code 2330`

3. （選用）從 Shioaji 下載歷史資料 → 轉存 CSV → 回測：

	 - 需要先設定環境變數（可放在 `.env`）：`API_KEY`、`SECRET_KEY`
	 - 指令範例：

		 `python demo_backtest_ui.py --code 2330 --start 2026-01-01 --end 2026-03-31 --out-csv data/2330_20260101_20260331.csv`

### 參數

- `--initial-cash`：初始資金（預設 1,000,000）
- `--short` / `--long`：均線期數（預設 5 / 20）
- `--quantity`：每次下單張數（預設 1）

## 進度紀錄

- 已完成：回測/UI 整合、Only Filled 過濾、成交時間修正、未實現損益同步、股票本金單位修正（1 張 = 1000 股）、現金不足直接阻止成交
- 進行中：Strategy 參數驗證、回測交易成本與滑價評估、UI 與回測進一步一致化
- 目前預設本金：10,000,000


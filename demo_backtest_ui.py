"""sj-trading 完整系統 DEMO（回測 + UI）。

此腳本示範如何把各模組串成一個可跑的 DEMO：

- MarketData.load_csv() 讀 KBar
- Backtester（Strategy + MockBroker）跑回測閉環
- TradingUI 訂閱 broker 事件顯示訂單/持倉/帳戶
- 回測完成後在 UI 顯示 PerformanceReport + 淨值曲線

使用方式（Windows / PowerShell）：

    python demo_backtest_ui.py --csv data/2330_1d.csv --code 2330

或（從 Shioaji 下載歷史資料 → 轉存 CSV → 回測）：

    python demo_backtest_ui.py --code 2330 --start 2026-01-01 --end 2026-03-31 --out-csv data/2330_20260101_20260331.csv

注意：
- tkinter 視窗必須在主執行緒執行，因此回測會放在背景執行緒跑。
- 本專案採 src-layout；直接跑此腳本時會自動把 ./src 加入 sys.path。
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Optional


def _ensure_src_on_path() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _require_env(name: str) -> str:
    v = str((__import__("os").environ.get(name) or "")).strip()
    if not v:
        raise RuntimeError(f"缺少環境變數：{name}")
    return v


def _download_to_csv(*, code: str, start: str, end: str, out_csv: Path) -> Path:
    """使用 MarketData.get_history_kbar 下載資料並轉存為 CSV（符合 MarketData.load_csv 格式）。"""

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        # dotenv 為可選；若環境本來就有設定 env 也能跑。
        pass

    try:
        import shioaji as sj  # type: ignore
    except Exception as e:
        raise RuntimeError(f"shioaji 匯入失敗：{e}")

    from sj_trading.market_data import MarketData

    api_key = _require_env("API_KEY")
    secret_key = _require_env("SECRET_KEY")

    api = sj.Shioaji(simulation=True)
    api.login(api_key=api_key, secret_key=secret_key, contracts_timeout=10000)
    try:
        md = MarketData(api=api)
        kbars = md.get_history_kbar(code=code, start=start, end=end)
        if not kbars:
            raise RuntimeError("下載到的 KBar 為空（可能是日期區間無資料或流量受限）")

        print(f"找到代號 {code} 的 KBar 數據")
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        import csv

        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "Open", "High", "Low", "Close", "Volume", "Amount"])
            w.writeheader()
            for b in kbars:
                w.writerow(
                    {
                        "ts": b.ts,
                        "Open": b.Open,
                        "High": b.High,
                        "Low": b.Low,
                        "Close": b.Close,
                        "Volume": b.Volume,
                        "Amount": b.Amount,
                    }
                )
        return out_csv
    finally:
        try:
            api.logout()
        except Exception:
            pass


def main() -> int:
    _ensure_src_on_path()

    from sj_trading.backtest import Backtester
    from sj_trading.broker import MockBroker
    from sj_trading.strategy import MACrossStrategy
    from sj_trading.ui import TradingUI

    parser = argparse.ArgumentParser(description="sj-trading DEMO（回測 + UI）")
    parser.add_argument("--code", required=True, help="標的代碼（純數字，例如 2330）")
    parser.add_argument("--csv", default=None, help="CSV 檔案路徑，欄位需含 ts/Open/High/Low/Close/Volume/Amount")
    parser.add_argument("--start", default=None, help="下載歷史資料起日（YYYY-MM-DD）；提供此參數時會走下載→轉存 CSV")
    parser.add_argument("--end", default=None, help="下載歷史資料迄日（YYYY-MM-DD）；提供此參數時會走下載→轉存 CSV")
    parser.add_argument("--out-csv", default=None, help="下載後轉存 CSV 的輸出路徑（預設：data/<code>_<start>_<end>.csv）")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0, help="初始資金")
    parser.add_argument("--short", type=int, default=5, help="短均線期數")
    parser.add_argument("--long", type=int, default=20, help="長均線期數")
    parser.add_argument("--quantity", type=int, default=1, help="每次下單張數")
    args = parser.parse_args()

    code = str(args.code).strip()
    csv_path: Optional[Path] = Path(args.csv) if args.csv else None

    if args.start or args.end:
        if not args.start or not args.end:
            print("使用下載模式時，--start 與 --end 必須同時提供")
            return 2
        start = str(args.start).strip()
        end = str(args.end).strip()
        if args.out_csv:
            csv_path = Path(args.out_csv)
        else:
            safe_start = start.replace("-", "")
            safe_end = end.replace("-", "")
            csv_path = Path("data") / f"{code}_{safe_start}_{safe_end}.csv"
        try:
            csv_path = _download_to_csv(code=code, start=start, end=end, out_csv=csv_path)
            print(f"已下載並轉存 CSV：{csv_path}")
        except Exception as e:
            print(f"下載/轉存失敗：{e}")
            return 2

    if csv_path is None:
        print("請提供 --csv，或提供 --start/--end 進行下載")
        return 2
    if not csv_path.exists() or not csv_path.is_file():
        print(f"CSV 不存在：{csv_path}")
        return 2

    broker = MockBroker(initial_cash=float(args.initial_cash))
    strategy = MACrossStrategy(
        strategy_id="ma_cross_v1",
        code=code,
        broker=broker,
        short_window=int(args.short),
        long_window=int(args.long),
        quantity=int(args.quantity),
        order_type="MARKET",
    )
    backtester = Backtester(strategy=strategy, initial_cash=float(args.initial_cash))

    ui = TradingUI(broker=broker, mode="backtest")

    def _run_backtest() -> None:
        try:
            report = backtester.run_from_csv(str(csv_path), code=code)
            ui.show_backtest_report(report)
        except Exception as e:
            # 不新增額外 UI 元件；用 stderr 提示即可。
            print(f"回測失敗：{e}", file=sys.stderr)

    t = threading.Thread(target=_run_backtest, name="backtest-thread", daemon=True)
    t.start()

    ui.start()  # blocking
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

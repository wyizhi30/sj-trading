"""UI 模組（桌面視窗）。

依照 `docs/ui.md` 的規格，本模組提供 `TradingUI`：

- 顯示帳戶資金與損益（Account.acc_balance / unrealized_pnl / realized_pnl / equity）
- 監控所有訂單狀態（Order.status）
- 顯示持倉與未實現損益（Position.pnl）
- 顯示回測績效報告與淨值曲線（PerformanceReport.equity_curve）

設計重點：
- UI 完全透過訂閱 Broker 事件接收更新，不直接呼叫任何 Shioaji API。
- Broker 事件可能來自其他執行緒；tkinter 只能在主執行緒更新。
  因此使用 `queue.Queue`：事件 thread -> queue；主執行緒每 100ms poll -> 更新 UI。

依賴：
- 僅依賴 `sj_trading.schemas` 與 `sj_trading.broker`。
"""

from __future__ import annotations

import queue
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import tkinter as tk
from tkinter import ttk
from .broker import BaseBroker
from .schemas import Account, Order, PerformanceReport, Position

try:  # matplotlib 為可選；未安裝時仍可顯示表格與文字報告。
    import matplotlib
    import matplotlib.dates as mdates
    from matplotlib import rcParams
    from matplotlib import font_manager

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    def _configure_matplotlib_cjk_font() -> None:
        """最佳努力設定中文字型，避免缺字警告。

        matplotlib 預設字型（DejaVu Sans）在多數 Windows 環境不含中文字形，
        若圖表標題/座標使用中文會噴 `Glyph ... missing from font` 警告。
        這裡嘗試從常見中文字型中挑一個已安裝的套用。
        """

        candidates = [
            # Windows
            "Microsoft JhengHei",
            "Microsoft YaHei",
            "SimHei",
            "PMingLiU",
            # macOS
            "PingFang TC",
            "Heiti TC",
            "Songti TC",
            # Linux 常見
            "Noto Sans CJK TC",
            "Noto Sans CJK SC",
            "WenQuanYi Zen Hei",
            "AR PL UMing TW",
        ]

        try:
            available = {f.name for f in font_manager.fontManager.ttflist}
        except Exception:
            return

        chosen = next((name for name in candidates if name in available), None)
        if not chosen:
            return

        rcParams["font.family"] = "sans-serif"
        # 把 chosen 放在最前面，讓它優先用於中文
        current = list(rcParams.get("font.sans-serif", []))
        rcParams["font.sans-serif"] = [chosen] + [x for x in current if x != chosen]
        rcParams["axes.unicode_minus"] = False


    _configure_matplotlib_cjk_font()
except Exception:  # pragma: no cover
    FigureCanvasTkAgg = None  # type: ignore
    Figure = None  # type: ignore


class TradingUI:
    """桌面交易監控 UI（MVP）。"""

    def __init__(self, broker: BaseBroker, mode: str = "live") -> None:
        self.broker = broker
        self.mode = "backtest" if str(mode).lower().strip() == "backtest" else "live"

        self._queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._queue_max: int = 500

        self._order_item_by_id: Dict[str, str] = {}
        self._order_data_by_id: Dict[str, Order] = {}
        self._position_item_by_code: Dict[str, str] = {}

        self._last_account: Optional[Account] = None
        self._connection_ok: bool = True
        self._closing: bool = False
        self._poll_after_id: Optional[str] = None

        self.root = tk.Tk()
        self.root.title("sj-trading")
        self.root.geometry("1100x700")

        self._style = ttk.Style(self.root)
        self._style.configure("PnlPos.TLabel", foreground="green")
        self._style.configure("PnlNeg.TLabel", foreground="red")
        self._style.configure("PnlZero.TLabel", foreground="black")

        self._build_layout()
        self._register_broker_events()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_after_id = self.root.after(100, self._poll_queue)

    # ------------------------------ Public API ------------------------------

    def start(self) -> None:
        """啟動視窗（blocking）。"""

        self.root.mainloop()

    def show_backtest_report(self, report: PerformanceReport) -> None:
        """在「回測績效」頁籤顯示報告與淨值曲線圖。"""

        self._enqueue("backtest_report", report)

    # ------------------------------ Layout ------------------------------

    def _build_layout(self) -> None:
        # 狀態列
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        self._title_var = tk.StringVar(value=f"sj-trading  [{'模擬模式' if self.mode == 'backtest' else '實盤模式'}]")
        self._status_var = tk.StringVar(value="狀態：連線正常")
        self._last_update_var = tk.StringVar(value="最後更新：--:--:--")

        ttk.Label(top, textvariable=self._title_var, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self._status_var).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(top, textvariable=self._last_update_var).pack(side=tk.RIGHT)

        # 帳戶總覽
        account = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        account.pack(side=tk.TOP, fill=tk.X)

        self._acc_balance_var = tk.StringVar(value="帳戶餘額：$0")
        self._equity_var = tk.StringVar(value="總資產估算：$0")
        self._unrealized_var = tk.StringVar(value="未實現損益：$0")
        self._realized_var = tk.StringVar(value="已實現損益：$0")

        self._lbl_acc_balance = ttk.Label(account, textvariable=self._acc_balance_var)
        self._lbl_equity = ttk.Label(account, textvariable=self._equity_var)
        self._lbl_unrealized = ttk.Label(account, textvariable=self._unrealized_var, style="PnlZero.TLabel")
        self._lbl_realized = ttk.Label(account, textvariable=self._realized_var, style="PnlZero.TLabel")

        self._lbl_acc_balance.grid(row=0, column=0, sticky="w", padx=(0, 16))
        self._lbl_equity.grid(row=0, column=1, sticky="w", padx=(0, 16))
        self._lbl_unrealized.grid(row=0, column=2, sticky="w", padx=(0, 16))
        self._lbl_realized.grid(row=0, column=3, sticky="w")

        for col in range(4):
            account.grid_columnconfigure(col, weight=1)

        # Notebook tabs
        self._tabs = ttk.Notebook(self.root)
        self._tabs.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._tab_live = ttk.Frame(self._tabs)
        self._tab_backtest = ttk.Frame(self._tabs)
        self._tabs.add(self._tab_live, text="即時監控")
        self._tabs.add(self._tab_backtest, text="回測績效")

        self._build_live_tab(self._tab_live)
        self._build_backtest_tab(self._tab_backtest)

    def _build_live_tab(self, parent: ttk.Frame) -> None:
        pane = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Labelframe(pane, text="即時訂單", padding=8)
        right = ttk.Labelframe(pane, text="持倉明細", padding=8)
        pane.add(left, weight=2)
        pane.add(right, weight=2)

        # 訂單表
        self._order_tree = ttk.Treeview(
            left,
            columns=("time", "code", "action", "type", "price", "filled_price", "qty", "status"),
            show="headings",
            height=16,
        )
        self._order_tree.heading("time", text="時間")
        self._order_tree.heading("code", text="代碼")
        self._order_tree.heading("action", text="動作")
        self._order_tree.heading("type", text="類型")
        self._order_tree.heading("price", text="委託價")
        self._order_tree.heading("filled_price", text="成交價")
        self._order_tree.heading("qty", text="數量")
        self._order_tree.heading("status", text="狀態")

        self._order_tree.column("time", width=160, anchor="w")
        self._order_tree.column("code", width=70, anchor="center")
        self._order_tree.column("action", width=60, anchor="center")
        self._order_tree.column("type", width=70, anchor="center")
        self._order_tree.column("price", width=80, anchor="e")
        self._order_tree.column("filled_price", width=80, anchor="e")
        self._order_tree.column("qty", width=60, anchor="e")
        self._order_tree.column("status", width=90, anchor="center")

        order_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self._order_tree.yview)
        self._order_tree.configure(yscrollcommand=order_scroll.set)
        self._order_tree.grid(row=0, column=0, sticky="nsew")
        order_scroll.grid(row=0, column=1, sticky="ns")
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # 狀態顏色 tag（Treeview tag 以 foreground 為主）
        self._order_tree.tag_configure("Filled", foreground="green")
        self._order_tree.tag_configure("Submitted", foreground="goldenrod")
        self._order_tree.tag_configure("Inactive", foreground="goldenrod")
        self._order_tree.tag_configure("Cancelled", foreground="gray")
        self._order_tree.tag_configure("Failed", foreground="red")

        # 持倉表
        self._position_tree = ttk.Treeview(
            right,
            columns=("code", "direction", "price", "qty", "last", "pnl"),
            show="headings",
            height=16,
        )
        self._position_tree.heading("code", text="代碼")
        self._position_tree.heading("direction", text="方向")
        self._position_tree.heading("price", text="均價")
        self._position_tree.heading("qty", text="張數")
        self._position_tree.heading("last", text="目前價")
        self._position_tree.heading("pnl", text="未實現損益")

        self._position_tree.column("code", width=70, anchor="center")
        self._position_tree.column("direction", width=60, anchor="center")
        self._position_tree.column("price", width=80, anchor="e")
        self._position_tree.column("qty", width=60, anchor="e")
        self._position_tree.column("last", width=80, anchor="e")
        self._position_tree.column("pnl", width=100, anchor="e")

        pos_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self._position_tree.yview)
        self._position_tree.configure(yscrollcommand=pos_scroll.set)
        self._position_tree.grid(row=0, column=0, sticky="nsew")
        pos_scroll.grid(row=0, column=1, sticky="ns")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._position_tree.tag_configure("pnl_pos", foreground="green")
        self._position_tree.tag_configure("pnl_neg", foreground="red")
        self._position_tree.tag_configure("pnl_zero", foreground="black")

    def _build_backtest_tab(self, parent: ttk.Frame) -> None:
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self._report_text = tk.Text(parent, height=10, wrap="word")
        self._report_text.grid(row=0, column=0, sticky="nsew")
        self._report_text.insert("1.0", "尚未載入回測報告。\n")
        self._report_text.configure(state=tk.DISABLED)

        plot_frame = ttk.Frame(parent)
        plot_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        plot_frame.grid_rowconfigure(0, weight=1)
        plot_frame.grid_columnconfigure(0, weight=1)

        self._plot_frame = plot_frame
        self._canvas: Any = None
        self._figure: Any = None

        if Figure is None or FigureCanvasTkAgg is None:
            tk.Label(plot_frame, text="matplotlib 未安裝，無法顯示淨值曲線圖。", fg="red").grid(
                row=0, column=0, sticky="nsew"
            )

    # ------------------------------ Broker events ------------------------------

    def _register_broker_events(self) -> None:
        self.broker.on_order_update(lambda order: self._enqueue("order", order))
        self.broker.on_position_update(lambda pos: self._enqueue("position", pos))
        self.broker.on_account_update(lambda acc: self._enqueue("account", acc))
        self.broker.on_connection_lost(lambda: self._enqueue("connection_lost", None))

    def _enqueue(self, event_type: str, data: Any) -> None:
        # 高頻事件保護：queue 上限 500，超過時丟棄最舊事件。
        try:
            while self._queue.qsize() >= self._queue_max:
                self._queue.get_nowait()
        except Exception:
            pass
        try:
            self._queue.put_nowait((event_type, data))
        except Exception:
            return

    def _poll_queue(self) -> None:
        if self._closing:
            return
        updated = False
        try:
            while True:
                event_type, data = self._queue.get_nowait()
                if event_type == "order":
                    if self._connection_ok:
                        self._refresh_order_table(data)
                        updated = True
                elif event_type == "position":
                    if self._connection_ok:
                        self._refresh_position_table(data)
                        updated = True
                elif event_type == "account":
                    if self._connection_ok:
                        self._refresh_account_panel(data)
                        updated = True
                elif event_type == "connection_lost":
                    self._refresh_connection_lost()
                    updated = True
                elif event_type == "backtest_report":
                    self._render_backtest_report(data)
                    updated = True
        except queue.Empty:
            pass
        except Exception:
            # MVP：不讓 UI poll 因單一事件崩潰。
            pass

        if updated:
            self._touch_last_update()
        try:
            if not self._closing and self.root.winfo_exists():
                self._poll_after_id = self.root.after(100, self._poll_queue)
        except Exception:
            return

    # ------------------------------ UI refresh helpers ------------------------------

    def _refresh_account_panel(self, account: Account) -> None:
        self._last_account = account

        self._acc_balance_var.set(f"帳戶餘額：{self._fmt_money(account.acc_balance)}")
        self._equity_var.set(f"總資產估算：{self._fmt_money(account.equity)}")

        self._unrealized_var.set(f"未實現損益：{self._fmt_money(account.unrealized_pnl, signed=True)}")
        self._realized_var.set(f"已實現損益：{self._fmt_money(account.realized_pnl, signed=True)}")

        self._set_label_pnl_style(self._lbl_unrealized, account.unrealized_pnl)
        self._set_label_pnl_style(self._lbl_realized, account.realized_pnl)

    def _refresh_order_table(self, order: Order) -> None:
        order_id = str(order.order_id or "").strip() or f"seqno:{order.seqno}"
        self._order_data_by_id[order_id] = order
        self._render_order_table()

    def _render_order_table(self) -> None:
        items = list(self._order_data_by_id.items())
        items.sort(key=lambda kv: self._order_sort_key(kv[1]), reverse=True)

        for idx, (order_id, order) in enumerate(items):
            ts = str(getattr(order, "updated_at", "") or "").strip() or str(order.created_at or "")

            qty_val = int(order.quantity or 0)
            if str(order.status) == "Filled":
                filled_qty = int(getattr(order, "filled_quantity", 0) or 0)
                if filled_qty > 0:
                    qty_val = filled_qty

            values = (
                self._fmt_time(ts),
                str(order.code),
                str(order.action),
                str(order.order_type),
                self._fmt_price(order.price),
                self._fmt_price(order.filled_price),
                str(qty_val),
                str(order.status),
            )

            tag = str(order.status)
            item_id = self._order_item_by_id.get(order_id)
            if item_id is None:
                item_id = self._order_tree.insert("", tk.END, values=values, tags=(tag,))
                self._order_item_by_id[order_id] = item_id
            else:
                self._order_tree.item(item_id, values=values, tags=(tag,))

            try:
                self._order_tree.move(item_id, "", idx)
            except Exception:
                pass

    @staticmethod
    def _order_sort_key(order: Order) -> datetime:
        ts = str(getattr(order, "updated_at", "") or "").strip() or str(order.created_at or "")
        return TradingUI._parse_iso_dt(ts)

    @staticmethod
    def _parse_iso_dt(iso_ts: str) -> datetime:
        s = str(iso_ts or "").strip()
        if not s:
            return datetime.min.replace(tzinfo=timezone.utc)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    def _refresh_position_table(self, position: Position) -> None:
        code = str(position.code)
        try:
            pnl_val = float(position.pnl)
        except Exception:
            pnl_val = 0.0
        if pnl_val > 0:
            pnl_tag = "pnl_pos"
        elif pnl_val < 0:
            pnl_tag = "pnl_neg"
        else:
            pnl_tag = "pnl_zero"
        values = (
            code,
            str(position.direction),
            self._fmt_price(position.price),
            str(position.quantity),
            self._fmt_price(position.last_price),
            self._fmt_money(position.pnl, signed=True),
        )

        item_id = self._position_item_by_code.get(code)
        if item_id is None:
            item_id = self._position_tree.insert("", tk.END, values=values, tags=(pnl_tag,))
            self._position_item_by_code[code] = item_id
        else:
            self._position_tree.item(item_id, values=values, tags=(pnl_tag,))

    def _refresh_connection_lost(self) -> None:
        self._connection_ok = False
        self._status_var.set("狀態：連線中斷")

    def _render_backtest_report(self, report: PerformanceReport) -> None:
        # 切到回測頁籤
        try:
            self._tabs.select(self._tab_backtest)
        except Exception:
            pass

        # 文字報告
        lines = []
        lines.append(f"總報酬率：{report.total_return_pct:.2f}%")
        lines.append(f"勝率：{report.win_rate_pct:.2f}%")
        lines.append(f"最大回撤：{report.max_drawdown_pct:.2f}%")
        lines.append(f"Sharpe：{report.sharpe_ratio:.4f}")
        lines.append("")
        lines.append(f"交易次數：{report.total_trades}")
        lines.append(f"獲利筆數：{report.winning_trades}  虧損筆數：{report.losing_trades}")
        lines.append(f"平均獲利：{report.avg_win_pct:.2f}%  平均虧損：{report.avg_loss_pct:.2f}%")
        lines.append(f"Profit Factor：{report.profit_factor:.3f}")

        self._report_text.configure(state=tk.NORMAL)
        self._report_text.delete("1.0", tk.END)
        self._report_text.insert("1.0", "\n".join(lines) + "\n")
        self._report_text.configure(state=tk.DISABLED)

        # 繪圖
        if Figure is None or FigureCanvasTkAgg is None:
            return

        try:
            if self._figure is None:
                self._figure = Figure(figsize=(8, 4), dpi=100)
                ax = self._figure.add_subplot(111)
                ax.set_title("Equity Curve")
                ax.set_xlabel("Step")
                ax.set_ylabel("Equity")
                self._canvas = FigureCanvasTkAgg(self._figure, master=self._plot_frame)
                self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

            self._figure.clear()
            ax = self._figure.add_subplot(111)
            equity = list(report.equity_curve or [])
            ts_list = list(getattr(report, "equity_timestamps", []) or [])
            if len(equity) > 1000:
                # 降採樣：保留最多 1000 個點（等距抽樣）
                step = max(1, len(equity) // 1000)
                equity = equity[::step]
                if ts_list:
                    ts_list = ts_list[::step]

            # 優先使用回測時間戳當 X 軸，讓圖表時間軸可讀且有交易語意。
            if ts_list and len(ts_list) == len(equity):
                x_dt = [self._parse_iso_dt(ts) for ts in ts_list]
                ax.plot(x_dt, equity)
                locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
                ax.xaxis.set_major_locator(locator)
                ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
                ax.set_xlabel("時間")
            else:
                ax.plot(equity)
                ax.set_xlabel("時間步")
            ax.set_title("淨值曲線")
            ax.set_ylabel("淨值")
            ax.grid(True, alpha=0.3)

            if self._canvas is not None:
                self._canvas.draw()
        except Exception:
            # matplotlib 繪圖錯誤：顯示訊息但不讓 UI 崩潰。
            self._report_text.configure(state=tk.NORMAL)
            self._report_text.insert(tk.END, "\n[警告] 繪圖失敗，請確認 matplotlib 安裝與環境。\n")
            self._report_text.configure(state=tk.DISABLED)

    def _touch_last_update(self) -> None:
        self._last_update_var.set(f"最後更新：{datetime.now().strftime('%H:%M:%S')}")

    # ------------------------------ Formatting ------------------------------

    @staticmethod
    def _fmt_money(value: float, *, signed: bool = False) -> str:
        try:
            v = float(value)
        except Exception:
            v = 0.0
        sign = "+" if signed and v > 0 else ""
        return f"${sign}{v:,.0f}" if abs(v) >= 1 else f"${sign}{v:,.2f}"

    @staticmethod
    def _fmt_price(value: Optional[float]) -> str:
        if value is None:
            return ""
        try:
            v = float(value)
        except Exception:
            return str(value)
        if abs(v) >= 1:
            return f"{v:,.2f}"
        return f"{v:.6f}"

    @staticmethod
    def _fmt_time(iso_ts: str) -> str:
        s = str(iso_ts or "").strip()
        if not s:
            return ""
        # 嘗試解析 isoformat，顯示通用格式：
        # - 若時間為 00:00:00（常見於日 K 回測），顯示 YYYY-MM-DD
        # - 否則顯示 YYYY-MM-DD HH:MM:SS
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return s

    def _set_label_pnl_style(self, label: ttk.Label, pnl: float) -> None:
        try:
            v = float(pnl)
        except Exception:
            v = 0.0
        if v > 0:
            label.configure(style="PnlPos.TLabel")
        elif v < 0:
            label.configure(style="PnlNeg.TLabel")
        else:
            label.configure(style="PnlZero.TLabel")

    # ------------------------------ Lifecycle ------------------------------

    def _on_close(self) -> None:
        self._closing = True
        try:
            if self._poll_after_id is not None:
                self.root.after_cancel(self._poll_after_id)
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

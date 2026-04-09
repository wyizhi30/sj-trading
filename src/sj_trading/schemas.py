"""Schemas 模組（內部資料結構定義）。

本模組是整個系統的**資料格式定義中心**，刻意**只放資料結構**（不包含任何邏輯），
因此可被其他模組（MarketData / Broker / Strategy / Backtest / UI）安全引用。

欄位定義以專案文件 `docs/schemas.md` 為準。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Signal:
    """交易訊號（Strategy -> Broker）。"""

    strategy_id: str
    code: str
    action: str  # "Buy" / "Sell"
    order_type: str  # "MARKET" / "LIMIT"
    price: Optional[float]
    quantity: int
    timestamp: str


@dataclass
class Order:
    """訂單（Broker 對外回報用的統一格式）。"""

    order_id: str
    seqno: str
    strategy_id: str
    code: str
    action: str
    order_type: str
    price: Optional[float]
    quantity: int
    status: str
    filled_price: Optional[float]
    filled_quantity: int
    created_at: str
    updated_at: str
    error_message: Optional[str]


@dataclass
class Position:
    """持倉資訊。"""

    code: str
    direction: str  # "Buy" / "Sell"
    quantity: int
    price: float
    last_price: float
    pnl: float
    yd_quantity: int


@dataclass
class Account:
    """帳戶資金狀態（由 Broker 組合計算）。"""

    acc_balance: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    equity: float
    updated_at: str


@dataclass
class Tick:
    """內部 Tick 資料結構（現股）。

    欄位定義以專案文件 `docs/schemas.md` 為準。
    """

    code: str
    datetime: str
    price: float
    volume: int
    total_volume: int
    simtrade: int


@dataclass
class KBar:
    """內部 K 棒資料結構。

    欄位定義以專案文件 `docs/schemas.md` 為準。
    """

    code: str
    ts: str
    Open: float
    High: float
    Low: float
    Close: float
    Volume: int
    Amount: int
    interval: str


@dataclass
class TradeResult:
    """單筆交易損益（回測用）。"""

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
    """回測績效報告（回測用）。"""

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
    equity_curve: List[float]
    trade_history: List[TradeResult]
    equity_timestamps: List[str] = field(default_factory=list)

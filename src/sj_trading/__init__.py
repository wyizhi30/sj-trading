import os
from importlib import import_module

from dotenv import load_dotenv

from .backtest_engine import BacktestEngine, ExecutionSimulator, MetricsCalculator
from .broker_interface import (
    Account,
    BrokerInterface,
    EventEnvelope,
    KBar,
    LiveBrokerGateway,
    Order,
    PerformanceMetrics,
    Position,
    Signal,
    SimulatedBrokerGateway,
    Tick,
)
from .strategy import BaseStrategy, MovingAverageCrossStrategy, RiskGuard

__all__ = [
    "Account",
    "BacktestEngine",
    "BaseStrategy",
    "BrokerInterface",
    "EventEnvelope",
    "ExecutionSimulator",
    "KBar",
    "LiveBrokerGateway",
    "MetricsCalculator",
    "MovingAverageCrossStrategy",
    "Order",
    "PerformanceMetrics",
    "Position",
    "RiskGuard",
    "Signal",
    "SimulatedBrokerGateway",
    "Tick",
]


def main() -> None:
    load_dotenv()

    sj = import_module("shioaji")
    api = sj.Shioaji(simulation=True)
    api.login(
        api_key=os.environ["API_KEY"],
        secret_key=os.environ["SECRET_KEY"],
        fetch_contract=False,
    )
    api.activate_ca(
        ca_path=os.environ["CA_CERT_PATH"],
        ca_passwd=os.environ["CA_PASSWORD"],
    )
    print("login and activate ca success")

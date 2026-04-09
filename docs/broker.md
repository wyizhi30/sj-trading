# Broker 模組文件

## 模組職責

`broker.py` 是系統的**交易執行核心**，負責：

- 封裝 Shioaji API 的下單、撤單、帳務查詢
- 接收 Signal，轉換為 Shioaji 格式後呼叫 `api.place_order(contract, order)`
- 追蹤 Order 狀態變化，透過 `api.set_order_callback()` 接收回報
- 維護 Position 與 Account 狀態
- 透過事件通知機制讓 Strategy 與 UI 知道狀態變動

---

## 模組依賴關係

```
schemas.py  ←  broker.py  ←  strategy.py（只呼叫 place_signal）
                           ←  ui.py（只訂閱事件）
                           ←  backtest.py（使用 MockBroker）
```

---

## 兩種 Broker

| 類別 | 用途 |
|------|------|
| `Broker` | 實盤，連接 Shioaji API |
| `MockBroker` | 回測，本地模擬成交，不連網路 |

兩者繼承同一個 `BaseBroker`，對外介面完全相同。

---

## 初始化與登入

```python
import shioaji as sj

# simulation=True 傳入建構子（不是 login）
api = sj.Shioaji(simulation=True)

accounts = api.login(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    contracts_timeout=10000,   # 等待商品檔 10 秒
    subscribe_trade=True       # 自動訂閱委託/成交回報（預設 True）
)

# 帳號
stock_account  = api.stock_account    # 現股帳號
futopt_account = api.futopt_account   # 期貨帳號
```

> ⚠️ `simulation=True` 是在 `sj.Shioaji()` 傳入，不是 `login()`。

---

## 下單流程（Shioaji 實際呼叫方式）

### 現股下單

```python
contract = api.Contracts.Stocks["2330"]

order = api.Order(
    price=625.0,
    quantity=1,
    action=sj.constant.Action.Buy,            # Buy 或 Sell
    price_type=sj.constant.StockPriceType.LMT, # LMT=限價, MKT=市價
    order_type=sj.constant.OrderType.ROD,      # ROD=當日有效
    order_lot=sj.constant.StockOrderLot.Common,# Common=整張
    account=api.stock_account
)

trade = api.place_order(contract, order)
```

### 期貨下單

```python
contract = api.Contracts.Futures.TXF["TXFA5"]

order = api.Order(
    price=20000,
    quantity=1,
    action=sj.constant.Action.Buy,
    price_type=sj.constant.FuturesPriceType.LMT,
    order_type=sj.constant.OrderType.ROD,
    octype=sj.constant.FuturesOCType.Auto,     # Auto=自動判斷開平倉
    account=api.futopt_account
)

trade = api.place_order(contract, order)
```

---

## Signal → Shioaji Order 轉換邏輯

`place_signal(signal)` 內部執行以下轉換：

```python
def place_signal(self, signal: Signal) -> Order:
    # 1. 取得 contract
    contract = self.api.Contracts.Stocks[signal.code]

    # 2. 轉換 price_type
    if signal.order_type == "LIMIT":
        price_type = sj.constant.StockPriceType.LMT
    else:
        price_type = sj.constant.StockPriceType.MKT

    # 3. 建立 Shioaji Order
    sj_order = self.api.Order(
        price=signal.price or 0,
        quantity=signal.quantity,
        action=sj.constant.Action.Buy if signal.action == "Buy"
               else sj.constant.Action.Sell,
        price_type=price_type,
        order_type=sj.constant.OrderType.ROD,
        order_lot=sj.constant.StockOrderLot.Common,
        account=self.api.stock_account
    )

    # 4. 送出
    trade = self.api.place_order(contract, sj_order)

    # 5. 轉換為內部 Order schema 回傳
    return self._convert_trade(trade, signal.strategy_id)
```

---

## 成交回報（Order Callback）

Shioaji 透過 `api.set_order_callback()` 推送委託/成交回報：

```python
@api.on_order_callback
def order_callback(stat, msg):
    # stat: OrderState.StockOrder 或 OrderState.FuturesOrder
    # msg: dict，含 operation、order、status、contract 資訊
    order_id = msg['order']['id']
    status   = msg['status']
    # 更新內部 Order 狀態，觸發事件通知
```

**回報內容範例：**
```json
{
  "operation": { "op_type": "New", "op_code": "00", "op_msg": "" },
  "order": {
    "id": "de616839",
    "seqno": "500009",
    "action": "Buy",
    "price": 625.0,
    "quantity": 1,
    "order_type": "ROD",
    "price_type": "LMT"
  },
  "status": {
    "id": "de616839",
    "exchange_ts": 1744200000.0,
    "order_quantity": 1,
    "cancel_quantity": 0
  },
  "contract": { "security_type": "STK", "code": "2330" }
}
```

---

## 撤單

```python
# 取得目前所有委託
api.update_status(api.stock_account)
trades = api.list_trades()

# 找到要撤的 trade 撤單
api.cancel_order(trade)
```

---

## 帳務查詢

### 現股餘額

```python
balance = api.account_balance()
# balance.acc_balance → 交割帳戶餘額
```

### 持倉

```python
positions = api.list_positions(api.stock_account)
# 每筆 StockPosition：code, direction, quantity, price, last_price, pnl, yd_quantity
```

### 已實現損益

```python
from datetime import date
pnl_list = api.list_profit_loss(
    api.stock_account,
    begin_date=date.today().strftime('%Y-%m-%d'),
    end_date=date.today().strftime('%Y-%m-%d')
)
```

---

## 事件通知介面（對 Strategy 與 UI）

```python
broker.on_order_update(callback: Callable[[Order], None])
broker.on_position_update(callback: Callable[[Position], None])
broker.on_account_update(callback: Callable[[Account], None])
broker.on_connection_lost(callback: Callable[[], None])
```

同一事件可訂閱多個 callback。

---

## API 速率限制

| 操作 | 限制 |
|------|------|
| place_order / cancel_order / update_status 等 | 10 秒內上限 **250 次** |
| list_positions / account_balance 等帳務查詢 | 5 秒內上限 **25 次** |
| 連線數（同一 person_id） | 最多 **5 個** |
| 登入次數 | 每日上限 **1000 次** |

---

## MockBroker（回測用）

繼承 `BaseBroker`，所有對外介面與 `Broker` 完全相同，不連接 Shioaji。

### 模擬成交規則

| 訂單類型 | 成交條件 | 成交價格 |
|---------|---------|---------|
| `MARKET` | 下一根 K 棒直接成交 | 下一根 K 棒 `Open` 價 |
| `LIMIT` 買單 | K 棒 `Low` ≤ 委託價 | 委託價 |
| `LIMIT` 賣單 | K 棒 `High` ≥ 委託價 | 委託價 |

### MockBroker 專屬方法

```python
MockBroker.process_kbar(kbar: KBar)
# 每推入一根 K 棒，判斷是否有委託單成交，並觸發對應事件
```

---

## 範例程式碼

### 實盤初始化

```python
import shioaji as sj
from schemas import Signal

# 初始化
api = sj.Shioaji(simulation=True)
api.login(api_key="YOUR_KEY", secret_key="YOUR_SECRET", contracts_timeout=10000)

# 設定成交回報
@api.on_order_callback
def on_order(stat, msg):
    print(f"委託回報：{msg['order']['id']} 狀態：{msg['status']}")

# 下單範例（透過 Broker 包裝）
from broker import Broker
broker = Broker(api)

signal = Signal(
    strategy_id="ma_cross_v1",
    code="2330",
    action="Buy",
    order_type="LIMIT",
    price=625.0,
    quantity=1,
    timestamp="2026-04-08T09:05:00"
)

order = broker.place_signal(signal)
print(f"訂單建立：{order.order_id}")
```

### 回測用 MockBroker

```python
from broker import MockBroker
from schemas import Signal, KBar

broker = MockBroker()
broker.on_order_update(lambda o: print(f"訂單：{o.status}"))

signal = Signal(
    strategy_id="ma_cross_v1",
    code="2330",
    action="Buy",
    order_type="LIMIT",
    price=625.0,
    quantity=1,
    timestamp="2026-04-01T09:00:00"
)
broker.place_signal(signal)

# 推入 K 棒，Low=622 ≤ 委託 625 → 成交
kbar = KBar(code="2330", ts="2026-04-02T09:00:00",
            Open=623, High=628, Low=622, Close=627,
            Volume=30000, Amount=18810000, interval="1d")
broker.process_kbar(kbar)
```

---

## 錯誤處理

| 情況 | 處理方式 |
|------|---------|
| 登入失敗 | 拋出 `RuntimeError` |
| 下單 API 失敗 | Order 狀態設為 `Failed`，`error_message` 記錄原因，觸發事件 |
| 撤單已成交訂單 | 回傳 `False`，不拋出例外 |
| Shioaji 斷線 | 停止接收回報，觸發 `on_connection_lost` |
| 帳務查詢超速 | 捕捉例外，等待 5 秒後重試 |

---

## 注意事項

1. `place_order` 預設為阻塞模式（`timeout=5000`），等待交易所回應後才回傳
2. 非阻塞模式（`timeout=0`）執行更快（約 0.01 秒 vs 0.13 秒），但 Order 初始狀態為 `Inactive`，需透過 `on_order_callback` 取得正式狀態
3. 商品檔需在 `login()` 後等待下載完成（建議 `contracts_timeout=10000`）才能取得 `contract`
4. 同一帳號最多 5 個連線，`api.login()` 即建立一個連線，程式結束時應呼叫 `api.logout()`

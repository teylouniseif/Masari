import alpaca_trade_api as tradeapi

# Alpaca API Credentials (Replace with yours)
alpaca_api_key = "your_alpaca_api_key"
alpaca_api_secret = "your_alpaca_api_secret"
base_url = "https://paper-api.alpaca.markets"  # Use "https://api.alpaca.markets" for live trading

# Connect to Alpaca
api = tradeapi.REST(alpaca_api_key, alpaca_api_secret, base_url, api_version='v2')

# Function to Buy Stocks
def buy_stock(symbol, quantity):
    try:
        order = api.submit_order(
            symbol=symbol,
            qty=quantity,
            side="buy",
            type="market",
            time_in_force="gtc"  # Good till canceled
        )
        print(f"Buy Order Placed: {order.id}")
        return order.id
    except Exception as e:
        print(f"Buy order failed: {e}")
        return None

# Function to Sell Stocks
def sell_stock(symbol, quantity):
    try:
        order = api.submit_order(
            symbol=symbol,
            qty=quantity,
            side="sell",
            type="market",
            time_in_force="gtc"
        )
        print(f"Sell Order Placed: {order.id}")
        return order.id
    except Exception as e:
        print(f"Sell order failed: {e}")
        return None

# Function to Check Order Status
def check_order_status(order_id):
    try:
        order = api.get_order(order_id)
        print(f"Order Status for {order_id}: {order.status}")
    except Exception as e:
        print(f"Failed to retrieve order status: {e}")

# Example: Buy 10 shares of Apple (AAPL)
order_id = buy_stock("AAPL", 10)
if order_id:
    check_order_status(order_id)

# Example: Sell 5 shares of Tesla (TSLA)
order_id = sell_stock("TSLA", 5)
if order_id:
    check_order_status(order_id)

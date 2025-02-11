from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from web3 import Web3

# Binance API Credentials
binance_api_key = "your_binance_api_key"
binance_api_secret = "your_binance_api_secret"

# Connect to Binance
binance_client = Client(binance_api_key, binance_api_secret)

# Function to place a market order on Binance
def place_binance_order(symbol, side, quantity):
    try:
        order = binance_client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print(f"Binance Order Placed: {order['orderId']}")
        return order['orderId']
    except Exception as e:
        print(f"Binance Order Failed: {e}")
        return None

# Function to check Binance order status
def check_binance_order_status(symbol, order_id):
    try:
        order = binance_client.get_order(symbol=symbol, orderId=order_id)
        print(f"Binance Order Status: {order['status']}")
    except Exception as e:
        print(f"Failed to retrieve Binance order status: {e}")

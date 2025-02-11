from web3 import Web3
import json

# Infura connection
infura_url = "https://mainnet.infura.io/v3/your_infura_project_id"
web3 = Web3(Web3.HTTPProvider(infura_url))

# Ethereum Wallet Details
wallet_address = "your_ethereum_wallet_address"
private_key = "your_private_key"  # ⚠️ Keep this safe!

# Uniswap V2 Router Contract
uniswap_router_address = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
uniswap_router_abi = json.loads('[{"constant":false,"inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"name":"swapExactTokensForETH","outputs":[{"name":"amounts","type":"uint256[]"}],"payable":false,"stateMutability":"nonpayable","type":"function"}]')
uniswap_router = web3.eth.contract(address=uniswap_router_address, abi=uniswap_router_abi)

# USDT and ETH Addresses
usdt_address = Web3.to_checksum_address("0xdAC17F958D2ee523a2206206994597C13D831ec7")
eth_address = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")  # Native ETH

# Function to swap USDT for ETH
def buy_crypto_exchange(amount_in_usdt):
    try:
        # Convert USDT to Wei (assuming 6 decimals for USDT)
        amount_in = int(amount_in_usdt * (10 ** 6))
        amount_out_min = 0  # Set to 0 for simplicity, adjust for slippage

        # Transaction details
        nonce = web3.eth.get_transaction_count(wallet_address)
        deadline = web3.eth.get_block("latest")["timestamp"] + 600  # 10 min expiry

        txn = uniswap_router.functions.swapExactTokensForETH(
            amount_in,
            amount_out_min,
            [usdt_address, eth_address],
            wallet_address,
            deadline
        ).build_transaction({
            'from': wallet_address,
            'gas': 200000,
            'gasPrice': web3.eth.gas_price,
            'nonce': nonce
        })

        # Sign and send transaction
        signed_txn = web3.eth.account.sign_transaction(txn, private_key)
        txn_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)

        print(f"Transaction sent: {web3.to_hex(txn_hash)}")

    except Exception as e:
        print(f"ETH purchase failed: {e}")

# Example: Buy 100 USDT worth of ETH
# buy_eth(100)


eth_account = "your_ethereum_wallet_address"
eth_private_key = "your_private_key"  # ⚠️ Keep this safe

# Function to send ETH
def send_crypto(to_address, amount_eth):
    try:
        nonce = web3.eth.get_transaction_count(eth_account)
        gas_price = web3.eth.gas_price
        value = web3.to_wei(amount_eth, "ether")

        txn = {
            'nonce': nonce,
            'to': to_address,
            'value': value,
            'gas': 21000,
            'gasPrice': gas_price,
            'chainId': 1
        }

        signed_txn = web3.eth.account.sign_transaction(txn, eth_private_key)
        txn_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        print(f"Transaction sent: {web3.to_hex(txn_hash)}")
    except Exception as e:
        print(f"ETH Transfer failed: {e}")

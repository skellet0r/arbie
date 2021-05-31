import os

from brownie import ArbieV3, accounts
from brownie.convert import to_address
from brownie.network.gas.strategies import GasNowScalingStrategy
from pathlib import Path
import requests
import pandas as pd

PROJECT_DIR = Path(__file__).parent.parent
CHAIN_ID = 1
TOKENS_LIST_URL = f"https://apiv4.paraswap.io/v2/tokens/{CHAIN_ID}"


# Fetch the token list if it doesn't exist
tokens_fp = PROJECT_DIR.joinpath(f"data/tokens-chain-{CHAIN_ID}.csv")
if not tokens_fp.exists():
    tokens_fp.parent.mkdir(parents=True, exist_ok=True)
    tokens = requests.get(TOKENS_LIST_URL).json()["tokens"]
    tokens_df = pd.DataFrame.from_records(tokens, index="address")
    tokens_df.index = tokens_df.index.map(to_address)
    tokens_df.to_csv(tokens_fp)
else:
    tokens_df = pd.read_csv(tokens_fp, index_col="address")


tx_params = {
    "from": accounts.add(os.getenv("PRIVATE_KEY")),
    "gas_price": GasNowScalingStrategy("fast"),
}

# Helper functions
def get_token_addresses(*symbols):
    """Get a list of token addresses given their symbols"""
    addresses = []
    for symbol in symbols:
        addr = tokens_df[tokens_df["symbol"] == symbol].index[0]
        addresses.append(to_address(addr))
    return addresses


def main():
    coins = get_token_addresses("USDT", "WBTC", "WETH")
    ArbieV3.deploy(coins, tx_params)

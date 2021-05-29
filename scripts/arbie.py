import requests
import pandas as pd
from pathlib import Path
from loguru import logger
import sys
import itertools as it


PROJECT_DIR = Path(__file__).parent.parent
TOKENS_LIST_URL = "https://apiv4.paraswap.io/v2/tokens/1"
PRICES_URL = "https://apiv4.paraswap.io/v2/prices"
TX_BUIDLER_URL = "https://apiv4.paraswap.io/v2/transactions/1"

TRICRYPTO_SWAP_ADDR = "0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5"
MULTICALL2_ADDR = "0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696"
AUGUSTUSSWAPPER = "0x1bD435F3C054b6e901B7b108a0ab7617C808677b"

# Logger Setup
log_file = PROJECT_DIR.joinpath("logs/arbie.log")
log_format = "<g>{time}</> - <lvl>{level}</> - {message}"
logger.remove()
logger.add(sys.stdout, format=log_format)
logger.add(log_file, format=log_format, rotation="5 MB", compression="gz")

# Fetch the token list if it doesn't exist
tokens_fp = PROJECT_DIR.joinpath("data/tokens.csv")
if not tokens_fp.exists():
    tokens_fp.parent.mkdir(parents=True, exist_ok=True)
    tokens = requests.get(TOKENS_LIST_URL).json()["tokens"]
    tokens_df = pd.DataFrame.from_records(tokens, index="address")
    tokens_df.to_csv(tokens_fp)
    logger.debug("Fetched and saved token list from paraswap api")
else:
    tokens_df = pd.read_csv(tokens_fp, index_col="address")


# Helper functions


def get_token_addresses(*symbols):
    """Retrive a list of token addresses given their symbols"""
    mask = tokens_df.symbol.isin(symbols)
    return tokens_df[mask].index.tolist()


def get_pair_price(_from, to, amount, side, network=1, **kwargs):
    query_params = {
        "from": _from,
        "to": to,
        "amount": amount,
        "side": side,
        "network": network,
    }
    query_params.update(kwargs)
    return requests.get(PRICES_URL, params=query_params).json()


# Pool coins
tricrypto_swap_coin_symbols = ("USDT", "WBTC", "WETH")
tricrypto_swap_coin_addrs = get_token_addresses(*tricrypto_swap_coin_symbols)
swap_io_pairings = tuple(it.permutations(tricrypto_swap_coin_addrs, r=2))

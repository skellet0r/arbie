import concurrent.futures
import itertools as it
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from brownie import accounts, chain, interface, multicall
from cachecontrol import CacheControl
from loguru import logger
from retry import retry

ACCOUNT = accounts.add(os.getenv("PRIVATE_KEY"))

PROJECT_DIR = Path(__file__).parent.parent
# Using tor proxies CloudFlare interrupts :/
# PROXIES = {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Arbie"})
CACHED_SESSION = CacheControl(SESSION)
RANDOM_STATE = 42
N_THREADS = 30

# 1 = Ethereum Mainnet
TOKENS_LIST_URL = "https://apiv4.paraswap.io/v2/tokens/1"
PRICES_URL = "https://apiv4.paraswap.io/v2/prices"
TX_BUIDLER_URL = "https://apiv4.paraswap.io/v2/transactions/1"

# Contract Addrs
TRICRYPTO_SWAP_ADDR = "0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5"
MULTICALL2_ADDR = "0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696"
AUGUSTUSSWAPPER_ADDR = "0x1bD435F3C054b6e901B7b108a0ab7617C808677b"
LENDING_POOL_ADDR_PROVIDER_ADDR = "0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5"

# Contracts
LENDING_POOL_ADDR_PROVIDER = interface.ILendingPoolAddressesProvider(
    LENDING_POOL_ADDR_PROVIDER_ADDR
)
LENDING_POOL = interface.ILendingPool(LENDING_POOL_ADDR_PROVIDER.getLendingPool())
AUGUSTUSSWAPPER = interface.IAugustusSwapper(AUGUSTUSSWAPPER_ADDR)
CRYPTO_SWAP = interface.CryptoSwap(TRICRYPTO_SWAP_ADDR)

# Thread Pool initialized here to reduce overhead of constantly creating
THREAD_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS)


class TooManyRequests(Exception):
    pass


# Logger Setup
log_file = PROJECT_DIR.joinpath("logs/arbie.log")
log_format = "<g>{time}</> - <lvl>{level}</> - {message}"
logger.remove()
logger.add(sys.stdout, format=log_format)
logger.add(
    log_file, format=log_format, rotation="5 MB", compression="gz", buffering=512
)

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
    """Get a list of token addresses given their symbols"""
    mask = tokens_df.symbol.isin(symbols)
    return tokens_df[mask].index.tolist()


def get_prices_data(_from, to, amount, side="SELL", network=1, **kwargs):
    """Get pair price data from Paraswap API

    BUY = Buy amount _from asset equivalent to get_dx
    SELL = Sell amount _from asset and get x to asset equivalent to get_dy
    """
    query_params = {
        "from": _from,
        "to": to,
        "amount": amount,
        "side": side,
        "network": network,
        "includeDEXS": "Uniswap,Sushiswap,Aave2,Weth,Curve,Kyber,MultiPath,MegaPath,Compound,Bancor",  # noqa
        "includeContractMethods": "simpleSwap,multiSwap,megaSwap,swapOnUniswap,buy,swapOnUniswapFork",  # noqa
    }
    query_params.update(kwargs)
    resp = CACHED_SESSION.get(PRICES_URL, params=query_params)
    if resp.ok:
        return resp.json()
    elif resp.status_code == 429:
        raise TooManyRequests()
    else:
        raise Exception()


def get_crypto_swap_balances():
    """Get the token balances of the crypto swap"""
    with multicall(MULTICALL2_ADDR) as call:
        return [call(CRYPTO_SWAP).balances(i) for i in range(3)]


def unwrap_proxy(obj):
    return getattr(obj, "__wrapped__", obj)


# Pool coins
crypto_swap_coin_addrs = get_token_addresses("USDT", "WBTC", "WETH")
swap_io_pairs = list(it.permutations(range(3), r=2))
io_reverse_lookup = {idx: addr for idx, addr in zip(range(3), crypto_swap_coin_addrs)}


@retry(
    (concurrent.futures.TimeoutError, TooManyRequests),
    delay=10,
    max_delay=60,
    jitter=5,
    logger=logger,
)
def arbitrage_curve():
    balances = get_crypto_swap_balances()
    multicall_results = []

    # make calls to crypto_swap get_dy for 200 evenly spaced values
    # between [balances[i] / 10, balances[i] / 2]
    logger.debug("Starting call to multicall2")
    start_time = time.time()
    with multicall(MULTICALL2_ADDR) as call:
        for i, j in swap_io_pairs:
            balance = balances[i]
            for dx in np.linspace(balance * 0.10, balance * 0.50, 200):
                dx = int(dx)
                min_dy = call(CRYPTO_SWAP).get_dy(i, j, dx)
                multicall_results.append([i, j, dx, min_dy])
    logger.debug(f"Finished call to multicall2 in {time.time() - start_time:.2f}")

    # make calls to paraswap api checking for the best routes
    # min dy is the output of the curve swap
    df = pd.DataFrame(multicall_results, columns=["i", "j", "dx", "min_dy"]).applymap(
        unwrap_proxy
    )

    df["from"] = df["j"].replace(io_reverse_lookup)
    df["to"] = df["i"].replace(io_reverse_lookup)

    # take a random sample of 10% since we can't ping the paraswap api for all opportunities
    sampling_df = df.sample(frac=0.10)

    logger.debug(
        f"Calling Prices API {sampling_df.shape[0]} time(s) with {N_THREADS} threads"
    )
    start_time = time.time()
    # use threading to speed things up hopefully execution is <2-3 seconds
    # real time: up to 10 seconds at worst
    futures = THREAD_POOL.map(
        get_prices_data,
        sampling_df["from"].tolist(),
        sampling_df["to"].tolist(),
        sampling_df["min_dy"].tolist(),
        timeout=10,
    )
    results = list(futures)
    logger.debug(f"Finished making calls in {time.time() - start_time:.2f}s")
    sampling_df["results"] = results
    sampling_df["dest_amount"] = sampling_df["results"].map(
        lambda x: int(
            float(
                x.get("priceRoute", {}).get("bestRoute", [{}])[0].get("destAmount", 0)
            )
        )
    )
    sampling_df["profit"] = (
        sampling_df["dest_amount"] - sampling_df["dx"]
    ) / sampling_df["dest_amount"]
    return sampling_df


def main():
    for block in chain.new_blocks():
        logger.opt(colors=True).info(f"New block mined: <c>{block['number']}</>")
        data = arbitrage_curve()
        profit_margin = data["profit"].max()
        color = "<r>" if profit_margin < 0 else "<g>"
        logger.opt(colors=True).info(
            f"Arbitrage Curve Net Profit Margin: {color}{profit_margin:.2%}</>"
        )

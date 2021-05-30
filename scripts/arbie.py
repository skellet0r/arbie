import concurrent.futures
import itertools as it
import os
import sys
import time
from functools import lru_cache, partial
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from brownie import Contract, accounts, interface, multicall
from brownie.convert import to_address
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
TX_BUILDER_URL = "https://apiv4.paraswap.io/v2/transactions/1"

# Contract Addrs
TRICRYPTO_SWAP_ADDR = "0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5"
MULTICALL2_ADDR = "0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696"
AUGUSTUSSWAPPER_ADDR = "0x1bD435F3C054b6e901B7b108a0ab7617C808677b"
LENDING_POOL_ADDR_PROVIDER_ADDR = "0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5"

# Contracts
LENDING_POOL_ADDR_PROVIDER = interface.ILendingPoolAddressesProvider(
    LENDING_POOL_ADDR_PROVIDER_ADDR
)
LENDING_POOL = Contract(LENDING_POOL_ADDR_PROVIDER.getLendingPool())
AUGUSTUSSWAPPER = interface.IAugustusSwapper(AUGUSTUSSWAPPER_ADDR)
CRYPTO_SWAP = interface.CryptoSwap(TRICRYPTO_SWAP_ADDR)

# Contract Constants
with multicall(MULTICALL2_ADDR) as call:
    AAVE_FLASH_LOAN_FEE = call(LENDING_POOL).FLASHLOAN_PREMIUM_TOTAL()
AAVE_FLASH_LOAN_FEE /= 10_000  # .09%

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
    tokens_df.index = tokens_df.index.map(to_address)
    tokens_df.to_csv(tokens_fp)
    logger.debug("Fetched and saved token list from paraswap api")
else:
    tokens_df = pd.read_csv(tokens_fp, index_col="address")


# Helper functions
@lru_cache
def get_token_addresses(*symbols):
    """Get a list of token addresses given their symbols"""
    addresses = []
    for symbol in symbols:
        addr = tokens_df[tokens_df["symbol"] == symbol].index[0]
        addresses.append(to_address(addr))
    return addresses


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
        raise Exception(resp.status_code)


def get_crypto_swap_balances():
    """Get the token balances of the crypto swap"""
    with multicall(MULTICALL2_ADDR) as call:
        return [call(CRYPTO_SWAP).balances(i) for i in range(3)]


def unwrap_proxy(obj):
    return getattr(obj, "__wrapped__", obj)


def color(value):
    return "<g>" if value > 0 else "<r>"


def build_paraswap_tx(data):

    details = data["priceRoute"]["details"]

    from_token = to_address(details["tokenFrom"])
    to_token = to_address(details["tokenTo"])
    body = {
        "toDecimals": int(tokens_df.loc[to_token, "decimals"]),
        "fromDecimals": int(tokens_df.loc[from_token, "decimals"]),
        "referrer": "Arbie",
        "userAddress": ACCOUNT.address,
        "priceRoute": data["priceRoute"],
        "destAmount": int(details["destAmount"]) * 1,  # No slippage
        "srcAmount": int(details["srcAmount"]),
        "destToken": to_token,
        "srcToken": from_token,
    }

    headers = {"Content-Type": "application/json"}.update(SESSION.headers)
    params = {"skipChecks": "true"}
    tx = SESSION.post(TX_BUILDER_URL, json=body, params=params, headers=headers)
    return tx.json()["data"]


# Pool coins
crypto_swap_coin_addrs = get_token_addresses("USDT", "WBTC", "WETH")
swap_io_pairs = list(it.permutations(range(3), r=2))
io_reverse_lookup = {idx: addr for idx, addr in zip(range(3), crypto_swap_coin_addrs)}


def get_crypto_swap_io():
    balances = get_crypto_swap_balances()
    multicall_results = []

    # make calls to crypto_swap get_dy for 200 evenly spaced values
    # between [balances[i] / 10, balances[i] / 2]
    logger.debug("Starting call to multicall2")
    start_time = time.time()
    with multicall(MULTICALL2_ADDR) as call:
        for i, j in swap_io_pairs:
            balance = balances[i]
            for dx in np.linspace(balance // 200, balance // 3, 200):
                dx = int(dx)
                min_dy = call(CRYPTO_SWAP).get_dy(i, j, dx)
                multicall_results.append([i, j, dx, min_dy])
    logger.debug(f"Finished call to multicall2 in {time.time() - start_time:.2f}")
    return multicall_results


def arbitrage_curve(crypto_swap_io):

    multicall_results = crypto_swap_io

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
            float(x.get("priceRoute", {}).get("details", {}).get("destAmount", 0))
        )
    )
    sampling_df["profit"] = (
        sampling_df["dest_amount"] - sampling_df["dx"]
    ) / sampling_df["dx"]
    return sampling_df


def arbitrage_paraswap(crypto_swap_io):

    multicall_results = crypto_swap_io

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
    func = partial(get_prices_data, side="BUY")
    futures = THREAD_POOL.map(
        func,
        sampling_df["from"].tolist(),
        sampling_df["to"].tolist(),
        sampling_df["dx"].tolist(),
        timeout=10,
    )
    results = list(futures)
    logger.debug(f"Finished making calls in {time.time() - start_time:.2f}s")
    sampling_df["results"] = results
    sampling_df["src_amount"] = sampling_df["results"].map(
        lambda x: int(
            float(x.get("priceRoute", {}).get("details", {}).get("srcAmount", 0))
        )
    )
    sampling_df["profit"] = (
        sampling_df["min_dy"] - sampling_df["src_amount"]
    ) / sampling_df["src_amount"]
    return sampling_df


@retry(
    (concurrent.futures.TimeoutError, TooManyRequests),
    delay=15,
    backoff=1.2,
    logger=logger,
)
def go_arbie():
    crypto_swap_io = get_crypto_swap_io()

    curve_df = arbitrage_curve(crypto_swap_io)
    curve_row_idx = np.argmax(curve_df["profit"])
    gc_profit_margin = curve_df.iloc[curve_row_idx, -1]
    logger.opt(colors=True).info(
        f"Curve Arb Profit Margin: {color(gc_profit_margin)}{gc_profit_margin:.2%}</>"
    )

    paraswap_df = arbitrage_paraswap(crypto_swap_io)
    paraswap_row_idx = np.argmax(paraswap_df["profit"])
    gp_profit_margin = paraswap_df.iloc[paraswap_row_idx, -1]
    logger.opt(colors=True).info(
        f"Paraswap Arb Profit Margin: {color(gp_profit_margin)}{gp_profit_margin:.2%}</>"
    )

    if max(gc_profit_margin, gp_profit_margin) < AAVE_FLASH_LOAN_FEE:
        logger.opt(colors=True).info(
            f"<r>No opportunity available, profit margin is less than {AAVE_FLASH_LOAN_FEE:.2%}</>"
        )
        return


def main():
    pass

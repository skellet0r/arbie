import concurrent.futures
import itertools as it
from mmap import ALLOCATIONGRANULARITY
import os
import sys
import time
from functools import lru_cache, partial
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from brownie import ArbieV3, accounts, chain, interface, multicall, web3
from brownie.convert import to_address
from brownie.network.gas.strategies import GasNowScalingStrategy, GasNowStrategy
from cachecontrol import CacheControl
from eth_abi import abi
from hexbytes import HexBytes
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
CHAIN_ID = 1

# 1 = Ethereum Mainnet
TOKENS_LIST_URL = f"https://apiv4.paraswap.io/v2/tokens/{CHAIN_ID}"
PRICES_URL = "https://apiv4.paraswap.io/v2/prices"
TX_BUILDER_URL = f"https://apiv4.paraswap.io/v2/transactions/{CHAIN_ID}"
COIN_GECKO_API = (
    "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies={}"
)

# Contract Addrs
ARBIE_ADDR = "0x5CfB168f03f8185BD21a3d75f6887c6DCD2B1312"
TRICRYPTO_SWAP_ADDR = "0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5"
MULTICALL2_ADDR = "0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696"
AUGUSTUSSWAPPER_ADDR = "0x1bD435F3C054b6e901B7b108a0ab7617C808677b"
LENDING_POOL_ADDR_PROVIDER_ADDR = "0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5"

# Contracts
LENDING_POOL_ADDR_PROVIDER = interface.ILendingPoolAddressesProvider(
    LENDING_POOL_ADDR_PROVIDER_ADDR
)
LENDING_POOL = interface.IAAVELendingPool(LENDING_POOL_ADDR_PROVIDER.getLendingPool())
AUGUSTUSSWAPPER = interface.IAugustusSwapper(AUGUSTUSSWAPPER_ADDR)
CRYPTO_SWAP = interface.CryptoSwap(TRICRYPTO_SWAP_ADDR)
ARBIE = ArbieV3.at(ARBIE_ADDR)

# Contract Constants
with multicall(MULTICALL2_ADDR) as call:
    AAVE_FLASH_LOAN_FEE = call(LENDING_POOL).FLASHLOAN_PREMIUM_TOTAL()
AAVE_FLASH_LOAN_FEE = AAVE_FLASH_LOAN_FEE.__wrapped__ / 10_000  # .09%
SLIPPAGE = 0.01

ENCODE_TYP = "(bool,uint256,uint256,uint256,uint256,uint256,bytes)"

# Thread Pool initialized here to reduce overhead of constantly creating
THREAD_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS)

TX_PARAMS = {
    # "from": ACCOUNT,
    "gas_price": GasNowScalingStrategy("fast"),
    "required_confs": 3,
}


class TooManyRequests(Exception):
    pass


# Logger Setup
log_file = PROJECT_DIR.joinpath(f"logs/arbie-{CHAIN_ID}.log")
log_format = "<g>{time}</> - <lvl>{level}</> - {message}"
logger.remove()
logger.add(sys.stdout, format=log_format)
logger.add(
    log_file,
    format=log_format,
    rotation="5 MB",
    compression="gz",
    buffering=512,
    diagnose=False,
)
logger = logger.opt(colors=True)

# Fetch the token list if it doesn't exist
tokens_fp = PROJECT_DIR.joinpath(f"data/tokens-chain-{CHAIN_ID}.csv")
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


def get_prices_data(_from, to, amount, side="SELL", network=CHAIN_ID, **kwargs):
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
        "includeDEXS": "Uniswap,Sushiswap",  # noqa
    }
    query_params.update(kwargs)
    resp = CACHED_SESSION.get(PRICES_URL, params=query_params)
    if resp.ok:
        return resp.json()
    elif resp.status_code == 429:
        raise TooManyRequests()
    elif resp.status_code == 400:
        return {"priceRoute": {"details": {"srcAmount": 2 ** 256 - 1, "destAmount": 0}}}
    else:
        raise Exception(resp.status_code)


def gas_limit_to_cost(gas_limit, address):
    gas_price_eth = GasNowStrategy("fast").get_gas_price() / 10 ** 18  # in wei
    row = tokens_df.loc[address]
    symbol = row["symbol"]
    if symbol in ("WETH", "ETH"):
        return gas_limit * gas_price_eth * 10 ** 18, symbol, 18
    elif symbol == "WBTC":
        resp = requests.get(COIN_GECKO_API.format("btc"))
        eth_to_btc = resp.json()["ethereum"]["btc"]
        decimals = row["decimals"]
        return gas_price_eth * gas_limit * eth_to_btc * 10 ** decimals, symbol, decimals
    elif symbol == "USDT":
        resp = requests.get(COIN_GECKO_API.format("usd"))
        eth_to_usd = resp.json()["ethereum"]["usd"]
        decimals = row["decimals"]
        return gas_price_eth * gas_limit * eth_to_usd * 10 ** decimals, symbol, decimals


def unwrap_proxy(obj):
    return getattr(obj, "__wrapped__", obj)


def color(value):
    return "<g>" if value > AAVE_FLASH_LOAN_FEE else "<y>" if value > 0 else "<r>"


def build_paraswap_tx(data, is_arb_curve=False):

    details = data["priceRoute"]["details"]

    if is_arb_curve:
        details["destAmount"] = str(int(int(details["destAmount"]) * (1 - SLIPPAGE)))

    from_token = to_address(details["tokenFrom"])
    to_token = to_address(details["tokenTo"])
    body = {
        "toDecimals": int(tokens_df.loc[to_token, "decimals"]),
        "fromDecimals": int(tokens_df.loc[from_token, "decimals"]),
        "referrer": "Arbie",
        "userAddress": ARBIE_ADDR,
        "priceRoute": data["priceRoute"],
        "destAmount": details["destAmount"],  # need to account here
        "srcAmount": details["srcAmount"],
        "destToken": details["tokenTo"],
        "srcToken": details["tokenFrom"],
    }

    headers = {"Content-Type": "application/json"}.update(SESSION.headers)
    params = {"skipChecks": "true"}
    tx = SESSION.post(TX_BUILDER_URL, json=body, params=params, headers=headers)
    if tx.ok:
        return tx.json()
    elif tx.status_code == 429:
        raise TooManyRequests()
    else:
        raise Exception(tx.status_code)


# Pool coins
crypto_swap_coin_addrs = get_token_addresses("USDT", "WBTC", "WETH")
swap_io_pairs = list(it.permutations(range(len(crypto_swap_coin_addrs)), r=2))
io_reverse_lookup = {
    idx: addr
    for idx, addr in zip(range(len(crypto_swap_coin_addrs)), crypto_swap_coin_addrs)
}


def get_crypto_swap_balances():
    """Get the token balances of the crypto swap"""
    with multicall(MULTICALL2_ADDR) as call:
        return [call(CRYPTO_SWAP).balances(i) for i in range(3)]


def get_crypto_swap_io():
    balances = get_crypto_swap_balances()
    multicall_results = []

    # make calls to crypto_swap get_dy for 200 evenly spaced values
    # between [balances[i] / 10, balances[i] / 2]
    start_time = time.time()
    with multicall(MULTICALL2_ADDR) as call:
        for i, j in swap_io_pairs:
            balance = balances[i]
            for dx in np.linspace(balance // 500, balance // 250, 100):
                dx = int(dx)
                min_dy = call(CRYPTO_SWAP).get_dy(i, j, dx)
                multicall_results.append([i, j, dx, min_dy])
    logger.debug(f"Multicall2 response time: {time.time() - start_time:.2f}")
    return multicall_results


def arbitrage_curve(crypto_swap_io):
    # buy on curve sell on quickswap
    # aave i > curve j > paraswap i

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
        sampling_df["min_dy"].tolist(),  # input amount
        timeout=10,
    )
    results = list(futures)
    logger.debug(f"API response time: {time.time() - start_time:.2f}s")
    sampling_df["results"] = results
    sampling_df["dest_amount"] = sampling_df["results"].map(
        # need to account for in tx building
        lambda x: float(x["priceRoute"]["details"]["destAmount"])
        * (1 - SLIPPAGE)
    )
    sampling_df["profit"] = (
        sampling_df["dest_amount"] - sampling_df["dx"]
    ) / sampling_df["dx"]
    return sampling_df


def arbitrage_paraswap(crypto_swap_io):
    # buy on paraswap sell on curve
    # aave j > paraswap i > curve j

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
        # no need to account for in tx building call since
        # we do so in our initial call
        (sampling_df["dx"] * (1 + SLIPPAGE)).tolist(),
        timeout=10,
    )
    results = list(futures)
    logger.debug(f"API response time: {time.time() - start_time:.2f}s")
    sampling_df["results"] = results
    sampling_df["src_amount"] = sampling_df["results"].map(
        lambda x: float(x["priceRoute"]["details"]["srcAmount"])
    )
    sampling_df["profit"] = (
        sampling_df["min_dy"] - sampling_df["src_amount"]
    ) / sampling_df["src_amount"]
    return sampling_df


def go_arbie():
    crypto_swap_io = get_crypto_swap_io()

    curve_df = arbitrage_curve(crypto_swap_io)
    curve_row_idx = np.argmax(curve_df["profit"])
    gc_profit_margin = curve_df.iloc[curve_row_idx, -1]
    logger.opt(colors=True).info(
        f"Curve Arb Profit Margin: {color(gc_profit_margin)}{gc_profit_margin:.2%}</>"
    )

    if gc_profit_margin > AAVE_FLASH_LOAN_FEE:
        # arbing curve
        row = curve_df.iloc[curve_row_idx]
        paraswap_tx = build_paraswap_tx(row.results, True)
        paraswap_calldata = HexBytes(paraswap_tx["data"])
        # calldata given to arbie through the lending pool
        params = abi.encode_single(
            ENCODE_TYP,
            [
                True,
                int(row.i),
                int(row.j),
                int(row.dx),
                int(row.min_dy),
                chain.time() + 120,
                paraswap_calldata,
            ],
        )
        # calldata sent to lending pool
        # i > j > i
        calldata = LENDING_POOL.flashLoan.encode_input(
            ARBIE_ADDR,
            [crypto_swap_coin_addrs[row.i]],
            [int(row.dx)],
            [0],
            ACCOUNT.address,
            params,
            0,
        )
        gas_limit = web3.eth.estimate_gas(
            {"from": ACCOUNT.address, "to": LENDING_POOL.address, "data": calldata}
        )
        cost, symbol, decimals = gas_limit_to_cost(gas_limit, row["to"])
        logger.info(
            f"Estimated Gas Limit: {gas_limit} - Estimated cost: {cost / 10 ** decimals:.5f} {symbol}"
        )
        if row["dest_amount"] - (row["dx"] * (1 + AAVE_FLASH_LOAN_FEE)) - cost > 0:
            ACCOUNT.transfer(
                LENDING_POOL, data=calldata, gas_limit=gas_limit, **TX_PARAMS
            )

    paraswap_df = arbitrage_paraswap(crypto_swap_io)
    paraswap_row_idx = np.argmax(paraswap_df["profit"])
    gp_profit_margin = paraswap_df.iloc[paraswap_row_idx, -1]
    logger.opt(colors=True).info(
        f"Paraswap Arb Profit Margin: {color(gp_profit_margin)}{gp_profit_margin:.2%}</>"
    )

    if gp_profit_margin > AAVE_FLASH_LOAN_FEE:
        # arbing paraswap
        row = paraswap_df.iloc[paraswap_row_idx]
        paraswap_tx = build_paraswap_tx(row.results)
        paraswap_calldata = HexBytes(paraswap_tx["data"])
        params = abi.encode_single(
            ENCODE_TYP,
            [
                False,
                int(row.i),
                int(row.j),
                int(row.dx),
                int(row.min_dy),
                chain.time() + 120,
                paraswap_calldata,
            ],
        )
        # j > i > j
        calldata = LENDING_POOL.flashLoan.encode_input(
            ARBIE_ADDR,
            [crypto_swap_coin_addrs[row.j]],
            [int(row.src_amount)],
            [0],
            ACCOUNT.address,
            params,
            0,
        )

        gas_limit = web3.eth.estimate_gas(
            {"from": ACCOUNT.address, "to": LENDING_POOL.address, "data": calldata}
        )
        cost, symbol, decimals = gas_limit_to_cost(gas_limit, row["from"])
        logger.info(
            f"Estimated Gas Limit: {gas_limit} - Estimated cost: {cost / 10 ** decimals:.5f} {symbol}"
        )
        if row["min_dy"] - (row["src_amount"] * (1 + AAVE_FLASH_LOAN_FEE)) - cost > 0:
            ACCOUNT.transfer(
                LENDING_POOL, data=calldata, gas_limit=gas_limit, **TX_PARAMS
            )


@retry(
    (Exception),
    delay=15,
    backoff=1.2,
    logger=logger,
)
def main():
    for block in chain.new_blocks():
        logger.opt(colors=True).info(f"New block mined <c>{block['number']}</>")
        go_arbie()
        logger.debug("Sleeping for 5s")
        time.sleep(5)
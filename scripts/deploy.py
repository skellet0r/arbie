import os

from brownie import ArbieV3, accounts
from brownie.network.gas.strategies import GasNowScalingStrategy

USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
WBTC = "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

tx_params = {
    "from": accounts.add(os.getenv("PRIVATE_KEY")),
    "gas_price": GasNowScalingStrategy("fast"),
}


def main():
    ArbieV3.deploy([USDT, WBTC, WETH], tx_params)

import pytest
from brownie import Contract
from brownie_tokens import MintableForkToken


@pytest.fixture(scope="session")
def alice(accounts):
    """Dummy account for testing"""
    return accounts[0]


@pytest.fixture(scope="session")
def bob(accounts):
    """Dummy account for testing"""
    return accounts[1]


@pytest.fixture(scope="session")
def charlie(accounts):
    """Dummy account for testing"""
    return accounts[2]


crypto_swap_coins = [
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
]


@pytest.fixture(scope="session")
def usdt():
    contract = Contract(crypto_swap_coins[0])
    setattr(contract, "strip", lambda: crypto_swap_coins[0])
    return MintableForkToken(contract)


@pytest.fixture(scope="session")
def wbtc():
    contract = Contract(crypto_swap_coins[1])
    setattr(contract, "strip", lambda: crypto_swap_coins[1])
    return MintableForkToken(contract)


@pytest.fixture(scope="session")
def weth():
    contract = Contract(crypto_swap_coins[2])
    setattr(contract, "strip", lambda: crypto_swap_coins[2])
    return MintableForkToken(contract)


@pytest.fixture(scope="session")
def coins(usdt, wbtc, weth):
    return [usdt, wbtc, weth]


@pytest.fixture(scope="session")
def crypto_swap(interface):
    """Curve USDT/WBTC/WETH TriCryptoSwap"""
    return interface.CryptoSwap("0x80466c64868E1ab14a1Ddf27A676C3fcBE638Fe5")


@pytest.fixture(scope="session")
def lending_pool(interface):
    """AAVE Lending Pool"""
    lending_pool_registry = interface.ILendingPoolAddressesProvider(
        "0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5"
    )
    return interface.IAAVELendingPool(lending_pool_registry.getLendingPool())


@pytest.fixture(scope="session")
def augustus_swap(interface):
    """Paraswap swapper contract"""
    return interface.IAugustusSwapper("0x1bD435F3C054b6e901B7b108a0ab7617C808677b")


@pytest.fixture(scope="session")
def token_transfer_proxy(accounts, augustus_swap, interface):
    """Paraswap token transfer proxy contract"""
    return interface.ITokenTransferProxy(augustus_swap.getTokenTransferProxy())


@pytest.fixture(scope="session")
def multicall2_addr():
    """Address of Multicall2"""
    return "0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696"


@pytest.fixture(scope="module")
def arbie(alice, ArbieV3):
    return ArbieV3.deploy(crypto_swap_coins, {"from": alice})


@pytest.fixture(autouse=True)
def test_isolation(fn_isolation):
    pass


@pytest.fixture(scope="session")
def crypto_swap_balances(crypto_swap):
    def _crypto_swap_balances():
        return [crypto_swap.balances(i) for i in range(3)]

    return _crypto_swap_balances


@pytest.fixture(scope="session")
def uniswap_factory(interface):
    return interface.IUniswapV2Factory("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")


@pytest.fixture(scope="session")
def uniswap_router(interface):
    return interface.IUniswapV2Router02("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")


@pytest.fixture(scope="session")
def get_pair(interface, uniswap_factory):
    def _get_pair(coin_a, coin_b):
        pair_addr = uniswap_factory.getPair(coin_a, coin_b)
        return interface.IUniswapV2Pair(pair_addr)

    return _get_pair

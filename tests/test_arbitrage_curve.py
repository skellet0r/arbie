import pytest


@pytest.fixture(scope="module", autouse=True)
def setup(
    alice,
    coins,
    crypto_swap,
    crypto_swap_balances,
    token_transfer_proxy,
    usdt,
    wbtc,
    router,
    get_pair,
):
    for coin in coins:
        # mint the same balances as the crypto_swap pool has
        amount = 1_000_000_000 * 10 ** coin.decimals()  # a billion of each token
        coin._mint_for_testing(alice, amount)
        coin.approve(token_transfer_proxy, 2 ** 256 - 1, {"from": alice})
        coin.approve(router, 2 ** 256 - 1, {"from": alice})
        coin.approve(crypto_swap, 2 ** 256 - 1, {"from": alice})

    # create favorable conditions for an arb (usdt > wbtc > usdt)
    # first give the uniswap pool enough liquidity for a big trade

    pair = get_pair(usdt, wbtc)
    # wbtc = coin_0, usdt = coin_1
    reserve_0, reserve_1, _ = pair.getReserves()
    quote = router.quote(100 * 10 ** 8, reserve_0, reserve_1)

    # add liquidity to the uniswap pool, given current market conditions
    # this should be enough liquidity so we don't have to worry about changing conditions
    # in the future
    router.addLiquidity(
        wbtc, usdt, 100 * 10 ** 8, quote, 0, 0, alice, 2 ** 32 - 1, {"from": alice}
    )

    # deposit a bunch of wbtc into the crypto_pool, giving it a low price

from eth_abi import abi
from hexbytes import HexBytes


def test_arbing_curve(
    alice,
    arbie,
    augustus_swap,
    coins,
    crypto_swap,
    token_transfer_proxy,
    usdt,
    wbtc,
    uniswap_router,
    get_pair,
):
    # add a bunch of liquidity to uniswap pair
    pair = get_pair(usdt, wbtc)
    # wbtc = coin_0, usdt = coin_1
    reserve_0, reserve_1, _ = pair.getReserves()
    quote = uniswap_router.quote(100 * 10 ** 8, reserve_0, reserve_1)
    usdt._mint_for_testing(pair, quote)
    wbtc._mint_for_testing(pair, 100 * 10 ** 8)
    pair.mint(alice, {"from": alice})

    for coin in coins:
        # mint a bunch of tokens to alice
        amount = 1_000_000 * 10 ** coin.decimals()  # a billion of each token
        coin._mint_for_testing(alice, amount)
        coin.approve(token_transfer_proxy, 2 ** 256 - 1, {"from": alice})
        coin.approve(uniswap_router, 2 ** 256 - 1, {"from": alice})
        coin.approve(pair, 2 ** 256 - 1, {"from": alice})
        coin.approve(crypto_swap, 2 ** 256 - 1, {"from": alice})

    wbtc_price = crypto_swap.price_oracle(0) // 10 ** 18
    # swap usdt for ~100 wbtc
    get_dy_before = crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6)
    # deposit a bunch of wbtc into the crypto_pool, giving it a low price
    crypto_swap.add_liquidity([0, 100_000 * 10 ** 8, 0], 0, {"from": alice})
    # I should be able to get more wbtc now then I did before
    assert crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6) > get_dy_before

    # I should now be able to do a favorable arb (usdt > wbtc > usdt)
    usdt_initial_amount = 100 * wbtc_price * 10 ** 6
    # this is the amount out after the curve trade
    min_dy = crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6)
    # now selling on uniswap
    reserve_0, reserve_1, _ = pair.getReserves()
    usdt_amount_out = uniswap_router.getAmountOut(min_dy, reserve_0, reserve_1)

    assert usdt_amount_out > usdt_initial_amount

    usdt._mint_for_testing(arbie, usdt_initial_amount)
    # craft the calldata for paraswap swapping on uniswap
    paraswap_calldata = augustus_swap.swapOnUniswap.encode_input(
        min_dy, usdt_amount_out, [wbtc, usdt], 0
    )

    typ = "(bool,uint256,uint256,uint256,uint256,uint256,bytes)"
    param = abi.encode_single(
        typ,
        [
            True,
            0,
            1,
            usdt_initial_amount,
            min_dy,
            2 ** 32 - 1,
            HexBytes(paraswap_calldata),
        ],
    )
    balance_before = usdt.balanceOf(alice)
    # now perform the arb
    arbie.executeOperation(
        [usdt],
        [usdt_initial_amount],
        [int(usdt_initial_amount * 0.0009)],
        alice,
        param,
        {"from": alice},
    )
    # withdraw the left over
    arbie.withdrawToken(usdt, {"from": alice})

    assert usdt.balanceOf(alice) > balance_before


def test_arbing_curve_flash_loaned(
    alice,
    arbie,
    augustus_swap,
    coins,
    crypto_swap,
    lending_pool,
    token_transfer_proxy,
    usdt,
    wbtc,
    uniswap_router,
    get_pair,
):
    # add a bunch of liquidity to uniswap pair
    pair = get_pair(usdt, wbtc)
    # wbtc = coin_0, usdt = coin_1
    reserve_0, reserve_1, _ = pair.getReserves()
    quote = uniswap_router.quote(100 * 10 ** 8, reserve_0, reserve_1)
    usdt._mint_for_testing(pair, quote)
    wbtc._mint_for_testing(pair, 100 * 10 ** 8)
    pair.mint(alice, {"from": alice})

    for coin in coins:
        # mint a bunch of tokens to alice
        amount = 1_000_000 * 10 ** coin.decimals()  # a billion of each token
        coin._mint_for_testing(alice, amount)
        coin.approve(token_transfer_proxy, 2 ** 256 - 1, {"from": alice})
        coin.approve(uniswap_router, 2 ** 256 - 1, {"from": alice})
        coin.approve(pair, 2 ** 256 - 1, {"from": alice})
        coin.approve(crypto_swap, 2 ** 256 - 1, {"from": alice})

    wbtc_price = crypto_swap.price_oracle(0) // 10 ** 18
    # swap usdt for ~100 wbtc
    get_dy_before = crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6)
    # deposit a bunch of wbtc into the crypto_pool, giving it a low price
    crypto_swap.add_liquidity([0, 100_000 * 10 ** 8, 0], 0, {"from": alice})
    # I should be able to get more wbtc now then I did before
    assert crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6) > get_dy_before

    # I should now be able to do a favorable arb (usdt > wbtc > usdt)
    usdt_initial_amount = 100 * wbtc_price * 10 ** 6
    # this is the amount out after the curve trade
    min_dy = crypto_swap.get_dy(0, 1, 100 * wbtc_price * 10 ** 6)
    # now selling on uniswap
    reserve_0, reserve_1, _ = pair.getReserves()
    usdt_amount_out = uniswap_router.getAmountOut(min_dy, reserve_0, reserve_1)

    assert usdt_amount_out > usdt_initial_amount

    # instead of minting into the contract we will use a flash loan
    # usdt._mint_for_testing(arbie, usdt_initial_amount)
    # craft the calldata for paraswap swapping on uniswap
    paraswap_calldata = augustus_swap.swapOnUniswap.encode_input(
        min_dy, usdt_amount_out, [wbtc, usdt], 0
    )

    typ = "(bool,uint256,uint256,uint256,uint256,uint256,bytes)"
    param = abi.encode_single(
        typ,
        [
            True,
            0,
            1,
            usdt_initial_amount,
            min_dy,
            2 ** 32 - 1,
            HexBytes(paraswap_calldata),
        ],
    )
    balance_before = usdt.balanceOf(alice)

    lending_pool.flashLoan(
        arbie, [usdt], [usdt_initial_amount], [0], alice, param, 0, {"from": alice}
    )

    assert usdt.balanceOf(alice) > balance_before

# ArbieV3

> Note: Arbie is for testing purposes only, and should not be used in a production environment without serious design considerations and optimizations.

This is the third iteration of the Arbie arbitrage bot. This iteration relies heavily on the paraswap API for identifying an optimal arbitrage route.
This iteration of ARbie also has the ability to use Flash Loans via AAVEv2, removing the initial capital requirements to perform an arbitrage trade.

Proof that ArbieV3 actually work can be found in the following transactions:

- [0x0795e8785dcc4371865d3bd4cf337a2c054b505c5e81b1d07ac7f7106e1864c5](https://etherscan.io/tx/0x0795e8785dcc4371865d3bd4cf337a2c054b505c5e81b1d07ac7f7106e1864c5)
- [0x0a121a9f06519dbd39b762973399bb98eab00758d5f7a24357a2c259e22d5460](https://etherscan.io/tx/0x0a121a9f06519dbd39b762973399bb98eab00758d5f7a24357a2c259e22d5460)

Improvements to be made:

- Speed ... can she be faster
- Slippage ... can she better account for slippage
  - Math overall should be checked
- Gas accounting ... she should instantly convert her profit into ETH to enable perpetual arbitrage

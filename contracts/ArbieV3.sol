// SPDX-License-Identifier: MIT
pragma solidity 0.6.12;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {
    FlashLoanReceiverBase
} from "@aave/contracts/flashloan/base/FlashLoanReceiverBase.sol";
import {
    ILendingPoolAddressesProvider
} from "@aave/contracts/interfaces/ILendingPoolAddressesProvider.sol";

contract ArbieV3 is FlashLoanReceiverBase, Ownable {
    ILendingPoolAddressesProvider constant LENDING_POOL_ADDRESS_PROVIDER =
        ILendingPoolAddressesProvider(
            0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5
        );

    constructor() public FlashLoanReceiverBase(LENDING_POOL_ADDRESS_PROVIDER) {}

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        return true;
    }
}

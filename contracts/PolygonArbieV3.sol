// SPDX-License-Identifier: MIT
pragma solidity 0.6.12;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {
    FlashLoanReceiverBase
} from "@aave/contracts/flashloan/base/FlashLoanReceiverBase.sol";
import {
    ILendingPoolAddressesProvider
} from "@aave/contracts/interfaces/ILendingPoolAddressesProvider.sol";

interface IERC20 {
    function approve(address _spender, uint256 _amount) external;

    function allowance(address _owner, address _spender)
        external
        returns (uint256);

    function balanceOf(address _account) external returns (uint256);

    function transfer(address _to, uint256 _amount) external;

    function transferFrom(
        address _from,
        address _to,
        uint256 _amount
    ) external;
}

interface TriCryptoZap {
    function exchange_underlying(
        uint256 _i,
        uint256 _j,
        uint256 _dx,
        uint256 _min_dy
    ) external;
}

contract PolygonArbieV3 is FlashLoanReceiverBase, Ownable {
    bytes4 constant CURVE_FN_SELECTOR = 0xe22c63c0;
    bytes4 constant PARASWAP_FN_SELECTOR = 0xe83ec731;
    address constant TOKEN_TRANSFER_PROxY_ADDR =
        0xCD52384e2A96F6E91e4e420de2F9a8C0f1FFB449;
    address constant PARASWAP_ADDR = 0x90249ed4d69D70E709fFCd8beE2c5A566f65dADE;

    TriCryptoZap constant CRYPTO_ZAP =
        TriCryptoZap(0x3FCD5De6A9fC8A99995c406c77DDa3eD7E406f81);

    ILendingPoolAddressesProvider constant LENDING_POOL_ADDRESS_PROVIDER =
        ILendingPoolAddressesProvider(
            0xd05e3E715d945B59290df0ae8eF85c1BdB684744
        );

    address private inputAsset;
    uint256 private amountToReturn;

    constructor(address[] memory coins)
        public
        FlashLoanReceiverBase(LENDING_POOL_ADDRESS_PROVIDER)
    {
        address lendingPool = LENDING_POOL_ADDRESS_PROVIDER.getLendingPool();
        for (uint256 i = 0; i < coins.length; i++) {
            address coin = coins[i];
            IERC20(coin).approve(address(CRYPTO_ZAP), uint256(-1));
            IERC20(coin).approve(TOKEN_TRANSFER_PROxY_ADDR, uint256(-1));
            IERC20(coin).approve(lendingPool, uint256(-1));
        }
    }

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        inputAsset = assets[0];
        amountToReturn = amounts[0].add(premiums[0]);

        (
            bool _isCurveArbitrage,
            uint256 _i,
            uint256 _j,
            uint256 _dx,
            uint256 _min_dy,
            uint256 _deadline,
            bytes memory _paraswap_calldata
        ) =
            abi.decode(
                params,
                (bool, uint256, uint256, uint256, uint256, uint256, bytes)
            );

        if (_isCurveArbitrage) {
            PolygonArbieV3.arbitrageCurve(
                _i,
                _j,
                _dx,
                _min_dy,
                _deadline,
                _paraswap_calldata
            );
        } else {
            PolygonArbieV3.arbitrageParaswap(
                _i,
                _j,
                _dx,
                _min_dy,
                _deadline,
                _paraswap_calldata
            );
        }

        return true;
    }

    /// buy low on curve, sell high on paraswap
    function arbitrageCurve(
        uint256 _i,
        uint256 _j,
        uint256 _dx,
        uint256 _min_dy,
        uint256 _deadline,
        bytes memory _paraswap_calldata
    ) public {
        require(block.timestamp < _deadline); // dev: deadline passed

        CRYPTO_ZAP.exchange_underlying(_i, _j, _dx, _min_dy);
        (bool success, bytes memory returnData) =
            PARASWAP_ADDR.call(_paraswap_calldata);
        require(success); // dev: call to paraswap failed

        uint256 balance = IERC20(inputAsset).balanceOf(address(this));
        uint256 profit = balance.sub(amountToReturn);

        require(profit > 0); // dev: no profit
        // trnasfer profit out and set storage variables to 0
        IERC20(inputAsset).transfer(Ownable.owner(), profit);
        inputAsset = address(0);
        amountToReturn = 0;
    }

    /// buy low on paraswap, sell high on curve
    function arbitrageParaswap(
        uint256 _i,
        uint256 _j,
        uint256 _dx,
        uint256 _min_dy,
        uint256 _deadline,
        bytes memory _paraswap_calldata
    ) public {
        require(block.timestamp < _deadline); // dev: deadline passed

        (bool success, bytes memory returnData) =
            PARASWAP_ADDR.call(_paraswap_calldata);
        require(success); // dev: call to paraswap failed
        CRYPTO_ZAP.exchange_underlying(_i, _j, _dx, _min_dy);

        uint256 balance = IERC20(inputAsset).balanceOf(address(this));
        uint256 profit = balance.sub(amountToReturn);

        require(profit > 0); // dev: no profit
        // trnasfer profit out and set storage variables to 0
        IERC20(inputAsset).transfer(Ownable.owner(), profit);
        inputAsset = address(0);
        amountToReturn = 0;
    }

    function withdrawToken(address _token) external {
        uint256 balance = IERC20(_token).balanceOf(address(this));
        IERC20(_token).transfer(Ownable.owner(), balance);
    }
}

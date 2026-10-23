#!/usr/bin/env python
"""Deploy both contracts to StudioNet and write frontend/config.json.

The test suite deploys fresh contracts per session; the read-only
frontend needs addresses that persist between runs, so this script does
a stable deploy and records what it deployed.

Usage:
    .venv\\Scripts\\python.exe scripts\\deploy.py
"""
import json
import os
import sys
import time

RPC = "https://studio.genlayer.com/api"
CHAIN_ID = 61999

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(ROOT, "frontend")


def main() -> int:
    print("== Deploying contracts to StudioNet ==\n")

    from gltest_cli.config.user import load_user_config, user_config_exists
    from gltest_cli.config.general import get_general_config

    if not user_config_exists():
        print("FAIL: gltest.config.yaml not found in current directory")
        return 1
    get_general_config().user_config = load_user_config("gltest.config.yaml")

    from gltest import get_contract_factory

    operator_factory = get_contract_factory(
        contract_file_path=os.path.join(ROOT, "contracts", "review_verifier.py")
    )
    marketplace_factory = get_contract_factory(
        contract_file_path=os.path.join(ROOT, "contracts", "demo_marketplace.py")
    )

    # Accounts come from gltest.config.yaml: [0]=deployer, [1]=buyer, [2]=seller.
    # There is no privileged operator: the deployer address is recorded as
    # `reader` only because unsigned read simulations (eth_call style) need
    # a sender identity; it carries no rights on the contracts.
    from gltest.accounts import get_accounts

    accounts = get_accounts()
    operator = accounts[0]
    operator_addr = str(operator.address)
    buyer_addr = str(accounts[1].address) if len(accounts) > 1 else None
    seller_addr = str(accounts[2].address) if len(accounts) > 2 else None

    print("[1/2] deploying ReviewVerifier() ...")
    verifier = operator_factory.deploy(args=[], account=operator)
    print(f"      -> {verifier.address}")

    print("[2/2] deploying DemoMarketplace() ...")
    marketplace = marketplace_factory.deploy(args=[], account=operator)
    print(f"      -> {marketplace.address}")

    config = {
        "rpc": RPC,
        "chainId": CHAIN_ID,
        "verifier": str(verifier.address),
        "marketplace": str(marketplace.address),
        "reader": operator_addr,
        "buyer": buyer_addr,
        "seller": seller_addr,
        "deployedAt": int(time.time()),
    }

    os.makedirs(FRONTEND_DIR, exist_ok=True)
    config_path = os.path.join(FRONTEND_DIR, "config.json")
    # encoding="utf-8" (no BOM): the browser's Response.json() throws on a
    # BOM and the frontend silently fails to boot.
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    print(f"\nWrote {config_path}")
    print(json.dumps(config, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

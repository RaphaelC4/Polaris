#!/usr/bin/env python
"""Preflight: sanity-check the live StudioNet setup before running pytest.

Checks, in order:
  1. JSON-RPC reachability + chain id of studio.genlayer.com/api
  2. gltest config parses and both accounts are loadable
  3. Guidance for funding: StudioNet has no balance RPC exposed through
     genlayer_py, so gas must be added via the studio.genlayer.com faucet.
     This script prints the exact addresses to fund and (best-effort) probes
     whether an account has gas by observing whether a cheap transaction is
     accepted.

Usage:
    .venv\\Scripts\\python.exe scripts\\preflight.py
"""
import json
import sys
import time
import urllib.request

from gltest_cli.config.user import user_config_exists

RPC = "https://studio.genlayer.com/api"
EXPECTED_CHAIN = 61999

OPERATOR = "0x7b56902aedf906044E40e8821A6fc412Ad0808dB"
ALICE = "0x2B13BCB1281295Aa7Cb77326C02beE7768F78Ab0"


def rpc(method: str, params: list):
    """JSON-RPC call with small retry loop (Studio TLS occasionally hiccups)."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                RPC,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())
            if "error" in body:
                raise RuntimeError(f"RPC error: {body['error']}")
            return body["result"]
        except urllib.error.HTTPError:
            raise  # real HTTP error (e.g. 403): surface immediately
        except Exception as exc:  # TLS resets / timeouts: back off and retry
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


def main() -> int:
    print("== GenLayer Review Verifier preflight ==\n")
    failures = 0

    print(f"[1/3] JSON-RPC {RPC}")
    try:
        chain = int(rpc("eth_chainId", []), 16)
        print(f"      reachable, chainId={chain}")
        if chain != EXPECTED_CHAIN:
            print(f"      WARNING: expected chain {EXPECTED_CHAIN}")
            failures += 1
    except Exception as exc:
        print(f"      FAIL: {exc}")
        return 1

    print("[2/3] gltest config")
    try:
        # The gltest pytest plugin loads gltest.config.yaml into its
        # GeneralConfig singleton during pytest_configure; outside pytest we
        # bootstrap it the same way so gltest APIs work in this script.
        from gltest_cli.config.user import load_user_config
        from gltest_cli.config.general import get_general_config
        from gltest.accounts import get_accounts

        if not user_config_exists():
            print("      FAIL: gltest.config.yaml not found in current directory")
            return 1
        get_general_config().user_config = load_user_config("gltest.config.yaml")

        accounts = get_accounts()
        addrs = [str(a.address) for a in accounts]
        print(f"      loaded {len(addrs)} account(s): {addrs}")
    except Exception as exc:
        print(f"      FAIL: {exc}")
        return 1

    print("[3/3] funding check (no balance RPC - probing with a cheap tx)")
    print(f"      operator : {OPERATOR}")
    print(f"      alice    : {ALICE}")
    print("      Fund both at https://studio.genlayer.com (Faucet / Send),")
    print("      then re-run this script. A funded account shows 'gas OK'.")
    try:
        from gltest.fixtures import default_account  # noqa: F401
    except Exception:
        pass

    # Probe via a marketplace deploy (small, no AI calls).
    try:
        import os

        from gltest import get_contract_factory

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        factory = get_contract_factory(
            contract_file_path=os.path.join(root, "contracts", "demo_marketplace.py")
        )
        contract = factory.deploy(args=[])
        # if deploy returned without raising, the operator has gas
        print(f"      operator gas OK (deploy accepted, addr={contract.address})")
        print("\nPreflight passed. Run the suite with:")
        print("    .venv\\Scripts\\python.exe -m pytest -m \"not e2e\" -v")
        return 0 if failures == 0 else 1
    except Exception as exc:
        print(f"      operator probe failed (likely unfunded): {exc}")
        print("\nPreflight incomplete: fund the accounts above and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

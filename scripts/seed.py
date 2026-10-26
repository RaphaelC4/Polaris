#!/usr/bin/env python
"""Seed the deployed contracts with a live, multi-party demo.

Builds two listings (seller = accounts[2]) and a spread of orders
(buyer = accounts[1]) that show every stage of the hand-in-hand flow:

    PAID -> DELIVERED -> USED -> registered -> PENDING -> LIVE / DENIED

State-aware and idempotent: each run reads the current on-chain state and
only performs the missing steps, so it can be re-run after any interruption.
Retries transparently on StudioNet's occasional TLS resets.

Usage:
    .venv\\Scripts\\python.exe scripts\\seed.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "frontend", "config.json")

AUTHENTIC_TEXT = (
    "I have used these headphones on my train commute every day for the "
    "past two weeks. The battery easily covers the full round trip and the "
    "bass stays clean even at high volume. The ear cups stay comfortable "
    "after three hours of wear, which was my main concern before buying."
)
SPAM_TEXT = (
    "AMAZING product!!! Best purchase ever!!! Everyone must buy this right "
    "now and grab the super-deal bonus through our promo link. Five stars, "
    "best price guaranteed, limited time offer, buy now buy now buy now!!!"
)

AURORA = "aurora-headphones"
TERRA = "terra-espresso"

RANK = {"PAID": 0, "DELIVERED": 1, "USED": 2, "REGISTERED": 3, "SUBMITTED": 4, "VERIFIED": 5}


def _transient(exc: Exception) -> bool:
    msg = repr(exc).lower()
    return "10054" in msg or "connection" in msg or "reset" in msg or "timed out" in msg


def main() -> int:
    print("== Seeding the marketplace + verifier ==\n")
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    print(f"verifier:   {cfg['verifier']}")
    print(f"marketplace:{cfg['marketplace']}\n")

    from gltest_cli.config.user import load_user_config, user_config_exists
    from gltest_cli.config.general import get_general_config

    if not user_config_exists():
        print("FAIL: gltest.config.yaml not found")
        return 1
    get_general_config().user_config = load_user_config("gltest.config.yaml")

    from gltest import get_contract_factory
    from gltest.accounts import get_accounts
    from gltest.assertions import tx_execution_failed

    accounts = get_accounts()
    deployer, buyer, seller = accounts[0], accounts[1], accounts[2]
    buyer_addr = str(buyer.address).lower()
    seller_addr = str(seller.address).lower()
    print(f"deployer: {str(deployer.address).lower()}")
    print(f"buyer:    {buyer_addr}")
    print(f"seller:   {seller_addr}\n")

    mfactory = get_contract_factory(
        contract_file_path=os.path.join(ROOT, "contracts", "demo_marketplace.py")
    )
    vfactory = get_contract_factory(
        contract_file_path=os.path.join(ROOT, "contracts", "review_verifier.py")
    )

    market_op = mfactory.build_contract(contract_address=cfg["marketplace"], account=deployer)
    market_buyer = mfactory.build_contract(contract_address=cfg["marketplace"], account=buyer)
    market_seller = mfactory.build_contract(contract_address=cfg["marketplace"], account=seller)
    verifier_op = vfactory.build_contract(contract_address=cfg["verifier"], account=deployer)
    verifier_buyer = vfactory.build_contract(contract_address=cfg["verifier"], account=buyer)

    def run(label, call, tries=4) -> bool:
        for attempt in range(tries):
            try:
                receipt = call.transact()
                ok = not tx_execution_failed(receipt)
                print(f"  [{label}] {'ok' if ok else 'FAILED'}")
                return ok
            except Exception as exc:
                if _transient(exc) and attempt < tries - 1:
                    print(f"  [{label}] transient network error, retry {attempt + 1}/{tries}")
                    time.sleep(2 * (attempt + 1))
                    continue
                print(f"  [{label}] error: {repr(exc)[:150]}")
                return False
        return False

    def read(label, call, default=None, tries=4):
        for attempt in range(tries):
            try:
                return call.call()
            except Exception as exc:
                if _transient(exc) and attempt < tries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                return default
        return default

    def ensure_product(pid: str, title: str, price: int) -> None:
        if read(f"product {pid}", market_op.get_product(args=[pid])):
            print(f"[product {pid}] already listed, skipping")
            return
        print(f"[product {pid}] listing ...")
        run(f"list {pid}", market_seller.list_item(args=[pid, title, price]))

    def ensure_order(oid: str, pid: str, upto: str, text: str = AUTHENTIC_TEXT) -> None:
        target = RANK[upto]
        rec = read(f"{oid} state", market_op.get_purchase(args=[oid]))
        m_state = {"NONE": -1, "PAID": 0, "DELIVERED": 1, "USED": 2}[rec["state"] if rec else "NONE"]
        print(f"[order {oid}] -> {upto}")

        if m_state < 0:
            run(f"{oid} buy", market_buyer.buy(args=[pid, oid]))
            m_state = 0
        if target >= RANK["DELIVERED"] and m_state < RANK["DELIVERED"]:
            run(f"{oid} deliver", market_seller.mark_delivered(args=[oid]))
            m_state = 1
        if target >= RANK["USED"] and m_state < RANK["USED"]:
            run(f"{oid} use", market_buyer.mark_used(args=[oid]))

        if target >= RANK["REGISTERED"]:
            rrec = read(f"{oid} review", verifier_op.get_review(args=[oid]))
            if not rrec:
                # self-service: the buyer registers their own purchase; the
                # verifier checks it against the marketplace record
                run(f"{oid} register", verifier_buyer.register_purchase(args=[oid]))
                rrec = read(f"{oid} review", verifier_op.get_review(args=[oid]))
            if target >= RANK["SUBMITTED"] and rrec and rrec.get("rating", 0) == 0:
                run(f"{oid} submit", verifier_buyer.submit_review(args=[oid, 5, text]))
                rrec = read(f"{oid} review", verifier_op.get_review(args=[oid]))
            if target >= RANK["VERIFIED"] and rrec and rrec.get("rating", 0) > 0 and rrec.get("status") == "PENDING":
                run(f"{oid} verify (AI, ~1-2 min)", verifier_op.verify_review(args=[oid]))

    ensure_product(AURORA, "Aurora Wireless Headphones", 129)
    ensure_product(TERRA, "Terra Espresso Machine", 249)
    print()

    ensure_order("ord-live-1", AURORA, "VERIFIED", AUTHENTIC_TEXT)
    ensure_order("ord-denied-1", AURORA, "VERIFIED", SPAM_TEXT)
    ensure_order("ord-pending-1", AURORA, "SUBMITTED", AUTHENTIC_TEXT)
    ensure_order("ord-used-1", AURORA, "REGISTERED")
    ensure_order("ord-used-2", TERRA, "USED")
    ensure_order("ord-shipped-1", TERRA, "DELIVERED")
    ensure_order("ord-new-1", AURORA, "PAID")

    print()
    stats = read("stats", verifier_op.stats())
    score = read("score", verifier_op.product_score(args=[AURORA]))
    print("stats:      ", json.dumps(stats))
    print("aurora score:", json.dumps(score))
    print("\nSeed done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

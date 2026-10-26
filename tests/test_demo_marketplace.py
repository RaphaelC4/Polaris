"""DemoMarketplace live tests on StudioNet.

Multi-account scenarios (seller != buyer) need the second funded account
(alice); they skip gracefully until the Studio faucet tops her up. All
single-account rules are covered with the operator account.
"""
import uuid

import pytest

from conftest import (
    E2E_PRODUCT,
    assert_ok,
    hex_addr,
    new_purchase_id,
)
from gltest.assertions import tx_execution_failed


@pytest.fixture(scope="session")
def e2e_listing(marketplace, operator):
    """One operator-owned listing reused by single-account tests."""
    product_id = f"{E2E_PRODUCT}-{uuid.uuid4().hex[:6]}"
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[product_id, "Wireless Headphones Pro", 129])
        .transact()
    )
    assert_ok(receipt, "list_item (e2e_listing)")
    return product_id


def test_list_and_get_product(marketplace, operator, e2e_listing):
    product = marketplace.get_product(args=[e2e_listing]).call()
    assert product["product_id"] == e2e_listing
    assert product["title"] == "Wireless Headphones Pro"
    assert product["price"] == 129
    assert product["seller"] == hex_addr(operator)


def test_get_purchase_safe_is_non_reverting(marketplace):
    """The verifier's cross-contract read: unknown id -> {}, never a revert."""
    assert marketplace.get_purchase_safe(args=[new_purchase_id("nope")]).call() == {}


def test_get_purchase_safe_matches_get_purchase(marketplace, buyer, e2e_listing):
    """buyer (alice) buys the operator's listing; both views agree."""
    purchase_id = new_purchase_id("safe")
    receipt = marketplace.connect(buyer).buy(args=[e2e_listing, purchase_id]).transact()
    assert_ok(receipt, "buy (safe view)")
    assert (
        marketplace.get_purchase_safe(args=[purchase_id]).call()
        == marketplace.get_purchase(args=[purchase_id]).call()
    )


def test_list_rejects_duplicate(marketplace, operator, e2e_listing):
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[e2e_listing, "dupe", 1])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_list_rejects_negative_price(marketplace, operator):
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[new_purchase_id("neg"), "bad", -5])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_seller_cannot_buy_own_product(marketplace, operator, e2e_listing):
    receipt = (
        marketplace.connect(operator)
        .buy(args=[e2e_listing, new_purchase_id()])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_buy_unknown_product(marketplace, operator):
    receipt = (
        marketplace.connect(operator)
        .buy(args=["no-such-product", new_purchase_id()])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_full_lifecycle_with_two_accounts(marketplace, operator, alice_ready):
    """alice buys from operator; operator delivers; alice confirms used."""
    product_id = f"{E2E_PRODUCT}-2acct-{uuid.uuid4().hex[:6]}"
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[product_id, "USB-C Cable 2m", 12])
        .transact()
    )
    assert_ok(receipt, "list_item (two-account)")

    purchase_id = new_purchase_id()
    receipt = (
        marketplace.connect(alice_ready)
        .buy(args=[product_id, purchase_id])
        .transact()
    )
    assert_ok(receipt, "buy (alice)")

    purchase = marketplace.get_purchase(args=[purchase_id]).call()
    assert purchase["state"] == "PAID"
    assert purchase["buyer"] == hex_addr(alice_ready)
    assert purchase["seller"] == hex_addr(operator)

    receipt = (
        marketplace.connect(operator)
        .mark_delivered(args=[purchase_id])
        .transact()
    )
    assert_ok(receipt, "mark_delivered")

    receipt = (
        marketplace.connect(alice_ready)
        .mark_used(args=[purchase_id])
        .transact()
    )
    assert_ok(receipt, "mark_used")
    assert marketplace.get_purchase(args=[purchase_id]).call()["state"] == "USED"


def test_lifecycle_state_rules(marketplace, operator, alice_ready):
    """mark_used directly on PAID reverts; wrong-role transitions revert."""
    product_id = f"{E2E_PRODUCT}-rules-{uuid.uuid4().hex[:6]}"
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[product_id, "Phone Case", 9])
        .transact()
    )
    assert_ok(receipt, "list_item (rules)")

    purchase_id = new_purchase_id()
    receipt = (
        marketplace.connect(alice_ready)
        .buy(args=[product_id, purchase_id])
        .transact()
    )
    assert_ok(receipt, "buy (rules)")

    # PAID -> USED is illegal
    receipt = (
        marketplace.connect(alice_ready)
        .mark_used(args=[purchase_id])
        .transact()
    )
    assert tx_execution_failed(receipt)

    # buyer cannot mark delivery
    receipt = (
        marketplace.connect(alice_ready)
        .mark_delivered(args=[purchase_id])
        .transact()
    )
    assert tx_execution_failed(receipt)

    # seller marks delivered; then alice (buyer) cannot re-deliver
    receipt = (
        marketplace.connect(operator)
        .mark_delivered(args=[purchase_id])
        .transact()
    )
    assert_ok(receipt, "mark_delivered (rules)")
    receipt = (
        marketplace.connect(operator)
        .mark_delivered(args=[purchase_id])
        .transact()
    )
    assert tx_execution_failed(receipt)

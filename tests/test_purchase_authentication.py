"""Purchase-authentication attack tests (live StudioNet, real consensus).

The verifier must only register a purchase that (a) exists in the bound
DemoMarketplace, (b) belongs to the transaction sender, and (c) has
reached DELIVERED or USED. Product and seller come from the marketplace.

Covered, per the review of register_purchase():
  - nonexistent purchases are rejected (fabricated purchase ids)
  - an attacker cannot register - or squat on - another buyer's purchase
    id, and the legitimate buyer can still register afterwards
  - orders in the wrong marketplace state (PAID) are rejected, and become
    registrable once delivered
  - fabricated purchases cannot be used to spam repeated reviews, even
    with the cooldown applied to every review
  - a rejected attempt leaves no record behind
"""
from conftest import (
    AUTHENTIC_TEXT,
    SHARED_PRODUCT,
    STATUS_PENDING,
    hex_addr,
    new_purchase_id,
    receipt_error,
)
from gltest.assertions import tx_execution_failed


def _no_record(verifier, purchase_id):
    assert verifier.get_review_safe(args=[purchase_id]).call() == {}


# --------------------------------------------------- nonexistent purchases
def test_register_rejects_nonexistent_purchase(verifier, register_purchase, buyer):
    """A wallet cannot invent a purchase id the marketplace never saw."""
    ghost = new_purchase_id("ghost")
    receipt = register_purchase(buyer, ghost, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "not found in marketplace" in receipt_error(receipt)
    _no_record(verifier, ghost)


def test_fabricated_purchase_cannot_be_reviewed(verifier, buyer, submit_review):
    """No registration -> no review: the Tier-1 gate refuses it."""
    ghost = new_purchase_id("ghost")
    receipt = submit_review(buyer, ghost, 5, AUTHENTIC_TEXT, expect_ok=False)
    assert tx_execution_failed(receipt)
    _no_record(verifier, ghost)


# ----------------------------------------------- attacker-first registration
def test_attacker_cannot_register_or_squat_buyers_purchase(
    verifier, make_order, register_purchase, buyer, operator
):
    """A non-buyer wallet (here the seller) races to register the buyer's
    visible purchase id. It must fail, leave nothing reserved, and the
    real buyer must still be able to register and review."""
    purchase_id = make_order("DELIVERED")

    receipt = register_purchase(operator, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "only the buyer may register" in receipt_error(receipt)
    _no_record(verifier, purchase_id)  # nothing was reserved

    # legitimate registration after the rejected fraudulent attempt
    register_purchase(buyer, purchase_id)
    record = verifier.get_review(args=[purchase_id]).call()
    assert record["buyer"] == hex_addr(buyer)
    assert record["status"] == STATUS_PENDING


def test_outside_attacker_cannot_squat_buyers_purchase(
    verifier, make_order, register_purchase, buyer, mallory
):
    """Same attack from a wallet unrelated to the order (third account)."""
    purchase_id = make_order("DELIVERED")

    receipt = register_purchase(mallory, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "only the buyer may register" in receipt_error(receipt)
    _no_record(verifier, purchase_id)

    register_purchase(buyer, purchase_id)
    assert verifier.get_review(args=[purchase_id]).call()["buyer"] == hex_addr(buyer)


def test_buyer_cannot_register_someone_elses_order(
    verifier, make_order, register_purchase, mallory
):
    """Even a funded outsider who owns NO order cannot claim another's."""
    purchase_id = make_order("USED")
    receipt = register_purchase(mallory, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    _no_record(verifier, purchase_id)


# ------------------------------------------------------ invalid order state
def test_register_rejects_undelivered_order(
    verifier, marketplace, make_order, register_purchase, buyer, operator
):
    """A PAID order is not review-eligible; once delivered it is."""
    purchase_id = make_order("PAID")

    receipt = register_purchase(buyer, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "has not been delivered" in receipt_error(receipt)
    _no_record(verifier, purchase_id)

    marketplace.connect(operator).mark_delivered(args=[purchase_id]).transact()
    register_purchase(buyer, purchase_id)  # now accepted
    assert verifier.get_review(args=[purchase_id]).call()["status"] == STATUS_PENDING


def test_register_accepts_used_order(verifier, make_order, register_purchase, buyer):
    purchase_id = make_order("USED")
    register_purchase(buyer, purchase_id)
    assert verifier.is_review_allowed(args=[purchase_id]).call() is True


# ----------------------------------------- provenance of product and seller
def test_product_and_seller_come_from_marketplace(
    verifier, marketplace, make_order, register_purchase, buyer, operator
):
    purchase_id = make_order("DELIVERED")
    register_purchase(buyer, purchase_id)

    order = marketplace.get_purchase(args=[purchase_id]).call()
    record = verifier.get_review(args=[purchase_id]).call()
    assert record["product_id"] == order["product_id"] == SHARED_PRODUCT
    assert record["seller"] == order["seller"] == hex_addr(operator)
    assert record["buyer"] == order["buyer"] == hex_addr(buyer)


def test_register_no_longer_accepts_caller_asserted_fields(verifier, buyer):
    """The old 4-argument form (buyer/product/seller supplied by the
    caller) is gone; calling it must not register anything."""
    purchase_id = new_purchase_id("legacy")
    try:
        receipt = (
            verifier.connect(buyer)
            .register_purchase(
                args=[purchase_id, hex_addr(buyer), SHARED_PRODUCT, "0x" + "99" * 20]
            )
            .transact()
        )
        assert tx_execution_failed(receipt)
    except Exception:
        pass  # rejected before submission: also acceptable
    _no_record(verifier, purchase_id)


# ------------------------------------- repeated submissions on one product
def test_cannot_farm_reviews_with_fabricated_purchases(
    cooldown_verifier, make_order, register_purchase, submit_review, buyer
):
    """Fabricated ids cannot be registered, so the only way to review is a
    real order; with the cooldown on every review, a second real review is
    rate-limited even for the same product."""
    ghost = new_purchase_id("ghost")
    receipt = register_purchase(buyer, ghost, expect_ok=False, contract=cooldown_verifier)
    assert tx_execution_failed(receipt)

    real = make_order("DELIVERED")
    register_purchase(buyer, real, contract=cooldown_verifier)
    submit_review(buyer, real, 5, AUTHENTIC_TEXT, contract=cooldown_verifier)

    again = make_order("DELIVERED")
    register_purchase(buyer, again, contract=cooldown_verifier)
    receipt = submit_review(
        buyer, again, 5, AUTHENTIC_TEXT, expect_ok=False, contract=cooldown_verifier
    )
    assert tx_execution_failed(receipt)
    assert "cooldown" in receipt_error(receipt).lower()

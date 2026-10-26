"""End-to-end journeys on StudioNet (real consensus, real AI, no mocks).

Two journeys, both over REAL marketplace orders (seller = operator,
buyer = alice) that the verifier authenticates at registration:

1. ``test_review_journey_delivered_order`` - buy -> deliver -> register
   (authenticated against the marketplace) -> Tier-1 submission -> Tier-2
   AI verdict through the Equivalence Principle -> aggregates.

2. ``test_marketplace_journey_full_lifecycle`` - the full story including
   ``mark_used`` and a fraudulent registration attempt that is rejected
   before the real buyer registers and reviews.

Both skip until alice has StudioNet gas.
"""
import pytest

from conftest import (
    AUTHENTIC_TEXT,
    SHARED_PRODUCT,
    STATUS_LIVE,
    STATUS_MANUAL,
    assert_ok,
    hex_addr,
    receipt_error,
)
from gltest.assertions import tx_execution_failed


def test_review_journey_delivered_order(
    verifier, make_order, register_purchase, submit_review, buyer
):
    """buy -> deliver -> register -> submit -> real AI verdict -> aggregates."""
    purchase_id = make_order("DELIVERED")
    register_purchase(buyer, purchase_id)
    submit_review(buyer, purchase_id, 5, AUTHENTIC_TEXT)

    receipt = verifier.verify_review(args=[purchase_id]).transact()
    assert_ok(receipt, f"verify_review({purchase_id})")

    record = verifier.get_review(args=[purchase_id]).call()
    print(
        f"\n[e2e] verdict={record['status']} "
        f"conf={record.get('verdict_confidence')} "
        f"reason={record.get('verdict_reason')}"
    )
    # Authentic text: the AI either decides (LIVE) or hands off to a human
    # (MANUAL_CHECK). A confident DENIED would mean the model disagreed
    # with the premise of the fixture text.
    assert record["status"] in (STATUS_LIVE, STATUS_MANUAL), record
    assert record["rating"] == 5
    confidence = record.get("verdict_confidence", 0)
    assert isinstance(confidence, int) and 0 <= confidence <= 100
    assert record["verdict_reason"].startswith("ai:")

    # Aggregates stay internally consistent after the verdict.
    stats = verifier.stats().call()
    score = verifier.product_score(args=[SHARED_PRODUCT]).call()
    assert stats["total_reviews"] >= 1
    assert score["live_reviews"] <= stats["live"]
    if score["live_reviews"]:
        assert 100 <= score["average_rating_x100"] <= 500
    else:
        assert score["average_rating_x100"] == 0

    assert verifier.is_review_allowed(args=[purchase_id]).call() is False


def test_marketplace_journey_full_lifecycle(
    verifier,
    marketplace,
    operator,
    buyer,
    shared_listing,
    register_purchase,
    submit_review,
):
    """list -> buy -> (fraud attempt) -> deliver -> use -> register -> review."""
    from conftest import new_purchase_id

    purchase_id = new_purchase_id("journey")
    assert_ok(
        marketplace.connect(buyer).buy(args=[shared_listing, purchase_id]).transact(),
        "buy (alice is buyer)",
    )

    # Not delivered yet: registration must be refused.
    receipt = register_purchase(buyer, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "has not been delivered" in receipt_error(receipt)

    assert_ok(
        marketplace.connect(operator).mark_delivered(args=[purchase_id]).transact(),
        "mark_delivered (operator is seller)",
    )

    # The seller trying to register the buyer's order is refused too.
    receipt = register_purchase(operator, purchase_id, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "only the buyer may register" in receipt_error(receipt)

    assert_ok(
        marketplace.connect(buyer).mark_used(args=[purchase_id]).transact(),
        "mark_used (alice is buyer)",
    )

    purchase = marketplace.get_purchase(args=[purchase_id]).call()
    assert purchase["state"] == "USED"
    assert purchase["buyer"] == hex_addr(buyer)
    assert purchase["seller"] == hex_addr(operator)

    # Only now does the marketplace-authenticated buyer registration work.
    register_purchase(buyer, purchase_id)
    assert verifier.is_review_allowed(args=[purchase_id]).call() is True

    submit_review(buyer, purchase_id, 5, AUTHENTIC_TEXT)
    record = verifier.get_review(args=[purchase_id]).call()
    assert record["status"] == "PENDING"
    assert record["buyer"] == hex_addr(buyer)
    assert record["seller"] == hex_addr(operator)

    product_reviews = verifier.get_product_reviews(args=[SHARED_PRODUCT]).call()
    assert purchase_id in [r["purchase_id"] for r in product_reviews]

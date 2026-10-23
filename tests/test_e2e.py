"""End-to-end journeys on StudioNet (real consensus, real AI, no mocks).

Two journeys:

1. ``test_review_journey_single_account`` - the review-verifier story:
   register a delivered purchase -> Tier-1 submission -> Tier-2 AI verdict
   through the Equivalence Principle -> aggregate score/stats reflect it.

2. ``test_marketplace_journey_two_accounts`` - the full marketplace story:
   alice lists a product, the operator buys it, alice delivers, the
   operator confirms usage, registers the purchase themself (the buyer
   is the registrar - self-service) and reviews it. Skips until alice
   has StudioNet gas.

Cooldown note: every operator submission in this file lands on
``SHARED_PRODUCT`` - the same fixed product ``test_review_verifier`` uses -
so this file (which runs before it, alphabetically) never trips the
per-buyer cross-product cooldown rule. The marketplace listing uses
``SHARED_PRODUCT`` too so the journey stays internally consistent; each
session deploys fresh contracts, so the listing never collides.
"""
import pytest

from conftest import (
    AUTHENTIC_TEXT,
    PHANTOM_SELLER,
    SHARED_PRODUCT,
    STATUS_LIVE,
    STATUS_MANUAL,
    assert_ok,
    hex_addr,
    new_purchase_id,
)


def test_review_journey_single_account(
    verifier, register_purchase, submit_review, operator
):
    """register -> submit -> real AI verdict -> aggregates."""
    purchase_id = new_purchase_id("journey")
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    submit_review(operator, purchase_id, 5, AUTHENTIC_TEXT)

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


def test_marketplace_journey_two_accounts(
    verifier,
    marketplace,
    operator,
    alice_ready,
    register_purchase,
    submit_review,
):
    """list -> buy -> deliver -> use -> register -> buyer review."""
    receipt = (
        marketplace.connect(alice_ready)
        .list_item(args=[SHARED_PRODUCT, "Wireless Headphones Pro", 129])
        .transact()
    )
    assert_ok(receipt, "list_item (journey, alice is seller)")

    purchase_id = new_purchase_id("journey")
    receipt = (
        marketplace.connect(operator)
        .buy(args=[SHARED_PRODUCT, purchase_id])
        .transact()
    )
    assert_ok(receipt, "buy (operator is buyer)")

    receipt = (
        marketplace.connect(alice_ready)
        .mark_delivered(args=[purchase_id])
        .transact()
    )
    assert_ok(receipt, "mark_delivered (alice is seller)")

    receipt = (
        marketplace.connect(operator).mark_used(args=[purchase_id]).transact()
    )
    assert_ok(receipt, "mark_used (operator is buyer)")

    purchase = marketplace.get_purchase(args=[purchase_id]).call()
    assert purchase["state"] == "USED"
    assert purchase["buyer"] == hex_addr(operator)
    assert purchase["seller"] == hex_addr(alice_ready)

    # Buyer self-service wiring: the USED order's buyer registers their
    # own purchase as review-eligible.
    register_purchase(operator, purchase_id, SHARED_PRODUCT, hex_addr(alice_ready))
    assert verifier.is_review_allowed(args=[purchase_id]).call() is True

    submit_review(operator, purchase_id, 5, AUTHENTIC_TEXT)
    record = verifier.get_review(args=[purchase_id]).call()
    assert record["status"] == "PENDING"
    assert record["buyer"] == hex_addr(operator)
    assert record["seller"] == hex_addr(alice_ready)

    product_reviews = verifier.get_product_reviews(args=[SHARED_PRODUCT]).call()
    assert purchase_id in [r["purchase_id"] for r in product_reviews]

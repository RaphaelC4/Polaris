"""ReviewVerifier live tests on StudioNet (real consensus + real AI).

Tier-1 tests are deterministic consensus checks (revert or succeed).
Tier-2 tests run genuine StudioNet AI validators through the Equivalence
Principle, so assertions accept the human-review outcome (MANUAL_CHECK)
whenever the model confidence is low, and record every verdict reason.

Cooldown safety: all operator submissions land on the fixed SHARED_PRODUCT
(same-product submissions are exempt from the per-buyer cooldown); the
cross-product cooldown rule gets its own dedicated test.
"""
import pytest

from conftest import (
    AUTHENTIC_TEXT,
    FAKE_TEXT,
    PHANTOM_SELLER,
    SHARED_PRODUCT,
    STATUS_DENIED,
    STATUS_LIVE,
    STATUS_MANUAL,
    STATUS_PENDING,
    assert_ok,
    hex_addr,
    new_purchase_id,
    receipt_error,
)
from gltest.assertions import tx_execution_failed


# ------------------------------------------------------- Tier 1: registration
def test_register_and_view_roundtrip(verifier, register_purchase, operator):
    purchase_id = new_purchase_id()
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)

    record = verifier.get_review(args=[purchase_id]).call()
    assert record["purchase_id"] == purchase_id
    assert record["product_id"] == SHARED_PRODUCT
    assert record["buyer"] == hex_addr(operator)
    assert record["seller"] == PHANTOM_SELLER
    assert record["rating"] == 0
    assert record["status"] == STATUS_PENDING
    assert record["appealed"] is False
    assert verifier.is_review_allowed(args=[purchase_id]).call() is True


def test_register_rejects_buyer_equals_seller(verifier, operator):
    conn = verifier.connect(operator)
    receipt = conn.register_purchase(
        args=[
            new_purchase_id(),
            hex_addr(operator),
            SHARED_PRODUCT,
            hex_addr(operator),
        ]
    ).transact()
    assert tx_execution_failed(receipt)


def test_register_rejects_non_hex_seller(verifier, operator):
    receipt = (
        verifier.connect(operator)
        .register_purchase(
            args=[new_purchase_id(), hex_addr(operator), SHARED_PRODUCT, "not-a-hex-address"]
        )
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_register_rejects_duplicate_purchase_id(verifier, operator):
    purchase_id = new_purchase_id()
    args = [purchase_id, hex_addr(operator), SHARED_PRODUCT, PHANTOM_SELLER]
    assert_ok(verifier.register_purchase(args=args).transact(), "first register")
    receipt = verifier.register_purchase(args=args).transact()
    assert tx_execution_failed(receipt)


def test_register_rejects_mismatched_buyer(verifier, operator, alice_ready):
    """Self-service registration: the sender must BE the declared buyer."""
    receipt = (
        verifier.connect(alice_ready)
        .register_purchase(
            args=[new_purchase_id(), hex_addr(operator), SHARED_PRODUCT, PHANTOM_SELLER]
        )
        .transact()
    )
    assert tx_execution_failed(receipt)
    assert "buyer may register" in receipt_error(receipt)


# --------------------------------------------------------- Tier 1: submission
def test_submit_requires_registered_purchase(verifier, operator):
    receipt = (
        verifier.connect(operator)
        .submit_review(args=[new_purchase_id("ghost"), 5, AUTHENTIC_TEXT])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_submit_only_buyer_may_review(verifier, register_purchase, operator, alice_ready):
    """A purchase registered by alice cannot be reviewed by anyone else."""
    purchase_id = new_purchase_id()
    register_purchase(alice_ready, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    receipt = (
        verifier.connect(operator)
        .submit_review(args=[purchase_id, 5, AUTHENTIC_TEXT])
        .transact()
    )
    assert tx_execution_failed(receipt)


def test_submit_rating_and_text_bounds(verifier, register_purchase, operator):
    purchase_id = new_purchase_id()
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    conn = verifier.connect(operator)
    for rating, text in ((0, AUTHENTIC_TEXT), (6, AUTHENTIC_TEXT), (5, "too short")):
        receipt = conn.submit_review(args=[purchase_id, rating, text]).transact()
        assert tx_execution_failed(receipt), f"expected revert for rating={rating}"


def test_duplicate_review_rejected(verifier, register_purchase, submit_review, operator):
    purchase_id = new_purchase_id()
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    submit_review(operator, purchase_id, 5, AUTHENTIC_TEXT)
    receipt = submit_review(
        operator, purchase_id, 4, AUTHENTIC_TEXT, expect_ok=False
    )
    assert tx_execution_failed(receipt)


# ------------------------------------------------- Tier 2: real AI verdicts
@pytest.fixture(scope="session")
def authentic_review(verifier, register_purchase, submit_review, operator):
    """Submit + verify one authentic review (real AI), once per session."""
    purchase_id = new_purchase_id("auth")
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    submit_review(operator, purchase_id, 5, AUTHENTIC_TEXT)
    receipt = verifier.verify_review(args=[purchase_id]).transact()
    assert_ok(receipt, f"verify_review({purchase_id})")
    record = verifier.get_review(args=[purchase_id]).call()
    print(f"\n[ai] authentic-text verdict: {record['status']} "
          f"conf={record.get('verdict_confidence')} "
          f"reason={record.get('verdict_reason')}")
    return purchase_id


@pytest.fixture(scope="session")
def fake_review(verifier, register_purchase, submit_review, operator):
    """Submit + verify one spam review (real AI), once per session."""
    purchase_id = new_purchase_id("fake")
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    submit_review(operator, purchase_id, 5, FAKE_TEXT)
    receipt = verifier.verify_review(args=[purchase_id]).transact()
    assert_ok(receipt, f"verify_review({purchase_id})")
    record = verifier.get_review(args=[purchase_id]).call()
    print(f"\n[ai] fake-text verdict: {record['status']} "
          f"conf={record.get('verdict_confidence')} "
          f"reason={record.get('verdict_reason')}")
    return purchase_id


def test_authentic_review_verdict(verifier, authentic_review):
    record = verifier.get_review(args=[authentic_review]).call()
    assert record["status"] in (STATUS_LIVE, STATUS_MANUAL), record
    assert record["rating"] == 5
    confidence = record.get("verdict_confidence", 0)
    assert isinstance(confidence, int) and 0 <= confidence <= 100
    assert record["verdict_reason"].startswith("ai:")


def test_fake_review_verdict(verifier, fake_review):
    record = verifier.get_review(args=[fake_review]).call()
    assert record["status"] in (STATUS_DENIED, STATUS_MANUAL), record
    confidence = record.get("verdict_confidence", 0)
    assert isinstance(confidence, int) and 0 <= confidence <= 100
    assert record["verdict_reason"].startswith("ai:")


# ------------------------------------------------------------------ governance
def test_appeal_sends_finished_review_back_to_pending(
    verifier, authentic_review, operator
):
    """Buyer appeal from any final verdict (LIVE/DENIED/MANUAL_CHECK) -> PENDING.

    The appeal is a governance re-roll for the rotated validator set, not
    a human override. It is allowed exactly once per review.
    """
    conn = verifier.connect(operator)

    record = verifier.get_review(args=[authentic_review]).call()
    assert record["status"] in (STATUS_LIVE, STATUS_DENIED, STATUS_MANUAL), record
    assert record["appealed"] is False

    receipt = conn.appeal_review(
        args=[authentic_review, "I believe this review is genuine"]
    ).transact()
    assert_ok(receipt, "appeal_review")

    record = verifier.get_review(args=[authentic_review]).call()
    assert record["status"] == STATUS_PENDING, record
    assert record["appealed"] is True
    assert record["verdict_reason"].startswith("appeal:")

    # A review can be appealed only once; the second attempt reverts
    # (a PENDING review is not a finished review).
    receipt = conn.appeal_review(
        args=[authentic_review, "trying again after the AI round"]
    ).transact()
    assert tx_execution_failed(receipt)
    assert "can be appealed" in receipt_error(receipt)


def test_appeal_only_the_buyer(verifier, fake_review, alice_ready):
    """Only the reviewer (buyer) may appeal a review."""
    receipt = (
        verifier.connect(alice_ready)
        .appeal_review(args=[fake_review, "not my review"])
        .transact()
    )
    assert tx_execution_failed(receipt)
    assert "may appeal" in receipt_error(receipt)

    record = verifier.get_review(args=[fake_review]).call()
    assert record["status"] in (STATUS_LIVE, STATUS_DENIED, STATUS_MANUAL), record
    assert record["appealed"] is False


# ----------------------------------------------------------------- aggregates
def test_product_score_counts_live_only(verifier, authentic_review):
    score = verifier.product_score(args=[SHARED_PRODUCT]).call()
    assert score["product_id"] == SHARED_PRODUCT
    assert isinstance(score["live_reviews"], int)
    assert isinstance(score["average_rating_x100"], int)
    if score["live_reviews"] > 0:
        assert 100 <= score["average_rating_x100"] <= 500
    else:
        assert score["average_rating_x100"] == 0


def test_stats_view(verifier):
    stats = verifier.stats().call()
    for key in ("total_reviews", "live", "denied", "manual_check", "pending"):
        assert key in stats and isinstance(stats[key], int)
    assert stats["total_reviews"] >= (
        stats["live"] + stats["denied"] + stats["manual_check"] + stats["pending"]
    ) - 0


# --------------------------------------------------------------- cooldown rule
def test_cooldown_blocks_cross_product_review(
    verifier, register_purchase, submit_review, operator
):
    """Second review by the same buyer on a DIFFERENT product within 1h reverts.

    The first submission lands on SHARED_PRODUCT (exempt from its own
    cooldown); the immediate cross-product attempt must revert.
    """
    purchase_id = new_purchase_id()
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    submit_review(operator, purchase_id, 4, AUTHENTIC_TEXT)

    other_id = new_purchase_id()
    register_purchase(operator, other_id, new_purchase_id("prod"), PHANTOM_SELLER)
    receipt = submit_review(operator, other_id, 4, AUTHENTIC_TEXT, expect_ok=False)
    assert tx_execution_failed(receipt)
    assert "cooldown" in receipt_error(receipt).lower()


def test_is_review_allowed_transitions(verifier, register_purchase, submit_review, operator):
    purchase_id = new_purchase_id()
    register_purchase(operator, purchase_id, SHARED_PRODUCT, PHANTOM_SELLER)
    assert verifier.is_review_allowed(args=[purchase_id]).call() is True
    submit_review(operator, purchase_id, 3, AUTHENTIC_TEXT)
    assert verifier.is_review_allowed(args=[purchase_id]).call() is False

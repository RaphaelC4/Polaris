"""Shared fixtures for the live StudioNet test suite.

Everything runs against GenLayer StudioNet (chain 61999) through gltest's
network stack: real consensus, real AI validators, no mocks, no fallbacks.

gltest API (verified against the installed gltest 0.29.2 sources):
    reads:   contract.method(args=[...]).call()
    writes:  contract.method(args=[...]).transact(...)  -> receipt dict
    success: gltest.assertions.tx_execution_succeeded(receipt)
    sender:  contract.connect(account)
"""
import os
import sys
import uuid

# Windows consoles default to cp1252: AI verdict_reason text can contain
# characters (e.g. U+2011 non-breaking hyphen) that crash any print() or
# pytest assertion rendering. Force UTF-8 with replace-on-error so live
# output never kills a test run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import pytest

from gltest.fixtures import gl_client, default_account, accounts  # noqa: F401
from gltest.assertions import tx_execution_failed
from gltest.accounts import get_accounts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONTRACTS_DIR = os.path.join(ROOT, "contracts")
REVIEW_VERIFIER_PATH = os.path.join(CONTRACTS_DIR, "review_verifier.py")
MARKETPLACE_PATH = os.path.join(CONTRACTS_DIR, "demo_marketplace.py")

# Cooldown design: the per-buyer cooldown only applies across DIFFERENT
# products, so every buyer submission lands on ONE fixed product id and
# repeated runs never trip the 1h cooldown. The cooldown rule itself gets a
# dedicated cross-product test. Same for alice: her submission product is
# fixed too.
SHARED_PRODUCT = "prod-verify-shared"
E2E_PRODUCT = "prod-e2e"

STATUS_PENDING = "PENDING"
STATUS_LIVE = "LIVE"
STATUS_DENIED = "DENIED"
STATUS_MANUAL = "MANUAL_CHECK"

ALICE_ADDRESS = "0x2b13bcb1281295aa7cb77326c02bee7768f78ab0"

# Real-StudioNet review texts: written so a competent model separates them
# (specific, experience-based details vs promotional spam).
AUTHENTIC_TEXT = (
    "I have used these headphones on my train commute every day for the past "
    "two weeks. The battery easily covers the full round trip and the bass "
    "stays clean even at high volume. The ear cups stay comfortable after "
    "three hours of continuous wear, and the case feels sturdy."
)
FAKE_TEXT = (
    "AMAZING product!!! Best purchase ever!!! Everyone must buy this right "
    "now and grab the super-deal bonus through our promo link. Five stars, "
    "best price guaranteed, limited time offer, buy now buy now buy now!!!"
)

PHANTOM_SELLER = "0x9999999999999999999999999999999999999999"


def hex_addr(account) -> str:
    """Lowercase hex form of a gltest LocalAccount (or of a hex string).

    Tolerating strings matters because most call sites pass
    ``hex_addr(...)`` results INTO helpers that hex them again.
    """
    if isinstance(account, str):
        return account.lower()
    return str(account.address).lower()


def new_purchase_id(prefix: str = "pur") -> str:
    """Colliding purchase ids revert, so every test uses a fresh one."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def receipt_error(receipt) -> str:
    """Best-effort extraction of the revert/stderr text from a receipt."""
    try:
        leader = receipt["consensus_data"]["leader_receipt"][0]
        result = leader.get("genvm_result", {}) or {}
        err = result.get("stderr") or result.get("stdout") or ""
        tail = err[-300:]
        return f"{leader.get('execution_result')}: {tail}"
    except Exception:
        return str(receipt)[:400]


def assert_ok(receipt, what: str = "transaction") -> None:
    """Fail the test with the on-chain error text if the write reverted."""
    if tx_execution_failed(receipt):
        pytest.fail(f"{what} failed on StudioNet: {receipt_error(receipt)}")


# ------------------------------------------------------------------ accounts
@pytest.fixture(scope="session")
def operator(default_account):
    """Primary funded account: deploys contracts and acts as buyer."""
    return default_account


@pytest.fixture(scope="session")
def alice(accounts):
    """Secondary account from gltest.config.yaml (second key in the list)."""
    for acct in get_accounts():
        if str(acct.address).lower() == ALICE_ADDRESS:
            return acct
    pytest.skip(
        f"account {ALICE_ADDRESS} not found in gltest.config.yaml accounts"
    )


@pytest.fixture(scope="session")
def alice_ready(alice, marketplace):
    """Skip alice-gated tests until the second account has StudioNet gas.

    There is no balance RPC on StudioNet, so funding is probed with a cheap
    write (a probe listing) under a short wait budget.
    """
    probe_id = f"probe-{uuid.uuid4().hex[:8]}"
    try:
        receipt = (
            marketplace.connect(alice)
            .list_item(args=[probe_id, "funding probe", 0])
            .transact(wait_interval=1000, wait_retries=8)
        )
        if tx_execution_failed(receipt):
            pytest.skip(
                f"alice ({ALICE_ADDRESS}) has no StudioNet gas "
                f"(probe reverted): {receipt_error(receipt)}"
            )
    except Exception as exc:  # unfunded txs never reach ACCEPTED
        pytest.skip(f"alice ({ALICE_ADDRESS}) appears unfunded: {exc}")
    return alice


# ---------------------------------------------------------------- deployments
@pytest.fixture(scope="session")
def verifier(operator):
    """ReviewVerifier deployed once per session; operator = deployer account."""
    factory = get_contract_factory_safe(REVIEW_VERIFIER_PATH)
    return factory.deploy(args=[], account=operator)


@pytest.fixture(scope="session")
def marketplace(operator):
    """DemoMarketplace deployed once per session."""
    factory = get_contract_factory_safe(MARKETPLACE_PATH)
    return factory.deploy(args=[], account=operator)


def get_contract_factory_safe(path):
    from gltest import get_contract_factory

    return get_contract_factory(contract_file_path=path)


# ------------------------------------------------------------------- helpers
# Session scope: pure wrappers around session-scoped contracts/accounts;
# the session-scoped AI fixtures (authentic_review, fake_review) depend on
# them, so function scope would raise ScopeMismatch.
@pytest.fixture(scope="session")
def register_purchase(verifier):
    """Buyer-side helper: registration is self-service.

    The tx sender must equal the ``buyer`` argument, so the first argument
    is the buyer's gltest account (address is derived from it, never
    passed as a string).
    """

    def _register(buyer, purchase_id: str, product_id: str, seller) -> str:
        receipt = (
            verifier.connect(buyer)
            .register_purchase(
                args=[purchase_id, hex_addr(buyer), product_id, hex_addr(seller)]
            )
            .transact()
        )
        assert_ok(receipt, f"register_purchase({purchase_id})")
        return purchase_id

    return _register


@pytest.fixture(scope="session")
def submit_review(verifier):
    """Submit a review as an arbitrary sender.

    Returns the receipt; callers pass expect_ok=False when the Tier-1 gate
    is SUPPOSED to revert (assertions on failure happen in the test).
    """

    def _submit(as_account, purchase_id: str, rating: int, text: str, expect_ok=True):
        receipt = (
            verifier.connect(as_account)
            .submit_review(args=[purchase_id, rating, text])
            .transact()
        )
        if expect_ok:
            assert_ok(receipt, f"submit_review({purchase_id})")
        return receipt

    return _submit

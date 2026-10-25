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

# Test topology: `operator` deploys both contracts and is the SELLER of
# every listing; `alice` is the BUYER. Every purchase is a real marketplace
# order (buy -> mark_delivered [-> mark_used]) because the verifier now
# authenticates registrations against the marketplace. That needs two
# funded accounts, so the suite skips until alice has StudioNet gas.
#
# The main `verifier` fixture is deployed with cooldown 0 so the many
# review submissions in one session do not trip the per-buyer cooldown; the
# cooldown rule itself is tested against a dedicated `cooldown_verifier`
# deployed with the production value (3600s).
SHARED_PRODUCT = "prod-verify-shared"
E2E_PRODUCT = "prod-e2e"
PRODUCTION_COOLDOWN = 3600

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
    """Primary funded account: deploys contracts and is the SELLER."""
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


def _probe_funded(marketplace, account, label):
    """No balance RPC on StudioNet: probe funding with a cheap write."""
    probe_id = f"probe-{uuid.uuid4().hex[:8]}"
    try:
        receipt = (
            marketplace.connect(account)
            .list_item(args=[probe_id, "funding probe", 0])
            .transact(wait_interval=1000, wait_retries=8)
        )
        if tx_execution_failed(receipt):
            pytest.skip(
                f"{label} has no StudioNet gas (probe reverted): "
                f"{receipt_error(receipt)}"
            )
    except Exception as exc:  # unfunded txs never reach ACCEPTED
        pytest.skip(f"{label} appears unfunded: {exc}")
    return account


@pytest.fixture(scope="session")
def alice_ready(alice, marketplace):
    """Skip buyer-gated tests until the second account has StudioNet gas."""
    return _probe_funded(marketplace, alice, f"alice ({ALICE_ADDRESS})")


@pytest.fixture(scope="session")
def buyer(alice_ready):
    """The buyer in every marketplace order (alias for the funded alice)."""
    return alice_ready


@pytest.fixture(scope="session")
def mallory(marketplace):
    """Optional third funded account (accounts[2]) used as an outside attacker.

    Skips when the config has no third key or it is unfunded; the core
    adversarial tests also run with the seller account as the non-buyer.
    """
    accts = get_accounts()
    if len(accts) < 3:
        pytest.skip("no third account in gltest.config.yaml for the attacker role")
    return _probe_funded(
        marketplace, accts[2], f"third account ({str(accts[2].address).lower()})"
    )


# ---------------------------------------------------------------- deployments
@pytest.fixture(scope="session")
def marketplace(operator):
    """DemoMarketplace deployed once per session."""
    factory = get_contract_factory_safe(MARKETPLACE_PATH)
    return factory.deploy(args=[], account=operator)


def _deploy_verifier(operator, marketplace, cooldown_seconds):
    factory = get_contract_factory_safe(REVIEW_VERIFIER_PATH)
    return factory.deploy(
        args=[str(marketplace.address).lower(), cooldown_seconds], account=operator
    )


@pytest.fixture(scope="session")
def verifier(operator, marketplace):
    """ReviewVerifier bound to `marketplace`, cooldown disabled for the suite."""
    return _deploy_verifier(operator, marketplace, 0)


@pytest.fixture(scope="session")
def cooldown_verifier(operator, marketplace):
    """ReviewVerifier bound to the same marketplace with the production cooldown."""
    return _deploy_verifier(operator, marketplace, PRODUCTION_COOLDOWN)


def get_contract_factory_safe(path):
    from gltest import get_contract_factory

    return get_contract_factory(contract_file_path=path)


# ------------------------------------------------------------------- helpers
@pytest.fixture(scope="session")
def shared_listing(marketplace, operator):
    """SHARED_PRODUCT listed once by the operator (the seller)."""
    receipt = (
        marketplace.connect(operator)
        .list_item(args=[SHARED_PRODUCT, "Wireless Headphones Pro", 129])
        .transact()
    )
    assert_ok(receipt, "list_item (shared listing)")
    return SHARED_PRODUCT


@pytest.fixture(scope="session")
def make_order(marketplace, operator, buyer, shared_listing):
    """Create a real marketplace order and move it to ``state``.

    state: "PAID" | "DELIVERED" | "USED". The buyer buys, the seller
    (operator) marks delivery, the buyer confirms use. Returns the id.
    """

    def _make(state: str = "DELIVERED", product_id: str = SHARED_PRODUCT) -> str:
        purchase_id = new_purchase_id("ord")
        assert_ok(
            marketplace.connect(buyer).buy(args=[product_id, purchase_id]).transact(),
            f"buy({purchase_id})",
        )
        if state in ("DELIVERED", "USED"):
            assert_ok(
                marketplace.connect(operator)
                .mark_delivered(args=[purchase_id])
                .transact(),
                f"mark_delivered({purchase_id})",
            )
        if state == "USED":
            assert_ok(
                marketplace.connect(buyer).mark_used(args=[purchase_id]).transact(),
                f"mark_used({purchase_id})",
            )
        return purchase_id

    return _make


# Session scope: pure wrappers around session-scoped contracts/accounts;
# the session-scoped AI fixtures depend on them, so function scope would
# raise ScopeMismatch.
@pytest.fixture(scope="session")
def register_purchase(verifier):
    """Register a marketplace order with the verifier as ``account``.

    Registration takes only the purchase id; the verifier authenticates
    buyer, existence and delivery state against the marketplace. Pass
    ``expect_ok=False`` for attempts that are SUPPOSED to revert.
    """

    def _register(account, purchase_id: str, expect_ok=True, contract=None):
        target = contract or verifier
        receipt = target.connect(account).register_purchase(args=[purchase_id]).transact()
        if expect_ok:
            assert_ok(receipt, f"register_purchase({purchase_id})")
        return receipt

    return _register


@pytest.fixture(scope="session")
def submit_review(verifier):
    """Submit a review as an arbitrary sender.

    Returns the receipt; callers pass expect_ok=False when the Tier-1 gate
    is SUPPOSED to revert (assertions on failure happen in the test).
    """

    def _submit(as_account, purchase_id: str, rating: int, text: str,
                expect_ok=True, contract=None):
        target = contract or verifier
        receipt = (
            target.connect(as_account)
            .submit_review(args=[purchase_id, rating, text])
            .transact()
        )
        if expect_ok:
            assert_ok(receipt, f"submit_review({purchase_id})")
        return receipt

    return _submit

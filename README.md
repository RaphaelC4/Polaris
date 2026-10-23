# VERITAS - a two-tier anti-fake-review gate on GenLayer StudioNet

A **live** GenLayer project: a two-tier anti-fake-review gate
(`ReviewVerifier`) plus a minimal `DemoMarketplace`, tested and deployed
against **GenLayer StudioNet** (chain 61999) with real consensus and real AI
validators. No mocks, no local simulator.

## Architecture

### ReviewVerifier — two tiers

**Tier 1 — deterministic evidence gate** (every submission, consensus-free):
- buyer-only submission (sender must match the registered buyer)
- one review per purchase
- rating 1–5, review text 20–2000 chars after sanitization
- per-buyer **cooldown** (1h) across *different* products
- a passed review is stored `PENDING`

Registration is **self-service**: only the buyer of a purchase may register
it with the gate (tx sender must equal the `buyer` argument, and
buyer != seller is enforced here — so the reviewer is never the seller).

**Tier 2 — AI-consensus verification** (`verify_review`):
- runs the review through the **Equivalence Principle**
  (`gl.eq_principle.prompt_comparative`): independent validators judge
  authenticity and must agree on the **decision** (`authentic` must match;
  confidences only need to fall on the same side of 50) — comparing the raw
  confidence integer makes nearly every round `UNDETERMINED`
- `LIVE` / `DENIED` when consensus confidence >= 50%, otherwise
  `MANUAL_CHECK`
- governance: there is **no human arbiter**. The buyer can appeal a
  finished review (`LIVE` / `DENIED` / `MANUAL_CHECK`) once, which sends
  it back to `PENDING` for a fresh AI round with rotated validators. An
  unappealed `MANUAL_CHECK` is a terminal "unverified" state (it never
  counts toward a product's score).

**Calldata rule:** no float ever crosses a consensus/calldata boundary. The
AI's float confidence is converted to a **clamped int percentage inside the
nondeterministic closure**; aggregate ratings are returned as
`average_rating_x100` (e.g. `450` for 4.5 stars). This was a real bug found
in v0.1.0 and fixed in v0.1.1.

### DemoMarketplace

Minimal `list_item / buy / mark_delivered / mark_used` lifecycle
(`PAID -> DELIVERED -> USED`). The wiring between marketplace and verifier
is the buyer themself — the buyer registers their finished order with the
gate (self-service `register_purchase`); no backend or privileged
account is involved. A production version could wire this via on-chain
messaging at the `IReviewSource` seam.

## Project layout

```
contracts/
  review_verifier.py     # v0.1.1 — the gate (float-safe)
  demo_marketplace.py    # demo lifecycle
scripts/
  preflight.py           # RPC reachability + accounts + funding probe
  deploy.py              # stable deploy -> frontend/config.json
  seed.py                # demo data for the dashboard (idempotent)
  schema_debug.py        # contract ABI/schema diagnostics
frontend/
  index.html             # VERITAS dashboard (static, no build step)
  styles.css             # ethereal-glass design system
  app.js                 # browser calldata codec + gen_call reads
  config.json            # written by scripts/deploy.py
tests/
  conftest.py            # live fixtures (session-scoped deploys)
  test_review_verifier.py
  test_demo_marketplace.py
  test_e2e.py            # end-to-end journeys (marked e2e)
gltest.config.yaml       # StudioNet network + 2 accounts, leader_only
pytest.ini               # testpaths + e2e marker
.diag/                   # dead direct-mode files, kept for reference
```

### Contract requirements (GenVM runner)

Both contracts start with the pinned runner header on line 1
(`# { "Depends": "py-genlayer:1jb45..." }`), use `from genlayer import *`
(the lazy `gl` binding), declare their own `__init__`, and stay **pure
ASCII** — gltest's schema clients hash contract code with an ascii-strict
encoder, so a single non-ASCII character breaks ABI generation.

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Funding checklist (required, one-time)

StudioNet exposes **no balance RPC** through genlayer_py, so gas must be
added from the Studio web wallet:

1. Open https://studio.genlayer.com
2. Import / use the demo accounts from `gltest.config.yaml`:
   - **deployer/reader** `0x7b56902aedf906044E40e8821A6fc412Ad0808dB`
     (deploys contracts; also the sender identity for unsigned reads)
   - **buyer** `0x2B13BCB1281295Aa7Cb77326C02beE7768F78Ab0`
     (primary buyer in tests and seeds)
   - **seller** `0x85e021c0f2503f4b00cEE82CC8a422190194375b`
     (lists the seed products)
3. Use the **Faucet** on each, then verify:

```powershell
.venv\Scripts\python.exe scripts\preflight.py
```

The preflight probes gas by actually deploying the tiny marketplace
contract — if that is accepted, the operator has gas.

## Running

```powershell
# full live suite — 27 tests, ~14 min (alice-gated tests skip until she is funded)
.venv\Scripts\python.exe -m pytest -v

# marketplace only
.venv\Scripts\python.exe -m pytest tests\test_demo_marketplace.py -v

# end-to-end journeys only
.venv\Scripts\python.exe -m pytest -m e2e -v
```

Last full run: **26 passed** — the suite's Tier-1 rules, two-account
marketplace journeys, and real-AI verdicts all green.

## Frontend dashboard (VERITAS)

A static dashboard that streams live chain state through GenLayer's
`gen_call` (unsigned simulation — reads need no wallet) and signs real
StudioNet writes through MetaMask. The browser port of the calldata
wire format (tagged ULEB128 + RLP) lives in `frontend/gl-codec.js`.

```powershell
# 1. deploy stable contracts (writes frontend/config.json)
.venv\Scripts\python.exe scripts\deploy.py

# 2. seed demo data (idempotent; uses real AI verification, ~3 min)
.venv\Scripts\python.exe scripts\seed.py

# 3. serve the dashboard (any static server; CORS allows localhost origins)
cd frontend
python -m http.server 8000

# 4. open http://localhost:8000
```

StudioNet's CORS echoes the requesting origin, so the dashboard must be
served from an http(s) origin (not `file://`). `?flat=1` disables
scroll-reveal animations (used for screenshots/debug).

### Testing with MetaMask (marketplace workbench)

The **Marketplace** tab signs real StudioNet writes — each transaction
targets the ConsensusMain contract exactly like `genlayer_py` does
(verified byte-for-byte: `scripts/parity_reference.py` vs
`frontend/test_encode.html`, both must print the same calldata).

1. Install [MetaMask](https://metamask.io/) and open
   `http://localhost:8000`, then switch to the **Marketplace** tab.
2. Click **Connect wallet**. If StudioNet (chain 61999) is missing,
   MetaMask prompts to add it automatically.
3. Import the demo parties (buyer / seller / deployer — keys in
   `gltest.config.yaml`, addresses also shown under *demo parties* so you
   can copy them) and **switch accounts in MetaMask** to change role. The
   role chip in the banner shows who you are; each order renders only the
   actions the connected account may sign:
   - **seller** — `list_item`, `mark_delivered`
   - **buyer** — `buy`, `mark_used`, `register_purchase`, `submit_review`,
     `appeal_review`
   - **anyone** — `verify_review` (permissionless, real AI)
4. Work one order through the flow: list as the seller, `buy` as the
   buyer, confirm use, **register the purchase** (self-service — the
   buyer's tx sender equals the declared buyer, which is what makes the
   gate trustless), submit a review, then trigger `verify_review`.
   That last one involves live AI validators and takes 1–2 minutes; the
   status line tracks the tx and waits for the chain *effect* (not just
   the receipt), so progress is visible while consensus is still running.
5. Every write reports revert reasons (e.g. Tier-1 gate or state rules)
   inline, and the order book reloads from chain after every write.

The write encoder is shared: `gl-codec.js` (calldata + RLP) plus
ethers ABI wrapping, matching `genlayer_py`'s `encode_tx_data_call` +
`addTransaction` (5 validators, 3 rotations, `validUntil=0`,
`leader_only=false` → RLP `0x80`).

### What "live" means for assertions

- Tier-1 tests are deterministic: a rule violation **must** revert
  (`tx_execution_failed`), with receipt stderr asserted (e.g. the cooldown
  message).
- Tier-2 tests run **real StudioNet AI validators**. When the model is
  unsure (confidence < 50%) the verdict is `MANUAL_CHECK`, so assertions
  accept `{LIVE, MANUAL_CHECK}` for the authentic text and
  `{DENIED, MANUAL_CHECK}` for the spam text — every verdict's reason and
  confidence are printed for inspection.
- Alice-gated tests **skip** with a clear reason until her account has gas.

## Notes / limitations

- Registration is self-service (no operator), so the gate does not
  cross-check the marketplace's order state (that would need
  contract-to-contract calls). A buyer could register an order that
  never reached `USED`; the deterministic gate still enforces
  one-review-per-purchase / per-buyer cooldown, and the AI tier judges
  review text — but a production version should close this loop with an
  on-chain marketplace-verified registration.

- `genlayer_py` 0.16.3 has no `get_balance`/`transfer`; funding is
  faucet-only (hence preflight's probe approach).
- Studio can throttle AI consensus calls; the config polls receipts every
  3s for up to ~10 min.
- Records are stored as JSON blobs in `TreeMap`s; production would use
  typed storage records.

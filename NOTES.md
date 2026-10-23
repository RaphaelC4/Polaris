# Project notes — genlayer-review-verifier (StudioNet authentic mode)

## Architecture decision (FINAL)
- NO mocks, NO fallbacks. Everything runs against **StudioNet** (GenLayer
  testnet) with real AI validators through the Equivalence Principle.
- The earlier local "Direct Mode" port (old v0.2.12 std, dialect port,
  direct_patch.py in .diag/) is a DEAD branch kept for reference only.
- Contracts stay in the CURRENT dialect (genlayer-py 0.16.3 std: gl.Contract,
  TreeMap, gl.message, gl.eq_principle) — exactly what runs on-chain.

## GenVM runner rules (verified on-chain, StudioNet runs GenVM v0.2.16)
- Line 1 must be EXACTLY:
  `# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }`
  (docs canonical hash). No BOM, no other comment lines between header and
  code (they become runner.json). `:latest` only in `--debug-mode`.
- Import MUST be `from genlayer import *` — it binds the lazy `gl` proxy
  (= genlayer.gl) plus top-level names like `TreeMap`. Plain
  `import genlayer as gl` fails on-chain (no `gl.Contract` attribute).
- `gl.TreeMap` does not exist; use top-level `TreeMap[...]`.
- A contract class must define its own `__init__` in class `__dict__`
  (private, no-arg OK) or `get_schema` raises
  `TypeError: ('__init__ is absent', ...)`.
- gltest schema clients hash contract code with ascii-strict encode_hex:
  contract files must be PURE ASCII (this bit us on two U+2014 em-dashes).
- `gltest` extracts the contract class expecting base exactly `gl.Contract`
  (`gltest/artifacts/contract.py`).

## Live stack (verified from installed sources)
- gltest 0.29.2 + genlayer-py 0.16.3 in .venv (Python 3.14.4).
- CLI: `genlayer` IS installed; network set to studionet:
  url https://studio.genlayer.com/api, chain id 61999.
- Config file: `gltest.config.yaml` in project root. Schema (NetworkConfigData):
  networks: { <name>: { id, url, accounts: [private keys],
  from_account, leader_only, default_wait_interval,
  default_wait_retries, chain_type } }, default_network: <name>.
  studionet is PRECONFIGURED (no chain_type needed); custom networks
  require chain_type in {localnet, studionet, testnet_asimov, testnet_bradbury}.
- Fixtures (session-scoped, from gltest.fixtures, auto-registered):
  `gl_client`, `default_account`, `accounts`.
  accounts = LocalAccount list built from network config `accounts` keys;
  default_account = accounts[from_account] (from_account = index/address).
- Deploy: `get_contract_factory(path).deploy(*ctor_args, account=...)`
  returns the raw tx receipt (has the new contract address).
- Contract methods built from schema; `readonly` methods -> client.read_contract
  (sim_call), write methods -> sign+send tx, wait to ACCEPTED, return receipt.
- `Contract.connect(account)` clones the contract for another sender.
- `Contract.appeal(tx_hash)` exists (real consensus appeal).

## Account for StudioNet (generated, in gltest.config.yaml)
- deployer/reader: 0x7b56902aedf906044E40e8821A6fc412Ad0808dB — HAS GAS
  (preflight deploys are accepted). private key: see gltest.config.yaml.
  Carries no contract rights; `reader` in config.json is only the sender
  identity for unsigned read simulations.
- buyer: 0x2B13BCB1281295Aa7Cb77326C02beE7768F78Ab0 — status unknown;
  multi-account tests skip gracefully until the Studio faucet tops her up.
- POST /api/faucet returns 204 but is a NO-OP (verified); real funding =
  Studio web UI faucet (login required).

## Float rule (contract port requirement, verified via diagF)
- AI verdict JSON crossing consensus must be calldata-safe: NO floats.
- Contract must sanitize: confidence -> int 0-100 before returning the
  verdict dict from the nondet closure. Never store/encode floats.
- Principle must compare exact sanitized fields, e.g.
  "The `authentic` boolean and `confidence` integer must match".

## Known constraints on live testnet (design consequences)
- Per-test fresh deploys are too slow (real consensus): tests share a
  session-scoped verifier+marketplace, use unique purchase ids per test.
- Revert messages: how write failures surface -> assert failure status,
  not message (verify exact behavior from genlayer_py before finalizing).
- Verdict tests with real AI: authentic text -> LIVE is the strong
  assertion; spam text -> assert verdict in {LIVE, DENIED} (record reason).
- leader_only: True (Studio-based RPC supports it) keeps sims fast.
- warp()/mock_llm()/expect_revert() DO NOT EXIST live. Cooldown 1h tested
  via a second immediate review from the same buyer (fails live).
- Verdict assertions tolerate MANUAL_CHECK: authentic text asserts
  {LIVE, MANUAL}, spam text {DENIED, MANUAL}, reasons/confidence printed.
- Tests must be ASCII-safe on print: AI verdict_reason can contain e.g.
  U+2011, which crashes cp1252 consoles — conftest reconfigures
  stdout/stderr to UTF-8 with errors="replace".
- Session-scope rule: fixtures the session-scoped AI fixtures depend on
  (register_purchase, submit_review) must also be session-scoped or pytest
  raises ScopeMismatch.

## Test suite status (Phase 3, verified)
- 26 tests, GREEN on StudioNet (Tier-1 rules, two-account journeys, real
  AI verdicts). Full run ~14 min. Operator role removed 2026-10-18:
  register_purchase is buyer self-service (sender == buyer), set_verdict
  deleted (no human overrides), appeal_review works once from any final
  verdict (LIVE/DENIED/MANUAL_CHECK) back to PENDING for a fresh AI round.
- Run order (alphabetical): test_demo_marketplace -> test_e2e ->
  test_review_verifier. Every buyer submission lands on SHARED_PRODUCT
  so nothing trips the 1h cross-product cooldown before the dedicated
  cooldown test runs. Manual single-rerun after transient StudioNet
  502/-32002 failures instead of a full-suite rerun (rate limit 30
  req/min).

## Frontend (VERITAS dashboard, Phase 5 — DONE: reads + MetaMask marketplace)
- Tabbed Workbench app shell (hallmark redesign): Overview · Reviews ·
  Lookup · Contracts · Test — one panel per view, hash + ?tab= routing,
  WAI-ARIA tablist, no long scroll. Homepage (Overview) is a pure hero:
  eyebrow + h1 + lede + two CTAs, over a full-bleed backdrop image
  (assets/creation-hands.jpg - robot + human hand, a Creation-of-Adam
  homage) with a violet glow at the fingertips, a top scrim for legibility
  and a slow drift. The instrument panel, "how the gate works" strip,
  fresh-verdicts rail and the live hero-stats counters were REMOVED
  (v2026-10-18) - the gate mechanics live in the Reviews/Marketplace
  tabs.
  the gate mechanics live in the Reviews/Marketplace tabs. Test tab
  (retitled Marketplace) is a real multi-party workbench over a full-bleed backdrop
  image (assets/ancient-assembly.jpg - a Roman assembly under an oculus, a
  consensus homage) with a dark scrim: a role banner
  (acting-as chip + demo parties to copy), a listings column (buy CTA),
  and a live order book; selecting an order opens a detail drawer with a
  stage timeline. Each wallet is shown only the actions it may sign
  (switch MetaMask accounts to change role) — the contracts enforce the
  same rules on-chain. Type system (non-generic-AI, single family):
  Geist 400/500/600 for display AND body (weight-driven hierarchy,
  tight tracking -0.04em on headings) · Geist Mono for all data,
  code and microlabels (tabular numerals). Accent words
  <span class="em"> = Geist 500 violet (no serif, no italics). Fonts
  load from Google Fonts in one css2 request. Contracts tab carries a
  consensus spec strip (validators 5 / rotations 3 / validUntil 0 /
  leader_only→0x80).
- Lookup try-ids + product defaults use live seed data (ord-live-1 /
  ord-denied-1 / ord-pending-1, aurora-headphones).
- Static, no build step: frontend/index.html + styles.css + app.js +
  gl-codec.js (shared codec, no DOM deps) + wallet.js (MetaMask) +
  vendor/ethers.umd.min.js + config.json (written by scripts/deploy.py).
- Browser read path (verified end-to-end in headless Edge against live
  chain): calldata tagged-ULEB128 encode {"method","args"} -> RLP
  ([bytes, 0x00]) -> JSON-RPC gen_call {type:"read", to, from, data,
  transaction_hash_variant:"LATEST_NONFINAL"} -> hex (unprefixed) ->
  calldata decode. `from` needs no signature (unsigned sim).
- Browser WRITE path (MetaMask, eth_sendTransaction to ConsensusMain):
  sim_getConsensusContract(["ConsensusMain"]) -> {address, abi};
  inner payload = RLP([calldata.encode({method,args}), leader_only])
  then ABI addTransaction(sender, to, validators=5, rotations=3, data,
  validUntil=0). CONFIRMED byte-identical to genlayer_py:
  scripts/parity_reference.py vs frontend/test_encode.html (586 chars,
  PASS). CRITICAL RLP GOTCHA: reads use raw b"\x00" (encodes 0x00) but
  writes encode a Python bool - rlp(False)=0x80, rlp(True)=0x01. A JS
  0x00 there silently corrupts every write. Also: the live ABI is
  uint256 x6 (not uint32), so the selector is the uint256 signature.
- Write success confirmation = effect-based polling (re-read state via
  gen_call) + eth_getTransactionReceipt only for revert detection
  (status 0x0); AI consensus takes 1-2 min so receipt==success is not
  enough. 15 min timeout, phased UI status line.
- Marketplace workbench design (replaced the guided 9-step journey):
  role is derived from the connected account (buyer/seller are read from
  config.json, else observer — NO operator, the gate has no arbiter).
  Capabilities — seller: list_item, mark_delivered; buyer: buy,
  mark_used, register_purchase (self-service: sender == buyer),
  submit_review, appeal_review (once, from any final verdict); anyone:
  verify_review (permissionless, real AI). MANUAL_CHECK displays as
  "unverified" and is terminal unless the buyer appeals.
  Only a wallet's own signable actions render on each order.
  loadMarket() reads get_all_products / get_all_purchases + per-order
  get_review_safe, so the book reflects on-chain state after every write.
- genlayer_py references for the port: genlayer_py/abi/calldata
  (encoder/decoder/consts), abi/transactions.py (rlp),
  contracts/actions.py (read_contract / write_contract /
  _encode_add_transaction_data), consensus/consensus_main/encoder.py.
- CORS: OPTIONS + POST both return access-control-allow-origin echoing
  the request Origin (tested with http://localhost:8000) -> any local
  static server works. POST with a non-browser UA (Python-urllib) gets
  HTTP 403 (bot filter); browsers pass. No balance/sim_call limits hit.
- scripts/deploy.py: stable deploys -> frontend/config.json (addresses
  persist between test sessions, which redeploy fresh contracts).
- scripts/seed.py: state-aware, retry-on-TLS-reset, idempotent seeder —
  2 products (aurora-headphones, terra-espresso; seller = bob) + 7
  orders covering every state (LIVE / DENIED / PENDING / registered /
  USED / DELIVERED / PAID). NOTE: register_purchase is buyer
  self-service (sender must equal buyer; registration enforces
  buyer != seller, so the reviewer is never the seller).
- ?flat=1 query param forces reveals visible + reports layout width via
  document.title (headless screenshot/debug aid).
- Headless note: Edge --headless min window width ~492 CSS px (a
  390px --window-size PNG crops the viewport); measured no horizontal
  overflow at 492 (scrollWidth 477). Zombie msedge processes steal
  stdout from --dump-dom (len 0) - kill them first. --screenshot above
  ~5000px window height fails silently (exit 0, no file).

## Files
- contracts/review_verifier.py, contracts/demo_marketplace.py (current
  dialect, pinned runner hash, pure ASCII)
- tests/: conftest.py + test_review_verifier.py + test_demo_marketplace.py
  + test_e2e.py (live journeys) — all green
- scripts/preflight.py (funding probe, passes);
  scripts/deploy.py + scripts/seed.py (frontend deploy/demo data);
  scripts/schema_debug.py (contract ABI/schema diagnostics)
- .diag/ = dead direct-mode files (direct_patch.py,
  demo_e2e_direct_mode.py, old diagnostics)
- frontend/ = VERITAS dashboard (static, read-only, live gen_call
  reads; see Frontend section above).

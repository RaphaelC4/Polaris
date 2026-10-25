/* ═══════════════════════════════════════════════════════════════════
   VERITAS app — browser read path for GenLayer StudioNet.

   Wire format (ported from genlayer_py):
     calldata = tagged ULEB128 stream: {"method":…,"args":[…]}
     request  = RLP([calldata_bytes, 0x00]) over JSON-RPC "gen_call"
     response = hex (unprefixed) -> calldata decode
   ═══════════════════════════════════════════════════════════════════ */
"use strict";

/* ──────────────────────────── RPC client ─────────────────────────── */

let CFG = null;

async function rpc(method, params) {
  const res = await fetch(CFG.rpc, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = await res.json();
  if (body.error) throw new Error(body.error.message || "rpc error");
  return body.result;
}

async function genCall(to, method, args) {
  const obj = { method };
  if (args && args.length) obj.args = args;
  const data = "0x" + hex(rlpEncode([calldataEncode(obj), Uint8Array.of(0)]));
  const result = await rpc("gen_call", [{
    type: "read",
    to,
    from: CFG.reader,
    data,
    transaction_hash_variant: "LATEST_NONFINAL",
  }]);
  return calldataDecode(fromHex(result));
}

const V = {
  stats: () => genCall(CFG.verifier, "stats"),
  score: (pid) => genCall(CFG.verifier, "product_score", [pid]),
  reviews: (pid) => genCall(CFG.verifier, "get_product_reviews", [pid]),
  review: (pid) => genCall(CFG.verifier, "get_review", [pid]),
  reviewSafe: (pid) => genCall(CFG.verifier, "get_review_safe", [pid]),
  reviewIds: () => genCall(CFG.verifier, "review_ids"),
  product: (pid) => genCall(CFG.marketplace, "get_product", [pid]),
  purchase: (pid) => genCall(CFG.marketplace, "get_purchase", [pid]),
  allProducts: () => genCall(CFG.marketplace, "get_all_products"),
  allPurchases: () => genCall(CFG.marketplace, "get_all_purchases"),
};

/* ──────────────────────────── UI helpers ─────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { product: "aurora-headphones", stats: null };

function statusClass(status) {
  return (
    { LIVE: "live", DENIED: "denied", MANUAL_CHECK: "manual", PENDING: "pending" }[status] ||
    "pending"
  );
}

function pillHTML(status) {
  const label = { MANUAL_CHECK: "unverified" }[status] || status;
  return `<span class="pill pill-${statusClass(status)}">${label}</span>`;
}

function starsHTML(rating) {
  const full = Math.max(0, Math.min(5, Math.round(rating)));
  return "★".repeat(full) + `<span class="off">${"★".repeat(5 - full)}</span>`;
}

function shortAddr(a) {
  if (!a || a.length < 14) return a || "–";
  return `${a.slice(0, 8)}…${a.slice(-6)}`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function safeUrl(u) {
  const s = String(u || "").trim();
  return /^https?:\/\//i.test(s) ? s : "";
}

function evidenceLinksHTML(r) {
  const web = safeUrl(r.web_url);
  const media = String(r.media_links || "").split("\n").map(safeUrl).filter(Boolean);
  if (!web && !media.length) return "";
  const ext = ' target="_blank" rel="noopener noreferrer"';
  const icon = '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M4.5 11.5 11.5 4.5M6 4.5h5.5V10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  let out = '<div class="evidence-links">';
  if (web) {
    out += `<span class="net-k">product link</span>
      <a class="evidence-link" href="${esc(web)}"${ext}><span class="mono">${esc(web)}</span>${icon}</a>`;
  }
  if (media.length) {
    out += `<span class="net-k">media</span>`;
    for (const m of media) {
      out += `<a class="evidence-link" href="${esc(m)}"${ext}><span class="mono">${esc(m)}</span>${icon}</a>`;
    }
  }
  return out + "</div>";
}

function tsLabel(sec) {
  if (!sec) return "—";
  const d = new Date(sec * 1000);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

/* ─────────────────────────── rendering ───────────────────────────── */

function metaRow(label, value, mono = true) {
  const v = value === undefined || value === null || value === "" ? "-" : value;
  return `<div class="meta-row">
      <span class="net-k">${esc(label)}</span>
      ${mono ? `<code class="mono">${esc(v)}</code>` : `<span class="tiny dim">${esc(v)}</span>`}
    </div>`;
}

function detailInnerHTML(r) {
  const conf = Number(r.verdict_confidence || 0);
  const text =
    esc(r.text) || `<span class="dim">registered &mdash; no review attached yet</span>`;
  const reason = r.verdict_reason
    ? `<div class="verdict-reason"><b class="mono" style="color:var(--ink)">${conf}%</b> &mdash; ${esc(r.verdict_reason)}</div>`
    : "";
  return `
    <div class="review-text">${text}${reason}</div>
    ${evidenceLinksHTML(r)}
    <div class="review-meta">
      ${metaRow("purchase id", r.purchase_id)}
      ${metaRow("product", r.product_id)}
      ${metaRow("buyer", shortAddr(r.buyer))}
      ${metaRow("seller", shortAddr(r.seller))}
      ${metaRow("submitted", tsLabel(r.submitted_ts), false)}
      ${metaRow("appealed", r.appealed ? "yes" : "no", false)}
    </div>`;
}

function renderReviews(list) {
  const host = $("#reviewList");
  $("#tableCount").textContent =
    `${list.length} record${list.length === 1 ? "" : "s"}`;
  if (!list.length) {
    host.innerHTML =
      `<div class="empty-line">No reviews for <code class="mono">${esc(state.product)}</code> yet.</div>`;
    return;
  }
  const sorted = [...list].sort(
    (a, b) => (b.submitted_ts || 0) - (a.submitted_ts || 0) ||
      a.purchase_id.localeCompare(b.purchase_id)
  );
  host.innerHTML = sorted
    .map((r) => {
      const conf = Number(r.verdict_confidence || 0);
      return `
      <div class="review-item" data-pid="${esc(r.purchase_id)}">
        <button class="row-main" type="button">
          <span class="pid mono">${esc(r.purchase_id)}</span>
          <span class="stars">${starsHTML(r.rating)}</span>
          ${pillHTML(r.status)}
          <span class="conf">
            <span class="conf-bar"><i style="width:${conf}%"></i></span>
            <span class="conf-num">${conf ? conf + "%" : "—"}</span>
          </span>
          <span class="appeal${r.appealed ? " yes" : ""}">${r.appealed ? "appealed" : "—"}</span>
        </button>
        <div class="row-detail"><div>
          <div class="detail-inner">${detailInnerHTML(r)}</div>
        </div></div>
      </div>`;
    })
    .join("");

  $$(".row-main", host).forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.closest(".review-item").classList.toggle("open");
    });
  });
}

function renderLookup(r) {
  const conf = Number(r.verdict_confidence || 0);
  $("#detailSlot").innerHTML = `
    <div class="bezel"><div class="core">
      <div class="detail-card">
        <div class="detail-status">
          ${pillHTML(r.status)}
          <span class="detail-rating mono">${(r.rating || 0).toFixed(1)}<small>/5</small></span>
          <span class="stars">${starsHTML(r.rating)}</span>
        </div>
        <div class="detail-body">
          <div class="detail-grid">
            ${metaRow("purchase id", r.purchase_id)}
            ${metaRow("product", r.product_id)}
            ${metaRow("buyer", shortAddr(r.buyer))}
            ${metaRow("seller", shortAddr(r.seller))}
            ${metaRow("submitted", tsLabel(r.submitted_ts), false)}
            ${metaRow("appealed", r.appealed ? "yes" : "no", false)}
          </div>
          <div class="detail-text">${esc(r.text) ||
            '<span class="dim">no text yet — registered, awaiting review submission</span>'}</div>
          ${evidenceLinksHTML(r)}
          ${r.verdict_reason
            ? `<div class="verdict-reason"><b class="mono" style="color:var(--ink)">${conf}%</b> — ${esc(r.verdict_reason)}</div>`
            : ""}
        </div>
      </div>
    </div></div>`;
}

function renderLookupError(msg, pid) {
  $("#detailSlot").innerHTML = `
    <div class="bezel"><div class="core">
      <div class="detail-err">No readable review for <code class="mono">${esc(pid)}</code>.
        ${esc(msg)}</div>
    </div></div>`;
}

/* ─────────────────────────── data loading ────────────────────────── */

async function safe(promise, onFail) {
  try { return await promise; } catch (e) { return onFail(e); }
}

async function refreshAll() {
  const stats = await safe(V.stats(), (e) => {
    $("#netDot").className = "dot err";
    $("#netLabel").textContent = "read failed";
    toast("Chain read failed — is StudioNet reachable?");
    return null;
  });
  if (!stats) return;
  $("#netDot").className = "dot ok";
  $("#netLabel").textContent = `StudioNet · ${CFG.chainId}`;

  const pid = state.product;
  await safe(V.reviews(pid).then(renderReviews), () => {
    $("#reviewList").innerHTML =
      `<div class="empty-line">Could not read reviews for this product.</div>`;
  });

  const testPanel = $("#panel-test");
  if (testPanel && !testPanel.hidden) await loadMarket();
}

async function boot() {
  try {
    // strip a UTF-8 BOM if present: Response.json() chokes on it and the
    // whole app would silently fail to boot.
    const raw = await (await fetch("config.json")).text();
    CFG = JSON.parse(raw.replace(/^\uFEFF/, ""));
  } catch {
    $("#netDot").className = "dot err";
    $("#netLabel").textContent = "no config.json";
    document.getElementById("reviewList").innerHTML =
      `<div class="empty-line">Missing <code class="mono">frontend/config.json</code> — run
       <code class="mono">scripts\\deploy.py</code> first.</div>`;
    return;
  }

  $("#addrVerifier").textContent = CFG.verifier;
  $("#addrMarket").textContent = CFG.marketplace;
  $("#addrReader").textContent = CFG.reader;
  $("#productInput").value = state.product;
  $("#tableProduct").textContent = state.product;

  await safe(rpc("eth_chainId", []).then((h) => {
    $("#chainId").textContent = String(parseInt(h, 16));
    $("#netDot").className = "dot ok";
  }), () => {
    $("#netDot").className = "dot err";
    $("#netLabel").textContent = "unreachable";
  });

  await refreshAll();
}

/* ───────────────────────────── events ────────────────────────────── */

document.addEventListener("DOMContentLoaded", () => {
  /* debug: ?flat=1 disables reveal animation (used for screenshots) */
  if (new URLSearchParams(location.search).has("flat")) {
    document.documentElement.classList.add("flat");
    window.addEventListener("load", () => {
      const wide = [...document.querySelectorAll("*")]
        .filter((el) => el.getBoundingClientRect().right > innerWidth + 1)
        .map((el) => `${el.tagName}.${String(el.className).slice(0, 40)}@${Math.round(el.getBoundingClientRect().right)}`)
        .slice(0, 14);
      document.title = `SW=${document.documentElement.scrollWidth} IW=${innerWidth} :: ${wide.join(" | ")}`;
    });
  }

  /* ── tab controller (Workbench app shell) ── */
  const TAB_IDS = ["overview", "reviews", "lookup", "contracts", "test"];
  const tabs = $$('[role="tab"][data-tab]');
  const panels = $$(".panel[data-panel]");

  function activateTab(name, { updateHash = true } = {}) {
    if (!TAB_IDS.includes(name)) name = "overview";
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p) => {
      const on = p.dataset.panel === name;
      p.hidden = !on;
      if (on) {
        /* restart the panel-in animation */
        p.style.animation = "none";
        void p.offsetWidth;
        p.style.animation = "";
      }
    });
    if (name === "test" && CFG) loadMarket();
    if (updateHash && location.hash.slice(1) !== name) {
      history.replaceState(null, "", "#" + name);
    }
  }

  tabs.forEach((t) => {
    t.addEventListener("click", () => activateTab(t.dataset.tab));
    t.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(t);
      let j = null;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = tabs.length - 1;
      if (j !== null) {
        e.preventDefault();
        tabs[j].focus();
        activateTab(tabs[j].dataset.tab);
      }
    });
  });
  $$("[data-tab-link]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      activateTab(el.dataset.tabLink);
    });
  });
  window.addEventListener("hashchange", () => {
    const h = location.hash.slice(1);
    activateTab(h === "testbench" ? "test" : h);
  });
  activateTab(
    TAB_IDS.includes(location.hash.slice(1))
      ? location.hash.slice(1)
      : TAB_IDS.includes(new URLSearchParams(location.search).get("tab"))
        ? new URLSearchParams(location.search).get("tab")
        : "overview",
    { updateHash: true }
  );

  /* copy buttons */
  $$("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const val = CFG && CFG[btn.dataset.copy];
      if (!val) return;
      try { await navigator.clipboard.writeText(val); } catch { /* denied */ }
      btn.classList.add("ok");
      toast("Copied " + shortAddr(val));
      setTimeout(() => btn.classList.remove("ok"), 1400);
    });
  });

  /* product switch */
  $("#productForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const pid = $("#productInput").value.trim();
    if (!pid) return;
    state.product = pid;
    $("#tableProduct").textContent = pid;
    refreshAll();
  });

  /* lookup */
  $("#lookupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pid = $("#lookupInput").value.trim();
    if (!pid || !CFG) return;
    $("#detailSlot").innerHTML =
      `<div class="bezel dim-bezel"><div class="core"><div class="empty-line">Simulating read on chain…</div></div></div>`;
    try {
      renderLookup(await V.review(pid));
    } catch (err) {
      renderLookupError("Simulation failed — the id is unknown to the verifier.", pid);
    }
  });

  $$("[data-try]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#lookupInput").value = btn.dataset.try;
      $("#lookupForm").requestSubmit();
    });
  });

  /* light auto-refresh */
  setInterval(() => { if (CFG) refreshAll(); }, 45000);

  boot();
});

/* ═══════════════════════ write path (MetaMask) ═══════════════════════
   StudioNet writes are standard EIP-1559 txs to the consensus contract:
     sim_getConsensusContract -> {address, abi}
     data = RLP([calldata.encode({method,args}), leader_only])
     tx   = addTransaction(sender, to, validators=5, rotations=3, data)
   Signed by MetaMask via eth_sendTransaction. Success is reported ONLY
   from the transaction's own consensus record (classifyTx in gl-codec.js:
   ACCEPTED/FINALIZED + execution SUCCESS) - never from shared chain
   state, which another wallet's tx could also produce.
   ═══════════════════════════════════════════════════════════════════ */

const VALIDATORS = 5;      /* studionet defaults from genlayer_py */
const MAX_ROTATIONS = 3;
const SHARED = "prod-verify-shared";
const PHANTOM_SELLER = "0x9999999999999999999999999999999999999999";
const AUTHENTIC_TEXT =
  "I have used these headphones on my train commute every day for the " +
  "past two weeks. The battery easily covers the full round trip and " +
  "the bass stays clean even at high volume.";

let consensusCache = null;

async function getConsensus() {
  if (consensusCache) return consensusCache;
  const res = await rpc("sim_getConsensusContract", ["ConsensusMain"]);
  if (!res || !res.address || !res.abi) {
    throw new Error("sim_getConsensusContract returned no ConsensusMain");
  }
  consensusCache = res;
  return res;
}

function glPayload(method, args, leaderOnly) {
  const obj = { method };
  if (args && args.length) obj.args = args;
  /* genlayer_py serializes leader_only as a Python bool: rlp(True)=0x01,
     rlp(False)=0x80 (empty = integer 0). Empty bytes encode to 0x80. */
  return rlpEncode([calldataEncode(obj), leaderOnly ? Uint8Array.of(1) : new Uint8Array(0)]);
}

async function buildAddTxData(toContract, method, args, sender) {
  const cons = await getConsensus();
  const iface = new ethers.Interface(cons.abi);
  const fn = iface.getFunction("addTransaction");
  const payload = "0x" + hex(glPayload(method, args, false));
  const base = [sender, toContract, VALIDATORS, MAX_ROTATIONS, payload];
  const callArgs = fn.inputs.length >= 6 ? [...base, 0] : base;
  return iface.encodeFunctionData("addTransaction", callArgs);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function txPhase(phase, label, hash, secs, detail) {
  const el = $("#txStatus");
  if (!el) return;
  el.hidden = false;
  el.className = "tx-line tx-" + phase;
  const words = {
    sent: "sent — wallet confirmed",
    waiting: `in consensus… ${secs}s`,
    done: `accepted · ${secs}s`,
    final: `finalized · ${secs}s`,
    error: `failed${detail ? " — " + detail : ""}`,
  };
  const short = hash ? `${hash.slice(0, 10)}…${hash.slice(-6)}` : "";
  el.innerHTML =
    `<span class="tx-dot" aria-hidden="true"></span>` +
    `<span class="mono tx-label">${esc(label)}</span>` +
    `<span class="tx-word">${esc(words[phase] || phase)}</span>` +
    (short ? `<code class="mono tx-hash">${esc(short)}</code>` : "");
}

/* Wait for THIS transaction's own outcome. The record comes from
   eth_getTransactionByHash and is classified by classifyTx(): success only
   when consensus ACCEPTED/FINALIZED and the leader's execution_result is
   SUCCESS. A reverted tx, an UNDETERMINED/CANCELED round, or a tx whose
   result cannot be read is never reported as success. */
async function waitForTx(hash, label, timeoutMs) {
  const t0 = Date.now();
  const deadline = t0 + (timeoutMs || 15 * 60 * 1000);
  txPhase("sent", label, hash, 0);
  while (Date.now() < deadline) {
    await sleep(4000);
    const secs = Math.round((Date.now() - t0) / 1000);
    let tx = null;
    try { tx = await rpc("eth_getTransactionByHash", [hash]); } catch { /* hiccup */ }
    const out = classifyTx(tx);
    if (out.phase === "failed") {
      txPhase("error", label, hash, secs, out.detail || "reverted on-chain");
      throw new Error(`${label}: ${out.detail || "transaction failed"} (${hash})`);
    }
    if (out.phase === "ok") {
      txPhase(out.final ? "final" : "done", label, hash, secs);
      return out;
    }
    txPhase("waiting", label, hash, secs);
  }
  txPhase("error", label, hash, 900, "still pending after 15 min");
  throw new Error(`${label}: consensus still pending after 15 min (tx ${hash})`);
}

async function sendTx({ to, method, args = [], label, timeoutMs }) {
  if (!Wallet || !Wallet.isReady()) {
    throw new Error("Connect MetaMask (StudioNet) first");
  }
  const sender = Wallet.state.account;
  const data = await buildAddTxData(to, method, args, sender);
  const tx = { from: sender, to, data, value: "0x0" };
  try {
    const gas = await rpc("eth_estimateGas", [tx]);
    tx.gas = gas.startsWith?.("0x") ? gas : "0x" + gas;
  } catch { /* MetaMask will estimate */ }
  const hash = await window.ethereum.request({
    method: "eth_sendTransaction",
    params: [tx],
  });
  return waitForTx(hash, label || method, timeoutMs);
}

/* ───────────────────────── marketplace workbench ───────────────────
   The real multi-party flow. Every write is signed by the connected
   MetaMask account, and the UI only surfaces the actions that account
   may actually sign for a given order — the contracts enforce the same
   rules on-chain:

     seller   list_item · mark_delivered
     buyer    buy · mark_used · register_purchase · submit_review · appeal_review
     anyone   verify_review
    ─────────────────────────────────────────────────────────────────── */

const randHex = () => Math.random().toString(16).slice(2, 8);

const MK = { products: [], orders: [], reviews: {}, scores: {}, selected: null, loading: false };

const VERIFIER_METHODS = new Set([
  "register_purchase", "submit_review", "verify_review", "appeal_review",
]);
const contractFor = (m) => (VERIFIER_METHODS.has(m) ? CFG.verifier : CFG.marketplace);
const isReg = (rev) => !!rev && !!rev.status;
const FINISHED = ["LIVE", "DENIED", "MANUAL_CHECK"];

const ACTION_LABEL = {
  mark_delivered: "Mark delivered",
  mark_used: "Mark used",
  register_purchase: "Register purchase",
  submit_review: "Write review",
  verify_review: "Verify (AI)",
  appeal_review: "Appeal",
};

const ROLE_COPY = {
  buyer: "Buyer — purchase, confirm use, register, submit and appeal reviews.",
  seller: "Seller — list products and mark orders delivered.",
  both: "Buyer & seller — buy any listing, or list products of your own.",
  none: "Connect MetaMask, then switch accounts to act as each party.",
};

const ROLE_LABEL = {
  buyer: "buyer",
  seller: "seller",
  both: "buyer · seller",
  none: "not connected",
};

const STATE_META = {
  PAID: { cls: "pending", label: "paid" },
  DELIVERED: { cls: "info", label: "delivered" },
  USED: { cls: "used", label: "used" },
};

const TIMELINE = ["paid", "delivered", "used", "registered", "reviewed", "verdict"];

function roleOf(addr) {
  if (!addr || !CFG) return "none";
  const a = String(addr).toLowerCase();
  const isBuyer = !!CFG.buyer && a === CFG.buyer.toLowerCase();
  const isSeller = !!CFG.seller && a === CFG.seller.toLowerCase();
  if (isBuyer && isSeller) return "both";
  if (isBuyer) return "buyer";
  if (isSeller) return "seller";
  /* any wallet can list (seller) and buy (buyer) — the contracts assign
     the role to msg.sender per listing / purchase. */
  return "both";
}

/* ── capabilities: what may the connected wallet sign for this order? ── */

function orderActions(order, rev, addr) {
  const a = String(addr || "").toLowerCase();
  const acts = [];
  if (order.state === "PAID" && a === order.seller) acts.push("mark_delivered");
  if (order.state === "DELIVERED" && a === order.buyer) acts.push("mark_used");
  /* the verifier re-checks buyer + DELIVERED/USED against the marketplace
     on-chain; this only decides whether to offer the button */
  if ((order.state === "DELIVERED" || order.state === "USED") && a === order.buyer && !isReg(rev)) acts.push("register_purchase");
  if (rev && rev.rating === 0 && a === order.buyer) acts.push("submit_review");
  if (rev && rev.rating > 0 && rev.status === "PENDING") acts.push("verify_review");
  if (rev && FINISHED.includes(rev.status) && a === order.buyer && !rev.appealed) acts.push("appeal_review");
  return acts;
}

/* ── load + render ─────────────────────────────────────────────────── */

async function loadMarket() {
  if (!CFG || MK.loading) return;
  MK.loading = true;
  try {
    const [products, orders] = await Promise.all([
      V.allProducts().catch(() => []),
      V.allPurchases().catch(() => []),
    ]);
    MK.products = products || [];
    MK.orders = orders || [];
    const revs = await Promise.all(
      MK.orders.map((o) => V.reviewSafe(o.purchase_id).catch(() => ({})))
    );
    MK.reviews = {};
    MK.orders.forEach((o, i) => { MK.reviews[o.purchase_id] = revs[i] || {}; });
    const scores = await Promise.all(
      MK.products.map((p) => V.score(p.product_id).catch(() => null))
    );
    MK.scores = {};
    MK.products.forEach((p, i) => { MK.scores[p.product_id] = scores[i]; });
  } finally {
    MK.loading = false;
  }
  renderListings();
  renderOrderBook();
  /* never rebuild the detail panel while the user is mid-review — the
     background refresh would wipe typed text and drop focus. */
  if (MK.selected && !reviewFormDirty()) renderOrderDetail();
}

function reviewFormDirty() {
  const form = document.querySelector('#orderDetailBody form[data-act="submit_review"]');
  if (!form) return false;
  const ae = document.activeElement;
  if (ae && form.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)) return true;
  return [...form.querySelectorAll("input, textarea")].some((el) => el.value !== el.defaultValue);
}

function renderParties() { }

function renderRole() {
  const chip = $("#roleChip");
  if (!chip) return;
  const addr = Wallet.state.account;
  const role = roleOf(addr);
  chip.dataset.role = role;
  chip.className = "pill role-chip role-" + role;
  chip.textContent = ROLE_LABEL[role] || role;
  $("#roleAddr").textContent = addr || "connect MetaMask to begin";
  $("#roleHint").textContent = !Wallet.state.installed
    ? "MetaMask not detected in this browser."
    : ROLE_COPY[role];
  renderListings();
  renderOrderBook();
  if (MK.selected && !reviewFormDirty()) renderOrderDetail();
}

function renderListings() {
  const el = $("#listingGrid");
  if (!el) return;
  if (!MK.products.length) {
    el.innerHTML =
      `<div class="empty-line">No listings yet — connect any account and list the first product below.</div>`;
    return;
  }
  const addr = (Wallet.state.account || "").toLowerCase();
  el.innerHTML = MK.products
    .slice()
    .sort((a, b) => a.product_id.localeCompare(b.product_id))
    .map((p) => {
      const mine = p.seller.toLowerCase() === addr;
      const s = MK.scores[p.product_id];
      const scoreTxt =
        s && s.live_reviews
          ? `${(s.average_rating_x100 / 100).toFixed(1)}★ live · ${s.live_reviews}`
          : "no live reviews";
      const canBuy = !!Wallet.state.account && !mine;
      return `<article class="listing">
        <div class="listing-top">
          <b>${esc(p.title)}</b>
          <span class="mono listing-price">${esc(String(p.price))}<i>GEN</i></span>
        </div>
        <div class="listing-meta">
          <code class="mono">${esc(p.product_id)}</code>
          <span class="tiny dim">${mine ? "your listing" : shortAddr(p.seller)}</span>
        </div>
        <div class="listing-foot">
          <span class="tiny dim">${scoreTxt}</span>
          <button class="chip-btn" type="button" data-buy="${esc(p.product_id)}" ${canBuy ? "" : "disabled"}>
            ${mine ? "your item" : "Buy"}
          </button>
        </div>
      </article>`;
    })
    .join("");
}

function sortOrders(a, b) {
  const addr = Wallet.state.account;
  const aa = orderActions(a, MK.reviews[a.purchase_id], addr).length;
  const ab = orderActions(b, MK.reviews[b.purchase_id], addr).length;
  if ((aa > 0) !== (ab > 0)) return aa > 0 ? -1 : 1;
  return a.purchase_id.localeCompare(b.purchase_id);
}

function reviewPill(rev) {
  if (!isReg(rev)) return `<span class="pill pill-none">unregistered</span>`;
  if (rev.rating === 0) return `<span class="pill pill-pending">awaiting review</span>`;
  return pillHTML(rev.status);
}

function actionBtn(oid, m) {
  const long = m === "verify_review" ? ` title="real AI consensus — takes 1–2 min"` : "";
  return `<button class="chip-btn chip-act" type="button" data-act="${m}" data-oid="${esc(oid)}"${long}>${ACTION_LABEL[m] || m}</button>`;
}

function renderOrderBook() {
  const el = $("#orderBook");
  if (!el) return;
  const count = $("#orderCount");
  if (count) count.textContent = MK.orders.length ? `${MK.orders.length} order${MK.orders.length > 1 ? "s" : ""}` : "empty";
  if (!MK.orders.length) {
    el.innerHTML =
      `<div class="empty-line">No orders yet — buy a listing as the buyer, then walk it through the flow.</div>`;
    return;
  }
  const addr = Wallet.state.account;
  el.innerHTML = MK.orders
    .slice()
    .sort(sortOrders)
    .map((o) => {
      const rev = MK.reviews[o.purchase_id];
      const sm = STATE_META[o.state] || { cls: "pending", label: String(o.state).toLowerCase() };
      const acts = orderActions(o, rev, addr);
      const btns = acts.map((m) => actionBtn(o.purchase_id, m)).join("");
      return `<article class="order${MK.selected === o.purchase_id ? " order-sel" : ""}" data-oid="${esc(o.purchase_id)}">
        <div class="order-id">
          <code class="mono">${esc(o.purchase_id)}</code>
          <span class="tiny dim">${esc(o.product_id)}</span>
        </div>
        <div class="order-flags">
          <span class="pill state-${sm.cls}">${sm.label}</span>
          ${reviewPill(rev)}
        </div>
        <div class="order-btns">${btns || `<span class="order-none tiny dim">no action for this wallet</span>`}</div>
      </article>`;
    })
    .join("");
}

function orderStage(order, rev) {
  let s = 1;
  if (order.state === "DELIVERED" || order.state === "USED") s = 2;
  if (order.state === "USED") s = 3;
  if (isReg(rev)) {
    s = 4;
    if (rev.rating > 0) s = 5;
    if (rev.status && rev.status !== "PENDING") s = 6;
  }
  return s;
}

function renderOrderDetail() {
  const body = $("#orderDetailBody");
  if (!body) return;
  const order = MK.orders.find((o) => o.purchase_id === MK.selected);
  if (!order) { body.innerHTML = ""; return; }
  const rev = MK.reviews[order.purchase_id];
  const stage = orderStage(order, rev);
  const addr = Wallet.state.account;
  const acts = orderActions(order, rev, addr);
  const sm = STATE_META[order.state] || { cls: "pending", label: String(order.state).toLowerCase() };

  const steps = TIMELINE.map((t, i) => {
    const cls = i < stage ? "done" : i === stage ? "cur" : "todo";
    return `<li class="tl-${cls}"><span class="tl-dot">${i < stage ? "✓" : i + 1}</span><span>${t}</span></li>`;
  }).join("");

  const simpleActs = acts.filter((m) => m !== "submit_review");
  const btnRow = simpleActs.map((m) => actionBtn(order.purchase_id, m)).join("");

  let form = "";
  if (acts.includes("submit_review")) {
    form += `<form class="ord-form" data-act="submit_review" data-oid="${esc(order.purchase_id)}">
      <div class="action-head"><code class="mono">submit_review</code><span class="tiny dim">as the buyer · Tier-1 gate</span></div>
      <div class="action-fields">
        <input name="rating" type="number" min="1" max="5" value="5" required>
        <textarea name="reviewText" rows="3" placeholder="review text (20–2000 chars)" required></textarea>
        <input name="webUrl" class="mono" inputmode="url" spellcheck="false" autocomplete="off"
               placeholder="https://product page (validators fetch it)" aria-label="Product web link">
        <input name="mediaLinks" class="mono" spellcheck="false" autocomplete="off"
               placeholder="image/file links, comma separated (max 4)" aria-label="Media links">
      </div>
      <button class="btn small ghost" type="submit"><span>Submit review</span><span class="btn-ico" aria-hidden="true">↗</span></button>
    </form>`;
  }

  body.innerHTML = `
    <div class="ord-head">
      <div class="ord-title">
        <h3 class="mono">${esc(order.purchase_id)}</h3>
        <span class="tiny dim">${esc(order.product_id)}</span>
      </div>
      <div class="order-flags">
        <span class="pill state-${sm.cls}">${sm.label}</span>
        ${reviewPill(rev)}
      </div>
      <button class="mini-btn" type="button" data-close-detail aria-label="Close detail">✕</button>
    </div>

    <div class="ord-body">
      <ol class="timeline">${steps}</ol>
      <div class="ord-facts">
        <div class="fact"><span class="k">buyer</span><code class="mono" title="${esc(order.buyer)}">${shortAddr(order.buyer)}</code></div>
        <div class="fact"><span class="k">seller</span><code class="mono" title="${esc(order.seller)}">${shortAddr(order.seller)}</code></div>
        <div class="fact"><span class="k">rating</span><span>${isReg(rev) && rev.rating ? starsHTML(rev.rating) : "—"}</span></div>
        <div class="fact"><span class="k">confidence</span><code class="mono">${isReg(rev) && rev.verdict_confidence ? rev.verdict_confidence + "%" : "—"}</code></div>
      </div>
    </div>

    ${isReg(rev) && rev.verdict_reason ? `<p class="ord-reason"><span class="eyebrow">chain reason</span> ${esc(rev.verdict_reason)}</p>` : ""}

    <div class="ord-actions">
      ${btnRow ? `<div class="ord-btns">${btnRow}</div>` : ""}
      ${form}
      ${!btnRow && !form ? `<p class="tiny dim">No action available for the connected wallet at this stage.${addr ? "" : " Connect MetaMask first."}</p>` : ""}
    </div>`;
}

function selectOrder(oid) {
  MK.selected = oid;
  renderOrderBook();
  renderOrderDetail();
  const d = $("#orderDetail");
  if (d) {
    d.hidden = false;
    d.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function closeDetail() {
  MK.selected = null;
  const d = $("#orderDetail");
  if (d) d.hidden = true;
  renderOrderBook();
}

/* ── writes ─────────────────────────────────────────────────────────── */

async function doWrite(to, method, args, timeoutMs) {
  try {
    await sendTx({ to, method, args, label: method, timeoutMs });
    toast(`${method} accepted`);
    await refreshAll();
    return true;
  } catch (err) {
    toast(String(err.message || err).slice(0, 160));
    return false;
  }
}

async function buyProduct(pid) {
  if (!Wallet.state.account) { toast("Connect MetaMask first"); return; }
  await doWrite(CFG.marketplace, "buy", [pid, `ord-${randHex()}`]);
}

async function runOrderAction(oid, method) {
  const order = MK.orders.find((o) => o.purchase_id === oid);
  if (!order) return;
  if (method === "submit_review") { selectOrder(oid); return; }
  let args;
  switch (method) {
    case "mark_delivered": args = [oid]; break;
    case "mark_used": args = [oid]; break;
    case "register_purchase": args = [oid]; break;
    case "verify_review": args = [oid]; break;
    case "appeal_review": args = [oid, "I believe this review is genuine"]; break;
    default: return;
  }
  await doWrite(contractFor(method), method, args, 5 * 60 * 1000);
}

/* ── interaction delegates ──────────────────────────────────────────── */

document.addEventListener("click", (e) => {
  const buy = e.target.closest("[data-buy]");
  if (buy) { buyProduct(buy.dataset.buy); return; }
  const act = e.target.closest("button[data-act]");
  if (act) { runOrderAction(act.dataset.oid, act.dataset.act); return; }
  const closeBtn = e.target.closest("[data-close-detail]");
  if (closeBtn) { closeDetail(); return; }
  const copy = e.target.closest("[data-copy-addr]");
  if (copy) {
    navigator.clipboard.writeText(copy.dataset.copyAddr).catch(() => {});
    toast("Address copied");
    return;
  }
  const row = e.target.closest(".order");
  if (row) { selectOrder(row.dataset.oid); }
});

/* ─────────────────────── form submits (delegate) ─────────────────── */

document.addEventListener("submit", (e) => {
  const detail = e.target.closest("form[data-act]");
  if (detail) { e.preventDefault(); submitDetailForm(detail); return; }
  const listForm = e.target.closest("#listForm");
  if (listForm) { e.preventDefault(); submitListForm(listForm); }
});

async function submitListForm(form) {
  const btn = form.querySelector('button[type="submit"]');
  const pid = form.querySelector('[name="productId"]').value.trim();
  const title = form.querySelector('[name="title"]').value.trim();
  const price = Number(form.querySelector('[name="price"]').value);
  if (!pid || !title || Number.isNaN(price)) return;
  btn.disabled = true;
  const ok = await doWrite(CFG.marketplace, "list_item", [pid, title, price]);
  if (ok) form.reset();
  btn.disabled = false;
}

async function submitDetailForm(form) {
  const btn = form.querySelector('button[type="submit"]');
  const oid = form.dataset.oid;
  btn.disabled = true;
  if (form.dataset.act === "submit_review") {
    const rating = Number(form.querySelector('[name="rating"]').value);
    const text = form.querySelector('[name="reviewText"]').value.trim();
    const webUrl = (form.querySelector('[name="webUrl"]')?.value || "").trim();
    const media = (form.querySelector('[name="mediaLinks"]')?.value || "").trim();
    await doWrite(CFG.verifier, "submit_review", [oid, rating, text, webUrl, media], 5 * 60 * 1000);
  }
  btn.disabled = false;
}

/* ─────────────────────────── wallet UI ───────────────────────────── */

function renderWallet() {
  const btn = $("#walletBtn"), lbl = $("#walletLabel");
  if (!btn) return;
  btn.classList.remove("wallet-ok", "wallet-warn");
  if (!Wallet.state.installed) {
    lbl.textContent = "Install MetaMask";
    btn.classList.add("wallet-warn");
  } else if (Wallet.state.connecting) {
    lbl.textContent = "Connecting…";
  } else if (!Wallet.state.account) {
    lbl.textContent = "Connect wallet";
  } else if (Wallet.state.chainId !== Wallet.CHAIN_ID_HEX) {
    lbl.textContent = "Switch to StudioNet";
    btn.classList.add("wallet-warn");
  } else {
    lbl.textContent = shortAddr(Wallet.state.account);
    btn.classList.add("wallet-ok");
  }
  renderRole();
  const disc = $("#walletDisconnect");
  if (disc) disc.hidden = !Wallet.state.account;
}

async function onWalletClick() {
  try {
    if (!Wallet.state.installed) {
      window.open("https://metamask.io/download/", "_blank", "noopener");
      return;
    }
    if (!Wallet.state.account) {
      await Wallet.connect();
      toast("Connected — actions unlock for this account's role");
    } else if (Wallet.state.chainId !== Wallet.CHAIN_ID_HEX) {
      await Wallet.ensureChain();
    } else {
      try { await navigator.clipboard.writeText(Wallet.state.account); } catch { /* denied */ }
      toast("Address copied — switch accounts in MetaMask to change role");
    }
  } catch (err) {
    toast(String(err.message || err).slice(0, 140));
  }
}

// wallet wiring
(async () => {
  if (typeof Wallet === "undefined") return;
  await Wallet.init();
  Wallet.on("accounts", renderWallet);
  Wallet.on("chain", renderWallet);
  $("#walletBtn").addEventListener("click", onWalletClick);
  const discBtn = $("#walletDisconnect");
  if (discBtn) {
    discBtn.addEventListener("click", async () => {
      try {
        await Wallet.disconnect();
        toast("Wallet disconnected");
      } catch (err) {
        toast(String(err.message || err).slice(0, 140));
      }
    });
  }
  const refresh = $("#marketRefresh");
  if (refresh) {
    refresh.addEventListener("click", (e) => {
      e.currentTarget.classList.add("spin");
      setTimeout(() => e.currentTarget.classList.remove("spin"), 750);
      loadMarket();
    });
  }
  renderWallet();
})();

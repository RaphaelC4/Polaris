/* ═══════════════════════════════════════════════════════════════════
   Shared calldata codec + RLP (ported from genlayer_py), used by both
   app.js (read + write paths) and test_encode.html (parity harness).
   No DOM dependencies — safe to load in any context.
   ═══════════════════════════════════════════════════════════════════ */
"use strict";

/* ────────────────────────── calldata codec ───────────────────────── */

const T = { SPECIAL: 0, PINT: 1, NINT: 2, BYTES: 3, STR: 4, ARR: 5, MAP: 6 };
const S = { NULL: 0, FALSE: 8, TRUE: 16, ADDR: 24 };
const ADDR_SIZE = 20;

const utf8enc = new TextEncoder();
const utf8dec = new TextDecoder();

function uleb(mem, n) {
  if (n === 0) { mem.push(0); return; }
  while (n > 0) {
    let b = n % 128;
    n = Math.floor(n / 128);
    if (n > 0) b |= 0x80;
    mem.push(b);
  }
}

function calldataEncode(x) {
  const mem = [];
  const enc = (v) => {
    if (v === null || v === undefined) mem.push(S.NULL);
    else if (v === true) mem.push(S.TRUE);
    else if (v === false) mem.push(S.FALSE);
    else if (typeof v === "number") {
      if (!Number.isInteger(v)) throw new Error("floats are not calldata-safe");
      if (v >= 0) uleb(mem, v * 8 + T.PINT);
      else uleb(mem, (-v - 1) * 8 + T.NINT);
    } else if (typeof v === "string") {
      const b = utf8enc.encode(v);
      uleb(mem, b.length * 8 + T.STR);
      for (const byte of b) mem.push(byte);
    } else if (v instanceof Uint8Array) {
      uleb(mem, v.length * 8 + T.BYTES);
      for (const byte of v) mem.push(byte);
    } else if (Array.isArray(v)) {
      uleb(mem, v.length * 8 + T.ARR);
      v.forEach(enc);
    } else if (typeof v === "object") {
      const keys = Object.keys(v).sort();
      uleb(mem, keys.length * 8 + T.MAP);
      for (const k of keys) {
        const kb = utf8enc.encode(k);
        uleb(mem, kb.length);
        for (const byte of kb) mem.push(byte);
        enc(v[k]);
      }
    } else throw new Error(`cannot encode ${typeof v}`);
  };
  enc(x);
  return Uint8Array.from(mem);
}

function calldataDecode(bytes) {
  let i = 0;
  const ulebRead = () => {
    let ret = 0, shift = 0, m;
    do {
      m = bytes[i++];
      ret += (m & 0x7f) * 2 ** shift;
      shift += 7;
    } while (m & 0x80);
    return ret;
  };
  const impl = () => {
    const code = ulebRead();
    const typ = code & 7;
    if (typ === T.SPECIAL) {
      if (code === S.NULL) return null;
      if (code === S.FALSE) return false;
      if (code === S.TRUE) return true;
      if (code === S.ADDR) {
        const a = bytes.slice(i, i + ADDR_SIZE);
        i += ADDR_SIZE;
        return "0x" + hex(a);
      }
      throw new Error(`unknown special ${code}`);
    }
    const len = code >> 3;
    if (typ === T.PINT) return len;
    if (typ === T.NINT) return -len - 1;
    if (typ === T.BYTES) {
      const b = bytes.slice(i, i + len);
      i += len;
      return b;
    }
    if (typ === T.STR) {
      const s = utf8dec.decode(bytes.slice(i, i + len));
      i += len;
      return s;
    }
    if (typ === T.ARR) {
      const arr = [];
      for (let k = 0; k < len; k++) arr.push(impl());
      return arr;
    }
    if (typ === T.MAP) {
      const obj = {};
      for (let k = 0; k < len; k++) {
        const le = ulebRead();
        const key = utf8dec.decode(bytes.slice(i, i + le));
        i += le;
        obj[key] = impl();
      }
      return obj;
    }
    throw new Error(`bad type ${typ}`);
  };
  const out = impl();
  if (i !== bytes.length) throw new Error("trailing calldata bytes");
  return out;
}

/* ─────────────────────────── RLP wrapper ─────────────────────────── */

function concatBytes(chunks) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

function rlpLength(n, shortBase, longBase) {
  if (n < 56) return Uint8Array.of(shortBase + n);
  const be = [];
  let m = n;
  while (m > 0) { be.unshift(m % 256); m = Math.floor(m / 256); }
  return Uint8Array.of(longBase + be.length, ...be);
}

function rlpEncode(item) {
  if (item instanceof Uint8Array) {
    if (item.length === 1 && item[0] < 0x80) return item;
    return concatBytes([rlpLength(item.length, 0x80, 0xb7), item]);
  }
  if (Array.isArray(item)) {
    const payload = concatBytes(item.map(rlpEncode));
    return concatBytes([rlpLength(payload.length, 0xc0, 0xf7), payload]);
  }
  throw new Error("rlp: unsupported item");
}

/* ─────────────────────────── hex helpers ─────────────────────────── */

function hex(bytes) {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

function fromHex(s) {
  if (s.startsWith("0x") || s.startsWith("0X")) s = s.slice(2);
  const out = new Uint8Array(s.length >> 1);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
  return out;
}

/* ───────────────────── transaction outcome classifier ─────────────────
   Decides, from the transaction's OWN consensus record (the object
   eth_getTransactionByHash returns on StudioNet), whether THAT tx
   executed successfully. The UI must never infer success from shared
   chain state: another wallet's tx can produce the same state (for
   example someone else registering the same purchase id), which would
   make a reverted tx look successful.

   Returns { phase, final, detail }:
     phase "ok"      - consensus ACCEPTED/FINALIZED and the leader's
                       execution_result is SUCCESS
     phase "failed"  - the tx reverted (execution ERROR) or consensus
                       ended UNDETERMINED / CANCELED / timed out
     phase "pending" - anything else, including a missing record or an
                       accepted tx whose execution result is not readable
                       (never treated as success)
   `final` is true only once the status is FINALIZED.
   ─────────────────────────────────────────────────────────────────── */

const GL_TX_STATUS = {
  0: "UNINITIALIZED", 1: "PENDING", 2: "PROPOSING", 3: "COMMITTING",
  4: "REVEALING", 5: "ACCEPTED", 6: "UNDETERMINED", 7: "FINALIZED",
  8: "CANCELED", 9: "APPEAL_REVEALING", 10: "APPEAL_COMMITTING",
  11: "READY_TO_FINALIZE", 12: "VALIDATORS_TIMEOUT", 13: "LEADER_TIMEOUT",
};

function classifyTx(tx) {
  if (!tx || typeof tx !== "object") return { phase: "pending", final: false, detail: "" };

  let status = tx.status_name ?? tx.statusName ?? tx.status;
  if (typeof status === "number" || /^\d+$/.test(String(status))) {
    status = GL_TX_STATUS[Number(status)] || String(status);
  }
  status = String(status || "").toUpperCase();

  const cd = tx.consensus_data ?? tx.consensusData ?? {};
  const receipts = cd.leader_receipt ?? cd.leaderReceipt ?? [];
  const leader = Array.isArray(receipts) ? receipts[0] : receipts;
  const exec = String(leader?.execution_result ?? leader?.executionResult ?? "").toUpperCase();
  const vmres = leader?.genvm_result ?? leader?.genvmResult ?? {};
  const detail = String(vmres?.stderr || vmres?.stdout || "").trim().slice(-200);

  if (["UNDETERMINED", "CANCELED", "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT"].includes(status)) {
    return { phase: "failed", final: false, detail: detail || `consensus ended ${status}` };
  }
  if (status === "ACCEPTED" || status === "FINALIZED") {
    if (exec === "SUCCESS") return { phase: "ok", final: status === "FINALIZED", detail: "" };
    if (exec === "ERROR") return { phase: "failed", final: status === "FINALIZED", detail: detail || "execution error" };
    return { phase: "pending", final: false, detail: "" };
  }
  return { phase: "pending", final: false, detail: "" };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifyTx };
}

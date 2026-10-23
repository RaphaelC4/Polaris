/* ═══════════════════════════════════════════════════════════════════
   VERITAS wallet — injected EIP-1193 (MetaMask) connection.

   Any injected wallet can drive the marketplace workbench: it only ever
   signs one action at a time for whichever account the user has
   selected.

   StudioNet is enforced: if MetaMask is on another chain, ensureChain()
   offers wallet_switchEthereumChain and, if the wallet does not know
   StudioNet yet (error 4902), wallet_addEthereumChain with the params
   below.
   ═══════════════════════════════════════════════════════════════════ */
"use strict";

const Wallet = (() => {
  const CHAIN_ID_HEX = "0xf22f";                 /* 61999 */
  const CHAIN_PARAMS = {
    chainId: CHAIN_ID_HEX,
    chainName: "GenLayer StudioNet",
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
    rpcUrls: ["https://studio.genlayer.com/api"],
    blockExplorerUrls: ["https://studio.genlayer.com"],
  };

  const state = { installed: false, account: null, chainId: null, connecting: false };
  const handlers = { accounts: [], chain: [] };
  let ethereum = null;

  function emit(kind) {
    handlers[kind].forEach((fn) => fn(state));
  }
  function on(kind, fn) {
    handlers[kind].push(fn);
  }

  function attachEvents() {
    ethereum.on("accountsChanged", (accs) => {
      state.account = accs && accs.length ? accs[0] : null;
      emit("accounts");
    });
    ethereum.on("chainChanged", (cid) => {
      state.chainId = cid;
      emit("chain");
    });
  }

  async function init() {
    ethereum = window.ethereum || null;
    state.installed = !!ethereum;
    if (!ethereum) return state;
    try {
      const accs = await ethereum.request({ method: "eth_accounts" });
      state.account = accs && accs.length ? accs[0] : null;
      state.chainId = await ethereum.request({ method: "eth_chainId" });
    } catch { /* locked / denied — leave defaults */ }
    attachEvents();
    emit("accounts");
    emit("chain");
    return state;
  }

  async function ensureChain() {
    if (!ethereum) return false;
    const current = await ethereum.request({ method: "eth_chainId" });
    state.chainId = current;
    if (current === CHAIN_ID_HEX) return true;
    try {
      await ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: CHAIN_ID_HEX }],
      });
      state.chainId = CHAIN_ID_HEX;
      emit("chain");
      return true;
    } catch (err) {
      /* 4902 = chain unknown to the wallet -> offer to add it */
      const code = err && (err.code || (err.data && err.data.originalError && err.data.originalError.code));
      if (code === 4902) {
        await ethereum.request({ method: "wallet_addEthereumChain", params: [CHAIN_PARAMS] });
        state.chainId = CHAIN_ID_HEX;
        emit("chain");
        return true;
      }
      throw err;
    }
  }

  async function connect() {
    if (!ethereum) throw new Error("MetaMask (or another injected wallet) is not installed");
    state.connecting = true;
    emit("accounts");
    try {
      const accs = await ethereum.request({ method: "eth_requestAccounts" });
      state.account = accs && accs.length ? accs[0] : null;
      await ensureChain();
      emit("accounts");
      return state;
    } finally {
      state.connecting = false;
      emit("accounts");
    }
  }

  /* EIP-1193 has no standard "disconnect". Modern MetaMask supports
     wallet_revokePermissions; where it is unavailable we still clear the
     local session so the UI returns to the connect state. */
  async function disconnect() {
    if (!ethereum) return false;
    try {
      await ethereum.request({
        method: "wallet_revokePermissions",
        params: [{ eth_accounts: {} }],
      });
    } catch { /* unsupported — fall through to a local clear */ }
    state.account = null;
    emit("accounts");
    return true;
  }

  const isReady = () => !!state.account;

  return { state, init, connect, disconnect, ensureChain, on, isReady, CHAIN_ID_HEX, CHAIN_PARAMS };
})();

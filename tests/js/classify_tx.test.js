// Run: node tests/js/classify_tx.test.js
// Verifies the frontend never reports success for a tx that did not itself
// execute successfully (the preempted-registration case in particular).
const assert = require("assert");
const { classifyTx } = require("../../frontend/gl-codec.js");

const rec = (status, exec, stderr) => ({
  status_name: status,
  consensus_data: {
    leader_receipt: [{ execution_result: exec, genvm_result: { stderr: stderr || "" } }],
  },
});

// own tx succeeded
assert.deepStrictEqual(classifyTx(rec("ACCEPTED", "SUCCESS")).phase, "ok");
assert.strictEqual(classifyTx(rec("ACCEPTED", "SUCCESS")).final, false);
assert.strictEqual(classifyTx(rec("FINALIZED", "SUCCESS")).final, true);

// reverted tx (e.g. attacker-preempted registration) must be a failure,
// and the revert reason must be surfaced
const reverted = classifyTx(rec("ACCEPTED", "ERROR", "ValueError: purchase already registered"));
assert.strictEqual(reverted.phase, "failed");
assert.ok(reverted.detail.includes("purchase already registered"));

// consensus that did not agree is a failure
for (const s of ["UNDETERMINED", "CANCELED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"]) {
  assert.strictEqual(classifyTx(rec(s, "SUCCESS")).phase, "failed", s);
}

// in-flight, missing, or unreadable results are never success
for (const s of ["PENDING", "PROPOSING", "COMMITTING", "REVEALING"]) {
  assert.strictEqual(classifyTx(rec(s, "SUCCESS")).phase, "pending", s);
}
assert.strictEqual(classifyTx(null).phase, "pending");
assert.strictEqual(classifyTx({}).phase, "pending");
assert.strictEqual(classifyTx({ status_name: "ACCEPTED" }).phase, "pending");

// numeric status codes (5 = ACCEPTED, 7 = FINALIZED)
assert.strictEqual(classifyTx({ status: 5, ...rec(undefined, "SUCCESS") , status_name: undefined }).phase, "ok");
assert.strictEqual(classifyTx({ ...rec(undefined, "SUCCESS"), status_name: undefined, status: "7" }).final, true);

console.log("classify_tx: all assertions passed");

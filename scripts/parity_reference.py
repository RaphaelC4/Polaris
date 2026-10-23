"""Reference encoder for the write-path parity check.

Prints the exact addTransaction calldata that genlayer_py would produce
for the fixture used by frontend/test_encode.html. Diff against the
browser output: they must match byte-for-byte.

Fixture (keep in sync with test_encode.html):
  sender    = 0x2b13bcb1281295aa7cb77326c02bee7768f78ab0
  recipient = 0x7f6e31c2d72c284c7ff4ceb1cbe34a776417617a (verifier)
  method    = submit_review, args = ["parity-check-1", 5, "hello",
             "https://example.com/p", ""]
  leader_only = False, validators = 5, rotations = 3, valid_until = 0
"""

from genlayer_py.abi import calldata
from genlayer_py.abi.transactions import serialize
from genlayer_py.contracts.utils import make_calldata_object
from genlayer_py.consensus.consensus_main.encoder import encode_add_transaction_data

FIXTURE = {
    "sender": "0x2b13bcb1281295aa7cb77326c02bee7768f78ab0",
    "recipient": "0x7f6e31c2d72c284c7ff4ceb1cbe34a776417617a",
    "method": "submit_review",
    "args": ["parity-check-1", 5, "hello", "https://example.com/p", ""],
    "leader_only": False,
    "validators": 5,
    "rotations": 3,
}


def main() -> None:
    inner = serialize(
        [
            calldata.encode(
                make_calldata_object(method=FIXTURE["method"], args=FIXTURE["args"])
            ),
            FIXTURE["leader_only"],
        ]
    )
    calldata_hex = encode_add_transaction_data(
        sender_address=FIXTURE["sender"],
        recipient_address=FIXTURE["recipient"],
        num_of_initial_validators=FIXTURE["validators"],
        max_rotations=FIXTURE["rotations"],
        tx_data=inner,
        valid_until=0,
    )
    print(calldata_hex.lower())


if __name__ == "__main__":
    main()

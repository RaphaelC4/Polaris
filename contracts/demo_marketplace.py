# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""DemoMarketplace - minimal demo marketplace for the ReviewVerifier gate.

Not production code: shows the purchase lifecycle the verifier gates on.
Purchase states flow PAID -> DELIVERED -> USED. A ReviewVerifier bound to
this marketplace reads orders through `get_purchase_safe` (a
contract-to-contract view call) and only lets the order's recorded buyer
register it for review once it reached DELIVERED or USED. This contract is
the source of truth for who bought what and whether it was delivered.
"""

from genlayer import *  # binds the lazy `gl` proxy (genlayer.gl) + TreeMap
import json

STATE_PAID = "PAID"
STATE_DELIVERED = "DELIVERED"
STATE_USED = "USED"


class DemoMarketplace(gl.Contract):
    # listings: product_id -> JSON {"product_id","seller","title","price"}
    listings: TreeMap[str, str]
    # purchases: purchase_id -> JSON {"purchase_id","product_id","buyer","seller","state"}
    purchases: TreeMap[str, str]

    # TreeMaps are auto-instantiated by the storage layer; do NOT assign
    # them here (see review_verifier.py).

    def __init__(self) -> None:
        # The GenVM schema generator requires an explicit (private) __init__
        # on every contract class - an inherited/absent one is a TypeError.
        pass

    # -------------------------------------------------------------- seller
    @gl.public.write
    def list_item(self, product_id: str, title: str, price: int) -> None:
        """List a product; the sender becomes the seller."""
        seller = gl.message.sender_address.as_hex.lower()
        self._require(product_id not in self.listings, "product already listed")
        self._require(len(title) > 0, "title required")
        self._require(price >= 0, "price must be non-negative")
        self.listings[product_id] = json.dumps(
            {
                "product_id": product_id,
                "seller": seller,
                "title": title,
                "price": price,
            }
        )

    # --------------------------------------------------------------- buyer
    @gl.public.write
    def buy(self, product_id: str, purchase_id: str) -> str:
        """Buy a product under an externally chosen purchase_id."""
        self._require(product_id in self.listings, "unknown product")
        self._require(purchase_id not in self.purchases, "purchase_id already used")
        listing = json.loads(self.listings[product_id])
        buyer = gl.message.sender_address.as_hex.lower()
        self._require(buyer != listing["seller"], "seller cannot buy own product")

        self.purchases[purchase_id] = json.dumps(
            {
                "purchase_id": purchase_id,
                "product_id": product_id,
                "buyer": buyer,
                "seller": listing["seller"],
                "state": STATE_PAID,
            }
        )
        return STATE_PAID

    # ----------------------------------------------------------- lifecycle
    @gl.public.write
    def mark_delivered(self, purchase_id: str) -> str:
        """Seller marks a PAID order as delivered."""
        record = self._load(purchase_id)
        self._require(
            record["state"] == STATE_PAID, "order is not in PAID state"
        )
        self._require(
            gl.message.sender_address.as_hex.lower() == record["seller"],
            "only the seller may mark delivery",
        )
        record["state"] = STATE_DELIVERED
        self._save(record)
        return STATE_DELIVERED

    @gl.public.write
    def mark_used(self, purchase_id: str) -> str:
        """Buyer confirms the product was used (strongest review eligibility)."""
        record = self._load(purchase_id)
        self._require(
            record["state"] == STATE_DELIVERED, "order is not in DELIVERED state"
        )
        self._require(
            gl.message.sender_address.as_hex.lower() == record["buyer"],
            "only the buyer may mark the product used",
        )
        record["state"] = STATE_USED
        self._save(record)
        return STATE_USED

    # -------------------------------------------------------------- views
    @gl.public.view
    def get_product(self, product_id: str) -> dict:
        self._require(product_id in self.listings, "unknown product")
        return json.loads(self.listings[product_id])

    @gl.public.view
    def get_purchase(self, purchase_id: str) -> dict:
        return self._load(purchase_id)

    @gl.public.view
    def get_purchase_safe(self, purchase_id: str) -> dict:
        """Non-reverting lookup: {} when the purchase does not exist.

        This is the read the ReviewVerifier makes across contracts, so an
        unknown id must come back as an empty record, not an exception.
        """
        if purchase_id not in self.purchases:
            return {}
        return json.loads(self.purchases[purchase_id])

    @gl.public.view
    def get_all_products(self) -> list:
        """Every listing, for the marketplace grid."""
        return [json.loads(blob) for _, blob in self.listings.items()]

    @gl.public.view
    def get_all_purchases(self) -> list:
        """Every order, for the order book (newest-first sort in the UI)."""
        return [json.loads(blob) for _, blob in self.purchases.items()]

    # ----------------------------------------------------------- internal
    def _load(self, purchase_id: str) -> dict:
        self._require(purchase_id in self.purchases, "unknown purchase")
        return json.loads(self.purchases[purchase_id])

    def _save(self, record: dict) -> None:
        self.purchases[record["purchase_id"]] = json.dumps(record)

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""ReviewVerifier - reusable two-tier anti-fake-review gate for GenLayer.

Tier 1 (deterministic evidence gate): provable, consensus-free checks that
run on every submission - buyer-only submission, one review per purchase,
rating/text bounds, sanitization, reviewer != seller, per-buyer cooldown.
A review that passes Tier 1 is stored with status PENDING.

Tier 2 (AI-consensus verification): `verify_review` runs the review text
through the Equivalence Principle (gl.eq_principle) where independent
validators judge authenticity. When the review carries a product web link,
each validator also fetches that URL with gl.nondet.web and judges whether
the linked page supports the review (product exists, matches, purchasable).
Verdicts map to LIVE / DENIED / MANUAL_CHECK (inconclusive or
low-confidence consensus).

Governance: there is no human arbiter. Registration is self-service
(only the buyer of the purchase may register it), and the buyer may
appeal once from any final verdict (LIVE / DENIED / MANUAL_CHECK),
which sends the review back to PENDING for a fresh AI round with a
rotated validator set. MANUAL_CHECK is a terminal "no confident AI
consensus -> shown as unverified" state unless the buyer appeals.

Calldata rule (important): no float ever crosses a consensus or calldata
boundary. The AI's confidence (typically a 0..1 float) is converted to an
integer percentage INSIDE the nondet closure, so only plain
ints/bools/strings travel through calldata. Aggregate ratings are returned
as integers scaled by 100 (average_rating_x100) for the same reason.

Consensus rule (important): independent LLM runs almost never emit the
same confidence integer, so the Equivalence Principle compares the
DECISION, not the number - `authentic` must match and both confidences
must fall on the same side of CONFIDENCE_THRESHOLD. Comparing raw
confidence would make nearly every verify_review land UNDETERMINED.

Storage note: records are stored as JSON blobs keyed by id in TreeMaps.
This keeps the layout simple and schema-stable; a production version would
use typed storage records.
"""

from genlayer import *  # binds the lazy `gl` proxy (genlayer.gl) + TreeMap
import json
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------- constants
MIN_RATING = 1
MAX_RATING = 5
MIN_REVIEW_LEN = 20
MAX_REVIEW_LEN = 2000
COOLDOWN_SECONDS = 3600  # minimum seconds between two reviews by same buyer
MAX_URL_LEN = 500        # product web link length cap
MAX_MEDIA_LINKS = 4      # max additional media/file links per review
MAX_EVIDENCE_CHARS = 4000  # cap on fetched page evidence fed to the LLM

STATUS_PENDING = "PENDING"
STATUS_LIVE = "LIVE"
STATUS_DENIED = "DENIED"
STATUS_MANUAL = "MANUAL_CHECK"

# AI confidence at or above this integer percentage decides LIVE/DENIED;
# below it the review stays MANUAL_CHECK ("unverified") until the buyer
# appeals for a re-verification round.
CONFIDENCE_THRESHOLD = 50

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _now() -> int:
    """Current time as epoch seconds."""
    return int(datetime.now(timezone.utc).timestamp())


def _sanitize_text(text: str) -> str:
    """Strip whitespace and remove control characters from review text."""
    return _CONTROL_CHARS.sub("", text).strip()


def _sanitize_url(url: str) -> str:
    """Normalize an evidence URL: strip control chars/space, enforce http(s).

    Returns "" for empty input (web link is optional). Raises ValueError for
    a non-empty URL that is not http/https or is absurdly long - keeps the
    gate deterministic and stops javascript:/data: style payloads.
    """
    clean = _CONTROL_CHARS.sub("", url or "").strip()
    if not clean:
        return ""
    if len(clean) > MAX_URL_LEN:
        raise ValueError(f"url longer than {MAX_URL_LEN} characters")
    low = clean.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError("url must start with http:// or https://")
    if any(c in clean for c in " \t\r\n<>\"'"):
        raise ValueError("url contains illegal characters")
    return clean


def _sanitize_media_links(raw: str) -> str:
    """Validate a newline/comma separated list of extra media evidence links.

    Returns a single newline-joined string ("" when empty). Each entry must
    pass the same http(s) rule as the product web link; counts are capped.
    """
    if not (raw or "").strip():
        return ""
    parts = [p for p in re.split(r"[\n,]+", raw) if p.strip()]
    if len(parts) > MAX_MEDIA_LINKS:
        raise ValueError(f"at most {MAX_MEDIA_LINKS} media links allowed")
    cleaned = [_sanitize_url(p) for p in parts]
    return "\n".join(c for c in cleaned if c)


def _confidence_to_int(raw) -> int:
    """Normalize an AI confidence value to a clamped integer percentage.

    Accepts 0..1 floats (0.92 -> 92) and already-scaled percentages (92 ->
    92). Anything unparsable becomes 0. The float math happens entirely on
    the validator side of the nondet boundary; only the int crosses.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0
    if value <= 1.0:
        value = value * 100.0
    pct = int(round(value))
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return pct


class ReviewVerifier(gl.Contract):
    # review records: purchase_id -> JSON review record
    reviews: TreeMap[str, str]
    # product_id -> JSON list of purchase_ids that reviewed this product
    product_reviews: TreeMap[str, str]
    # buyer address (hex) -> JSON list of {"product_id","review_ts"} for cooldown
    buyer_history: TreeMap[str, str]

    def __init__(self):
        # NOTE: the TreeMaps above are auto-instantiated by the storage
        # layer and must NOT be assigned here - `self.reviews = {}` would
        # shadow them with a plain dict that never persists.
        pass

    # ------------------------------------------------------------- internal
    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    def _load_record(self, purchase_id: str) -> dict:
        self._require(purchase_id in self.reviews, "unknown purchase")
        return json.loads(self.reviews[purchase_id])

    # ------------------------------------------------- Tier 1: registration
    @gl.public.write
    def register_purchase(
        self, purchase_id: str, buyer: str, product_id: str, seller: str
    ) -> None:
        """Register a delivered purchase as eligible for review (self-service).

        Only the buyer themself may register their purchase: the tx sender
        must equal `buyer`. This keeps the flow trustless - the platform
        never needs a privileged "operator" to wire orders into the gate.
        `seller` is a hex address; Tier-1 rejects buyer == seller.
        """
        sender = gl.message.sender_address.as_hex.lower()
        self._require(len(purchase_id) > 0, "purchase_id required")
        self._require(buyer.startswith("0x"), "buyer must be a hex address")
        self._require(sender == buyer.lower(), "only the buyer may register this purchase")
        self._require(seller.startswith("0x"), "seller must be a hex address")
        self._require(buyer.lower() != seller.lower(), "buyer equals seller")
        self._require(purchase_id not in self.reviews, "purchase already registered")

        self.reviews[purchase_id] = json.dumps(
            {
                "purchase_id": purchase_id,
                "product_id": product_id,
                "buyer": buyer.lower(),
                "seller": seller.lower(),
                "rating": 0,
                "text": "",
                "web_url": "",
                "media_links": "",
                "status": STATUS_PENDING,
                "submitted_ts": 0,
                "verdict_reason": "",
                "verdict_confidence": 0,
                "appealed": False,
            }
        )

    # ------------------------------------------------- Tier 1: submission
    @gl.public.write
    def submit_review(
        self, purchase_id: str, rating: int, text: str,
        web_url: str = "", media_links: str = "",
    ) -> str:
        """Submit a review for a registered purchase. Runs the Tier 1 gate.

        `web_url` is the product page link; `media_links` is an optional
        newline/comma separated list of extra image/file evidence links.
        Both are optional, validated to http(s), and stored on the record.
        Returns the review status ("PENDING") on success.
        """
        sender = gl.message.sender_address.as_hex.lower()
        record = self._load_record(purchase_id)

        self._require(record["rating"] == 0, "purchase already reviewed")
        self._require(
            sender == record["buyer"], "only the buyer may review this purchase"
        )
        # seller != reviewer is guaranteed by registration: the buyer is
        # the submitter and registration rejects buyer == seller.
        self._require(MIN_RATING <= rating <= MAX_RATING, "rating out of range")

        clean = _sanitize_text(text)
        self._require(
            MIN_REVIEW_LEN <= len(clean) <= MAX_REVIEW_LEN,
            f"review text must be {MIN_REVIEW_LEN}-{MAX_REVIEW_LEN} characters",
        )
        # evidence links: optional, but must be well-formed http(s) if given
        clean_url = _sanitize_url(web_url)
        clean_media = _sanitize_media_links(media_links)

        # per-buyer cooldown across different products (same product is
        # exempt - one-review-per-purchase already limits that path)
        history = json.loads(self.buyer_history.get(sender, "[]"))
        now = _now()
        for entry in history:
            if entry["product_id"] != record["product_id"]:
                self._require(
                    now - entry["review_ts"] >= COOLDOWN_SECONDS,
                    "cooldown active: wait before submitting another review",
                )

        record["rating"] = rating
        record["text"] = clean
        record["web_url"] = clean_url
        record["media_links"] = clean_media
        record["status"] = STATUS_PENDING
        record["submitted_ts"] = now
        self.reviews[purchase_id] = json.dumps(record)

        history.append({"product_id": record["product_id"], "review_ts": now})
        self.buyer_history[sender] = json.dumps(history)

        ids = json.loads(self.product_reviews.get(record["product_id"], "[]"))
        if purchase_id not in ids:
            ids.append(purchase_id)
        self.product_reviews[record["product_id"]] = json.dumps(ids)

        return STATUS_PENDING

    # ------------------------------------------------- Tier 2: AI verdict
    @gl.public.write
    def verify_review(self, purchase_id: str) -> str:
        """Run Tier 2 AI-consensus verification on a PENDING review.

        Uses the Equivalence Principle: validators independently judge the
        review text and must agree on the verdict for consensus.
        """
        record = self._load_record(purchase_id)
        self._require(record["rating"] > 0, "review not submitted yet")
        self._require(
            record["status"] == STATUS_PENDING,
            "review is not awaiting verification",
        )

        review_text = record["text"]
        rating = record["rating"]
        product_id = record["product_id"]
        web_url = record.get("web_url", "")
        media_links = record.get("media_links", "")

        def _fetch_evidence(url: str) -> str:
            """Fetch one evidence URL for the verdict prompt.

            Runs independently on each validator (inside the nondet
            closure), so consensus sees each node's own live fetch. Uses
            web.render(mode="text") so JS-rendered product pages resolve;
            falls back to a plain GET. Any failure (timeout, 4xx/5xx,
            bot block) degrades to an empty string instead of raising, so
            one bad fetch never breaks the whole consensus round.
            """
            try:
                page = gl.nondet.web.render(url, mode="text")
                text = str(page.get("text", "") or page.get("html", ""))
            except Exception:
                try:
                    resp = gl.nondet.web.get(url)
                    text = str(resp.get("body", "") or "")
                except Exception:
                    return ""
            text = _CONTROL_CHARS.sub(" ", text)
            text = re.sub(r"[ \t]+", " ", text).strip()
            return text[:MAX_EVIDENCE_CHARS]

        def get_verdict() -> dict:
            # Each validator fetches the linked page independently before
            # judging - this is the evidence half of the verdict.
            page_evidence = ""
            if web_url:
                page_evidence = _fetch_evidence(web_url)
            media_evidence = []
            for link in media_links.split("\n") if media_links else []:
                if link:
                    fetched = _fetch_evidence(link)
                    if fetched:
                        media_evidence.append(f"[{link}]\n{fetched}")

            evidence_block = page_evidence or "(no page content fetched)"
            media_block = "\n\n".join(media_evidence) or "(none)"

            prompt = f"""You are a fake-review detector for an online marketplace.
Analyze the following product review and decide whether it looks like a
genuine, specific, experience-based review or a fake/incentivized/spam one.

Product: {product_id}
Rating given: {rating}/5
Review text:
---
{review_text}
---

Product web link (fetched live by you just now):
{web_url or "(none provided)"}
Fetched page content:
---
{evidence_block}
---

Additional media/file links and their fetched content:
{media_block}

Judging guidance:
- If a product web link was provided, check that the linked page actually
  exists, is about this product, and looks like a real listing. A dead
  link, a mismatched product, or an obviously fake page lowers confidence.
- If no link was provided, judge only from the review text.
- Prefer stable facts (product exists, price range, category match) over
  volatile page details (timestamps, stock counts) since those change
  between fetches.

Respond using ONLY the following JSON format:
{{
"authentic": bool,
"confidence": number between 0 and 1,
"reason": str
}}
It is mandatory that you respond only using the JSON format above,
nothing else. This result should be perfectly parseable by a JSON parser
without errors."""
            raw = str(gl.nondet.exec_prompt(prompt))
            raw = raw.replace("```json", "").replace("```", "")
            first = raw.find("{")
            last = raw.rfind("}")
            parsed = None
            if first >= 0 and last > first:
                try:
                    parsed = json.loads(raw[first : last + 1])
                except Exception:
                    parsed = None
            if not isinstance(parsed, dict):
                # A malformed generation must degrade to the same
                # deterministic fallback on every validator instead of
                # raising and killing the consensus round.
                m_auth = re.search(r'"authentic"\s*:\s*(true|false)', raw, re.I)
                m_conf = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', raw)
                return {
                    "authentic": bool(m_auth) and m_auth.group(1).lower() == "true",
                    "confidence": _confidence_to_int(m_conf.group(1)) if m_conf else 0,
                    "reason": "model output was not valid JSON",
                }
            # Calldata-safe sanitization BEFORE the result crosses the
            # consensus boundary: floats are not calldata encodable, so
            # confidence becomes a clamped int percentage here.
            return {
                "authentic": bool(parsed.get("authentic", False)),
                "confidence": _confidence_to_int(parsed.get("confidence", 0)),
                "reason": str(parsed.get("reason", ""))[:300],
            }

        verdict = gl.eq_principle.prompt_comparative(
            get_verdict,
            "Both outputs must reach the same decision. The `authentic` "
            "boolean must be identical. The `confidence` numbers do NOT "
            "need to be equal - they are equivalent as long as both are "
            f"at or above {CONFIDENCE_THRESHOLD}, or both are below "
            f"{CONFIDENCE_THRESHOLD}. The `reason` wording may differ "
            "freely.",
        )

        if not hasattr(verdict, "get"):
            return self._apply_verdict(
                purchase_id, record, STATUS_MANUAL, "ai: no verdict returned", 0
            )

        authentic = bool(verdict.get("authentic", False))
        confidence = int(verdict.get("confidence", 0))
        reason = str(verdict.get("reason", ""))[:300]

        if confidence >= CONFIDENCE_THRESHOLD:
            new_status = STATUS_LIVE if authentic else STATUS_DENIED
        else:
            new_status = STATUS_MANUAL

        return self._apply_verdict(
            purchase_id, record, new_status, f"ai: {reason}", confidence
        )

    def _apply_verdict(
        self,
        purchase_id: str,
        record: dict,
        status: str,
        reason: str,
        confidence: int = 0,
    ) -> str:
        record["status"] = status
        record["verdict_reason"] = reason
        record["verdict_confidence"] = int(confidence)
        self.reviews[purchase_id] = json.dumps(record)
        return status

    # --------------------------------------------------------- governance
    @gl.public.write
    def appeal_review(self, purchase_id: str, reason: str) -> str:
        """Buyer appeal: send a finished review back to PENDING for one more
        AI round (rotated validators). Works from any final verdict
        (LIVE / DENIED / MANUAL_CHECK) and is allowed only once, only to
        the buyer. There is no human override in this contract.
        """
        sender = gl.message.sender_address.as_hex.lower()
        record = self._load_record(purchase_id)
        self._require(sender == record["buyer"], "only the reviewer may appeal")
        self._require(
            record["status"] in (STATUS_LIVE, STATUS_DENIED, STATUS_MANUAL),
            "only finished reviews can be appealed",
        )
        self._require(not record["appealed"], "review already appealed once")
        record["appealed"] = True
        record["verdict_confidence"] = 0
        return self._apply_verdict(
            purchase_id, record, STATUS_PENDING, f"appeal: {reason[:200]}"
        )

    # -------------------------------------------------------------- views
    @gl.public.view
    def get_review(self, purchase_id: str) -> dict:
        return self._load_record(purchase_id)

    @gl.public.view
    def get_review_safe(self, purchase_id: str) -> dict:
        """Non-reverting lookup: {} when the purchase is not registered.

        The order book calls this for every marketplace order, so it must
        never raise on the common "not registered yet" case.
        """
        if purchase_id not in self.reviews:
            return {}
        return json.loads(self.reviews[purchase_id])

    @gl.public.view
    def review_ids(self) -> list:
        """All registered purchase ids (batch lookup for the order book)."""
        return [k for k, _ in self.reviews.items()]

    @gl.public.view
    def get_product_reviews(self, product_id: str) -> list:
        ids = json.loads(self.product_reviews.get(product_id, "[]"))
        return [json.loads(self.reviews[pid]) for pid in ids]

    @gl.public.view
    def product_score(self, product_id: str) -> dict:
        """Aggregate score over LIVE reviews only (fake reviews never count).

        average_rating_x100 is an integer (e.g. 450 for 4.5 stars) because
        floats are not calldata encodable.
        """
        live = [
            r
            for r in self.get_product_reviews(product_id)
            if r["status"] == STATUS_LIVE
        ]
        count = len(live)
        avg_x100 = (sum(r["rating"] for r in live) * 100 // count) if count else 0
        return {
            "product_id": product_id,
            "live_reviews": count,
            "average_rating_x100": avg_x100,
        }

    @gl.public.view
    def is_review_allowed(self, purchase_id: str) -> bool:
        """Tier 1 dry-run: would this purchase currently pass the gate?"""
        if purchase_id not in self.reviews:
            return False
        record = json.loads(self.reviews[purchase_id])
        return record["rating"] == 0 and record["status"] == STATUS_PENDING

    @gl.public.view
    def stats(self) -> dict:
        total = live = denied = manual = pending = 0
        for _, blob in self.reviews.items():
            status = json.loads(blob)["status"]
            total += 1
            if status == STATUS_LIVE:
                live += 1
            elif status == STATUS_DENIED:
                denied += 1
            elif status == STATUS_MANUAL:
                manual += 1
            else:
                pending += 1
        return {
            "total_reviews": total,
            "live": live,
            "denied": denied,
            "manual_check": manual,
            "pending": pending,
        }

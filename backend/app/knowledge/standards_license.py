"""Decide what ResearchDataHub may do with each standard (JIS/ISO/IEC/...).

The policy is fail-closed. A standard whose licence has not been reviewed is
indexed by its public metadata (designation, title, edition) only: the body is
not stored on the shared server, not embedded and not given to any LLM.

Two independent axes are decided per document:

* storage  -- where the full text may live:
    ``metadata_only``  number/title/edition/clause numbers only (facts, not the text)
    ``personal_copy``  the licence allows one personal copy, but not a LAN server
    ``shared_server``  the text may be kept on the ResearchDataHub server
* ai_use   -- what kind of AI processing the text may receive:
    ``prohibited``     no embedding, no LLM input (local or cloud)
    ``local_only``     embedding and verified local-only LLMs, no cloud LLM
    ``allowed``        cloud LLMs are also allowed by the licence

Embedding counts as AI processing. Some licences name "vectorization" and
"embedding" explicitly, and ISO's terms cover "enabling an AI system to ingest".

The publisher table below is a *ceiling* for ordinary purchase/viewing
channels. A per-document record can only go above it with a written
permission or an organisation contract that is referenced and reviewed.

This module is not legal advice. It encodes how the project decides, and it
records why, so that a person can check the decision later. See
docs/standards-license-policy.md (moved from ResearchDataHub) for the method and the sources.

Run ``python -m app.knowledge.standards_license "JIS C 61000-4-2:2012"`` to see
the decision and its reasons for a designation.
"""
import datetime
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

STORAGE_LEVELS = ("metadata_only", "personal_copy", "shared_server")
AI_LEVELS = ("prohibited", "local_only", "allowed")

# How the copy in hand was obtained. The channel decides which terms apply.
INSTRUMENTS = {
    "purchase": "Store purchase under the publisher's/distributor's end-user licence",
    "free_view": "Free online viewing (e.g. JISC viewer, ISO OBP, no-cost access)",
    "open_publication": "Publisher states the document may be copied/redistributed",
    "organisation_contract": "Organisation subscription/licence contract (terms of that contract apply)",
    "written_permission": "Individual written permission from the rights holder",
    "public_law": "Statute, ministerial ordinance, public notice or circular (Copyright Act Art. 13)",
    "user_authored": "Notes written by the user that do not reproduce the standard's text",
    "unknown": "Channel not recorded",
}

# Operations ResearchDataHub/ControlDeck can perform on a document's content.
OPERATIONS = ("metadata_index", "fulltext_store", "fulltext_search", "embed", "local_llm", "cloud_llm")

# Re-check licences at least yearly: several publishers changed AI terms in 2025-2026.
REVIEW_MAX_AGE_DAYS = 365


@dataclass(frozen=True)
class PublisherTerms:
    """Snapshot of one publisher's terms as checked by a person."""
    key: str
    name: str
    storage: str
    ai_use: str
    terms_url: str
    terms_version: str
    checked_on: str
    verified_primary: bool
    finding: str


# checked_on = date a person read the terms. verified_primary=False means only a
# secondary source (library guide, reseller page) was read: treat as provisional.
PUBLISHERS: Dict[str, PublisherTerms] = {t.key: t for t in (
    PublisherTerms(
        "iso", "ISO", "personal_copy", "prohibited",
        "https://www.iso.org/terms-conditions-licence-agreement.html",
        "ISO Customer Licence Agreement / OBP Terms of Use / No-Cost CLA 2026-09-18",
        "2026-11-19", False,
        "Licensee may not access, use or process the ISO publication for machine learning, AI or "
        "analogous computational purposes unless ISO, a member body or an authorised distributor "
        "expressly permits it in a separate licence. OBP terms also bar enabling an AI system to "
        "ingest, summarise or restate ISO content, and ISO opts out of statutory TDM exceptions. "
        "Read via search excerpts; iso.org returned HTTP 403 to automated fetch."),
    PublisherTerms(
        "iec", "IEC", "personal_copy", "prohibited",
        "https://webstore.iec.ch/en/terms-conditions",
        "IEC Webstore Terms & Conditions, last updated 2025-08-04",
        "2026-11-19", True,
        "Clause 3: IEC Products may not be used for the development or operation of a machine "
        "learning or AI system, and may not be introduced into public or privately licensed AI "
        "tools without IEC's express permission. Clause 2 bars copying/transmitting without "
        "written permission."),
    PublisherTerms(
        "jsa", "JSA Webdesk (JIS and JSA-distributed ISO/IEC)", "personal_copy", "prohibited",
        "https://webdesk.jsa.or.jp/common/W10K0030/?post_type=book_common&page_id=b_j_yakkan",
        "JSA Webdesk 電子書籍配信サービス及びネット通販サービス利用個別規約 2026-10-01",
        "2026-11-19", True,
        "第2条第2項: 1台のコンピュータに限り利用。第2条第3項: LAN又はその他のネットワークでの利用、"
        "複製・配布、いかなる形態のデジタルツール又はAIツールへの入力、AIによる二次的著作物の作成を禁止。"
        "第5条第4項: 規格等も同様にAIツールへの入力を禁止。バックアップ1部と紙コピー1部のみ可。"),
    PublisherTerms(
        "jisc", "JISC online viewer", "metadata_only", "prohibited",
        "https://www.jisc.go.jp/",
        "JISC 規格閲覧（閲覧のみ・印刷/保存不可）",
        "2026-11-19", False,
        "Current JIS can be viewed on the JISC site but printing, saving and downloading are not "
        "permitted (library guides from Shizuoka/Osaka prefectural and Tohoku University). "
        "Do not retrieve it automatically."),
    PublisherTerms(
        "astm", "ASTM International", "personal_copy", "prohibited",
        "https://www.astm.org/astm-license-agreement",
        "ASTM License Agreement (Web & Compass) Ver 2025.06.26",
        "2026-11-19", True,
        "The ASTM Documents and their content may not be used in any way with any form of "
        "artificial intelligence (generative AI, machine learning, etc.). AI derivatives need "
        "written permission from ASTM's President. A 2026.01.23 version exists and was not read."),
    PublisherTerms(
        "ieee", "IEEE", "personal_copy", "prohibited",
        "https://standards.ieee.org/",
        "IEEE Xplore subscriber terms (secondary source only)",
        "2026-11-19", False,
        "A university library guide reproduces IEEE terms barring supplying IEEE content to AI "
        "systems and use that could advance an AI/LLM. IEEE's own current text was not read."),
    PublisherTerms(
        "itu", "ITU-T", "shared_server", "prohibited",
        "https://www.itu.int/en/ITU-T/publications/Pages/recs.aspx",
        "ITU-T Recommendations free download",
        "2026-11-19", False,
        "Most published Recommendations are free and may be copied and redistributed without "
        "modification. AI use is not addressed in what was read, so AI stays prohibited until a "
        "reviewer records a decision. Common ITU-T | ISO/IEC texts follow ISO/IEC terms."),
    PublisherTerms(
        "ietf", "IETF (RFC)", "shared_server", "prohibited",
        "https://trustee.ietf.org/",
        "IETF Trust Legal Provisions (not read in this review)",
        "2026-11-19", False,
        "RFCs are generally copyable under the IETF Trust Legal Provisions, but the current text "
        "was not read (fetch returned 404). AI stays prohibited until a reviewer records it."),
)}

# Designation prefixes -> publisher key. Order matters: the first match wins.
_DESIGNATION_RULES: Tuple[Tuple[str, str], ...] = (
    (r"^JIS\s*[A-Z]\b|^JIS[A-Z]\d", "jsa"),
    (r"^ISO\s*/\s*IEC\b", "iso"),            # joint JTC 1 texts: ISO and IEC terms both apply; ISO is checked first
    (r"^IEC\s*/\s*IEEE\b", "iec"),
    (r"^(IEC|CISPR)\b", "iec"),
    (r"^ISO\b", "iso"),
    (r"^ASTM\b", "astm"),
    (r"^IEEE\b", "ieee"),
    (r"^ITU-?[TR]\b", "itu"),
    (r"^RFC\s*\d", "ietf"),
)


def identify_publisher(designation: str) -> Optional[str]:
    """Return the publisher key for a designation such as 'IEC 61000-4-2:2008', or None."""
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", designation or "").strip().upper())
    for pattern, key in _DESIGNATION_RULES:
        if re.search(pattern, text):
            return key
    return None


@dataclass
class LicenseRecord:
    """What a person recorded about one copy of a standard."""
    designation: str
    instrument: str = "unknown"
    publisher: Optional[str] = None
    storage: Optional[str] = None
    ai_use: Optional[str] = None
    permission_ref: str = ""
    reviewer: str = ""
    reviewed_on: str = ""
    terms_version: str = ""
    note: str = ""


@dataclass
class Decision:
    designation: str
    publisher: Optional[str]
    instrument: str
    storage: str
    ai_use: str
    operations: FrozenSet[str]
    needs_review: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"designation": self.designation, "publisher": self.publisher,
                "instrument": self.instrument, "storage": self.storage, "ai_use": self.ai_use,
                "operations": sorted(self.operations), "needs_review": self.needs_review,
                "reasons": list(self.reasons)}


def _rank(levels: Tuple[str, ...], value: str) -> int:
    return levels.index(value)


def _min(levels: Tuple[str, ...], a: str, b: str) -> str:
    return a if _rank(levels, a) <= _rank(levels, b) else b


def operations_for(storage: str, ai_use: str) -> FrozenSet[str]:
    """Map the two axes to concrete operations on the shared ResearchDataHub server."""
    ops = {"metadata_index"}
    if storage == "shared_server":
        ops |= {"fulltext_store", "fulltext_search"}
        if ai_use in ("local_only", "allowed"):
            ops |= {"embed", "local_llm"}
        if ai_use == "allowed":
            ops.add("cloud_llm")
    return frozenset(ops)


def _parse_date(value: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _valid(levels: Tuple[str, ...], value: Optional[str]) -> bool:
    return value in levels


def evaluate(record: LicenseRecord, today: Optional[datetime.date] = None) -> Decision:
    """Return the effective storage/AI decision with human-readable reasons."""
    today = today or datetime.date.today()
    reasons: List[str] = []
    instrument = record.instrument if record.instrument in INSTRUMENTS else "unknown"
    publisher = record.publisher or identify_publisher(record.designation)
    terms = PUBLISHERS.get(publisher or "")
    needs_review = False

    def closed(why: str) -> Decision:
        reasons.append(why)
        return Decision(record.designation, publisher, instrument, "metadata_only", "prohibited",
                        operations_for("metadata_only", "prohibited"), True, reasons)

    if instrument == "unknown":
        return closed("入手経路が記録されていないため、メタデータのみとする（fail-closed）")

    if instrument == "public_law":
        reasons.append("法令・告示・通達は著作権法第13条により権利の目的とならない。解説書や注釈は対象外")
        return Decision(record.designation, publisher, instrument, "shared_server", "allowed",
                        operations_for("shared_server", "allowed"), False, reasons)

    if instrument == "user_authored":
        reasons.append("利用者自身のメモ。規格本文の転記を含まないことが前提。機密扱いは別の機密区分で判定")
        return Decision(record.designation, publisher, instrument, "shared_server", "allowed",
                        operations_for("shared_server", "allowed"), False, reasons)

    reviewed = _parse_date(record.reviewed_on)
    if instrument in ("organisation_contract", "written_permission"):
        if not record.permission_ref or not record.reviewer or reviewed is None:
            return closed("契約・許諾の参照番号、確認者、確認日のいずれかが無い")
        if not (_valid(STORAGE_LEVELS, record.storage) and _valid(AI_LEVELS, record.ai_use)):
            return closed("契約で認められた保存範囲・AI利用範囲が記録されていない")
        storage, ai_use = record.storage, record.ai_use
        reasons.append(f"契約・個別許諾（{record.permission_ref}）に記録された範囲を適用")
    else:
        if terms is None:
            return closed("発行団体が判別できない、または規約が未調査")
        storage, ai_use = terms.storage, terms.ai_use
        reasons.append(f"{terms.name} の規約（{terms.terms_version}、{terms.checked_on} 確認）: {terms.finding}")
        if not terms.verified_primary:
            needs_review = True
            reasons.append("規約は二次資料でのみ確認。原文を読んで verified_primary を更新すること")
        if instrument == "free_view":
            storage = "metadata_only"
            reasons.append("無料閲覧は閲覧のみの許諾。サーバーへの本文保存はしない")
        # A record may restrict further but never exceed the publisher ceiling.
        if _valid(STORAGE_LEVELS, record.storage):
            if _rank(STORAGE_LEVELS, record.storage) > _rank(STORAGE_LEVELS, storage):
                reasons.append("記録された保存範囲は規約の上限を超えるため、規約の上限に下げた")
            storage = _min(STORAGE_LEVELS, storage, record.storage)
        if _valid(AI_LEVELS, record.ai_use):
            if _rank(AI_LEVELS, record.ai_use) > _rank(AI_LEVELS, ai_use):
                reasons.append("記録されたAI利用範囲は規約の上限を超えるため、規約の上限に下げた")
            ai_use = _min(AI_LEVELS, ai_use, record.ai_use)
        if (record.terms_version and record.terms_version != terms.terms_version
                and (reviewed is None or reviewed.isoformat() < terms.checked_on)):
            needs_review = True
            reasons.append("記録時の規約版と現在の規約スナップショットが異なる")

    if reviewed is not None and (today - reviewed).days > REVIEW_MAX_AGE_DAYS:
        needs_review = True
        if ai_use != "prohibited":
            ai_use = "prohibited"
            reasons.append(f"確認から{REVIEW_MAX_AGE_DAYS}日を超えたため、再確認までAI利用を停止")
    if terms is not None and (today - _parse_date(terms.checked_on)).days > REVIEW_MAX_AGE_DAYS:
        needs_review = True
        reasons.append("発行団体の規約スナップショットが古い。規約の再確認が必要")

    if storage == "personal_copy":
        reasons.append("規約上は個人の1部保存まで。LAN上の共有サーバー（ResearchDataHub）には本文を置かない")
    return Decision(record.designation, publisher, instrument, storage, ai_use,
                    operations_for(storage, ai_use), needs_review, reasons)


def allowed(decision: Decision, operation: str) -> bool:
    """True only for a known operation that the decision permits."""
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation: {operation}")
    return operation in decision.operations


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m app.knowledge.standards_license DESIGNATION [INSTRUMENT]")
        print("instruments: " + ", ".join(INSTRUMENTS))
        return 0 if argv else 2
    record = LicenseRecord(designation=argv[0], instrument=argv[1] if len(argv) > 1 else "purchase")
    print(json.dumps(evaluate(record).to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

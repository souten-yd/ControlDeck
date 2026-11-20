"""Purpose: keep standards licence decisions fail-closed and explainable.

Basis: docs/standards-license-policy.md (moved from ResearchDataHub on 2026-11-19 when it was merged into ControlDeck). ISO/IEC/JSA/ASTM terms checked on 2026-11-19
forbid AI input of purchased standards; JSA also forbids LAN/network use. A wrong
"allowed" here would send licensed text to embeddings or LLMs, so every path that can
open AI use is pinned by a test.
"""
import datetime
import unittest

from app.knowledge import standards_license as sl

TODAY = datetime.date(2026, 11, 19)


def decide(designation, instrument="purchase", **kw):
    return sl.evaluate(sl.LicenseRecord(designation=designation, instrument=instrument, **kw), today=TODAY)


class IdentifyPublisherTest(unittest.TestCase):
    def test_common_designations(self):
        cases = {
            "JIS C 61000-4-2:2012": "jsa", "JISC61000-4-2": "jsa", "ＪＩＳ　Ｂ　１１９４": "jsa",
            "IEC 61000-4-2:2008": "iec", "CISPR 32:2015": "iec", "IEC/IEEE 60255-118-1": "iec",
            "ISO 9001:2015": "iso", "ISO/IEC 27001:2022": "iso",
            "ASTM D638-14": "astm", "IEEE Std 802.3-2022": "ieee",
            "ITU-T G.709": "itu", "RFC 9110": "ietf",
        }
        for designation, key in cases.items():
            self.assertEqual(sl.identify_publisher(designation), key, designation)

    def test_unknown_designation(self):
        self.assertIsNone(sl.identify_publisher("UL 1741"))
        self.assertIsNone(sl.identify_publisher(""))


class FailClosedTest(unittest.TestCase):
    def test_unknown_channel_is_metadata_only(self):
        d = decide("IEC 61000-4-2:2008", "unknown")
        self.assertEqual((d.storage, d.ai_use), ("metadata_only", "prohibited"))
        self.assertEqual(d.operations, frozenset({"metadata_index"}))
        self.assertTrue(d.needs_review)

    def test_unknown_publisher_is_metadata_only(self):
        d = decide("UL 1741")
        self.assertEqual(d.operations, frozenset({"metadata_index"}))

    def test_purchased_iso_iec_jis_astm_never_reach_ai_or_server(self):
        for designation in ("ISO 9001:2015", "IEC 61000-4-2:2008", "JIS C 61000-4-2:2012", "ASTM D638-14"):
            d = decide(designation)
            for op in ("fulltext_store", "embed", "local_llm", "cloud_llm"):
                self.assertFalse(sl.allowed(d, op), (designation, op))
            self.assertTrue(sl.allowed(d, "metadata_index"))

    def test_record_cannot_exceed_publisher_ceiling(self):
        d = decide("IEC 61000-4-2:2008", storage="shared_server", ai_use="allowed",
                   reviewer="owner", reviewed_on="2026-11-19")
        self.assertEqual((d.storage, d.ai_use), ("personal_copy", "prohibited"))
        self.assertTrue(any("上限" in r for r in d.reasons))

    def test_free_view_never_stores_text(self):
        d = decide("ITU-T G.709", "free_view")
        self.assertEqual(d.storage, "metadata_only")

    def test_unknown_operation_raises(self):
        with self.assertRaises(ValueError):
            sl.allowed(decide("ISO 9001"), "train")


class PermissionTest(unittest.TestCase):
    def test_contract_without_reference_is_closed(self):
        d = decide("ISO 9001:2015", "organisation_contract", storage="shared_server",
                   ai_use="local_only", reviewer="owner", reviewed_on="2026-11-19")
        self.assertEqual(d.ai_use, "prohibited")

    def test_contract_with_reference_grants_recorded_scope(self):
        d = decide("ISO 9001:2015", "written_permission", storage="shared_server", ai_use="local_only",
                   permission_ref="JSA-AI-2026-001", reviewer="owner", reviewed_on="2026-11-19")
        self.assertTrue(sl.allowed(d, "embed"))
        self.assertTrue(sl.allowed(d, "local_llm"))
        self.assertFalse(sl.allowed(d, "cloud_llm"))
        self.assertFalse(d.needs_review)

    def test_stale_review_stops_ai(self):
        d = decide("ISO 9001:2015", "written_permission", storage="shared_server", ai_use="allowed",
                   permission_ref="X-1", reviewer="owner", reviewed_on="2025-01-01")
        self.assertEqual(d.ai_use, "prohibited")
        self.assertTrue(d.needs_review)
        self.assertTrue(sl.allowed(d, "fulltext_search"))


class PublicSourcesTest(unittest.TestCase):
    def test_public_law_is_allowed(self):
        d = decide("電気設備の技術基準の解釈", "public_law")
        self.assertTrue(sl.allowed(d, "cloud_llm"))

    def test_open_publication_stores_but_ai_waits_for_review(self):
        d = decide("RFC 9110", "open_publication")
        self.assertTrue(sl.allowed(d, "fulltext_store"))
        self.assertFalse(sl.allowed(d, "embed"))
        self.assertTrue(d.needs_review)


class SnapshotTest(unittest.TestCase):
    def test_every_snapshot_is_well_formed(self):
        for key, t in sl.PUBLISHERS.items():
            self.assertEqual(key, t.key)
            self.assertIn(t.storage, sl.STORAGE_LEVELS)
            self.assertIn(t.ai_use, sl.AI_LEVELS)
            self.assertTrue(t.terms_url.startswith("https://"))
            datetime.date.fromisoformat(t.checked_on)
            self.assertTrue(t.finding)

    def test_no_snapshot_allows_ai_by_default(self):
        # Changing this needs a primary-source review recorded in docs/standards-license-policy.md.
        self.assertEqual({t.ai_use for t in sl.PUBLISHERS.values()}, {"prohibited"})


if __name__ == "__main__":
    unittest.main()

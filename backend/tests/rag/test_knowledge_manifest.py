from __future__ import annotations

import unittest

from backend.scripts.ingest_knowledge import ManifestError, parse_manifest


class KnowledgeManifestTests(unittest.TestCase):
    def test_parses_traceable_documents_with_stable_source_id(self) -> None:
        documents = parse_manifest(
            '{"source":"kb://guides/charging","title":"Charging guide",'
            '"document_type":"text","content":"Clean contacts.","version":"v1"}\n'
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].source, "kb://guides/charging")
        self.assertEqual(documents[0].version, "v1")
        self.assertRegex(documents[0].document_id, r"^[0-9a-f-]{36}$")

    def test_rejects_non_traceable_or_invalid_records(self) -> None:
        with self.assertRaisesRegex(ManifestError, "source is required"):
            parse_manifest('{"title":"Guide","content":"text"}')
        with self.assertRaisesRegex(ManifestError, "invalid JSON"):
            parse_manifest("not json")

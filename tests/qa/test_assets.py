"""Keep the implementation-independent QA scenario assets valid and complete."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA_ASSETS = (
    ROOT / "tests" / "qa" / "contract-matrix.json",
    ROOT / "tests" / "qa" / "sse-sequences.json",
    ROOT / "tests" / "qa" / "state-and-rag-gates.json",
    ROOT / "tests" / "apifox" / "scenarios" / "scenario-manifest-v1.json",
    ROOT / "tests" / "apifox" / "data" / "environment.example.json",
)


class QaAssetTest(unittest.TestCase):
    def test_assets_are_valid_json(self) -> None:
        for asset in QA_ASSETS:
            with self.subTest(asset=asset):
                self.assertTrue(asset.is_file())
                with asset.open(encoding="utf-8") as handle:
                    json.load(handle)

    def test_contract_matrix_covers_blocking_domains(self) -> None:
        matrix = json.loads(QA_ASSETS[0].read_text(encoding="utf-8"))
        case_ids = {case["id"] for case in matrix["cases"]}
        required_case_ids = {
            "API-CHAT-002",
            "API-MEMORY-002",
            "API-REVIEW-002",
            "API-ERROR-001",
        }
        self.assertTrue(required_case_ids <= case_ids)

    def test_sse_matrix_covers_all_discriminated_events(self) -> None:
        sse = json.loads(QA_ASSETS[1].read_text(encoding="utf-8"))
        expected_event_types = {
            "meta",
            "status",
            "tool_start",
            "tool_end",
            "delta",
            "citation",
            "review_required",
            "done",
            "error",
        }
        self.assertEqual(
            set(sse["event_types"]),
            expected_event_types,
        )

    def test_apifox_manifest_keeps_required_black_box_flows(self) -> None:
        manifest = json.loads(QA_ASSETS[3].read_text(encoding="utf-8"))
        case_ids = {scenario["id"] for scenario in manifest["scenarios"]}
        self.assertTrue({f"APIFOX-{number:03d}" for number in range(1, 10)} <= case_ids)


if __name__ == "__main__":
    unittest.main()

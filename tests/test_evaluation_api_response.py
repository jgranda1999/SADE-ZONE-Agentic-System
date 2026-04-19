"""Unit tests for evaluation API response mapping."""

import json
import unittest
from pathlib import Path

from evaluation_api_response import (
    build_processing_failed_response,
    normalize_evidence_requirement_spec,
    parse_constraint_to_api_item,
    to_evaluation_api_payload,
    utc_now_z,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_integration_decision_visibility(rel_path: str) -> dict:
    """Extract the JSON object after 'Full output' from an integration result file."""
    text = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    idx = text.find("Full output (decision + visibility):")
    if idx < 0:
        raise ValueError(f"No Full output section in {rel_path}")
    brace = text.find("{", idx)
    if brace < 0:
        raise ValueError("No JSON object start")
    # Find matching closing brace for first object (simple brace depth)
    depth = 0
    end = brace
    for i, ch in enumerate(text[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return json.loads(text[brace:end])


class ParseConstraintTests(unittest.TestCase):
    def test_max_altitude(self):
        self.assertEqual(
            parse_constraint_to_api_item("MAX_ALTITUDE(30m)"),
            {"code": "MAX_ALTITUDE_M", "value": 30},
        )

    def test_speed_limit(self):
        self.assertEqual(
            parse_constraint_to_api_item("SPEED_LIMIT(7m/s)"),
            {"code": "SPEED_LIMIT_MPS", "value": 7},
        )

    def test_payload_margin(self):
        self.assertEqual(
            parse_constraint_to_api_item("PAYLOAD_MARGIN_CAUTION"),
            {"code": "PAYLOAD_MARGIN_CAUTION", "value": True},
        )

    def test_daylight_only(self):
        self.assertEqual(
            parse_constraint_to_api_item("DAYLIGHT_ONLY"),
            {"code": "DAYLIGHT_ONLY", "value": True},
        )

    def test_raw_fallback(self):
        self.assertEqual(
            parse_constraint_to_api_item("UNKNOWN_CONSTRAINT(1)"),
            {"code": "RAW", "value": "UNKNOWN_CONSTRAINT(1)"},
        )


class NormalizeEvidenceSpecTests(unittest.TestCase):
    def test_sets_request_id(self):
        spec = {
            "type": "EVIDENCE_REQUIREMENT",
            "spec_version": "1.0",
            "request_id": "ACT-b80eac98-e26b-4988-9179-c4e84fc4530f",
            "subject": {
                "sade_zone_id": "zone-001",
                "pilot_id": "pilot-001",
                "organization_id": "org-001",
                "drone_id": "drone-001",
            },
            "categories": [],
        }
        eid = "b80eac98-e26b-4988-9179-c4e84fc4530f"
        out = normalize_evidence_requirement_spec(spec, eid)
        self.assertEqual(out["request_id"], eid)
        self.assertEqual(out["type"], "EVIDENCE_REQUIREMENT")

    def test_none_returns_none(self):
        self.assertIsNone(normalize_evidence_requirement_spec(None, "x"))


class ToEvaluationApiPayloadTests(unittest.TestCase):
    _EID = "b80eac98-e26b-4988-9179-c4e84fc4530f"
    _SID = "f17f4eab-35e6-4d2a-a802-1e00e51ade3d"
    _FIXED_TIME = "2026-03-27T21:19:48Z"

    def test_approved_from_integration_file(self):
        data = _load_integration_decision_visibility("results/local-integration/entry_result_1.txt")
        out = to_evaluation_api_payload(
            data,
            self._EID,
            self._SID,
            completed_at=self._FIXED_TIME,
        )
        self.assertEqual(out["evaluation_id"], self._EID)
        self.assertEqual(out["evaluation_series_id"], self._SID)
        self.assertEqual(out["completed_at"], self._FIXED_TIME)
        r = out["result"]
        self.assertEqual(r["decision"], "APPROVED")
        self.assertIn("Environment Agent", r["reason"])
        self.assertEqual(r["constraints"], [])
        self.assertIsNone(r["evidence_requirement_spec"])

    def test_approved_constraints_from_integration_file(self):
        data = _load_integration_decision_visibility(
            "results/local-integration/entry_result_accept_with_constraints.txt"
        )
        out = to_evaluation_api_payload(
            data,
            self._EID,
            self._SID,
            completed_at=self._FIXED_TIME,
        )
        r = out["result"]
        self.assertEqual(r["decision"], "APPROVED_CONSTRAINTS")
        self.assertEqual(
            r["constraints"],
            [
                {"code": "SPEED_LIMIT_MPS", "value": 7},
                {"code": "MAX_ALTITUDE_M", "value": 30},
                {"code": "PAYLOAD_MARGIN_CAUTION", "value": True},
            ],
        )

    def test_action_required_request_id_normalized(self):
        data = _load_integration_decision_visibility(
            "results/local-integration/entry_result_action_required.txt"
        )
        out = to_evaluation_api_payload(
            data,
            self._EID,
            self._SID,
            completed_at=self._FIXED_TIME,
        )
        r = out["result"]
        self.assertEqual(r["decision"], "ACTION_REQUIRED")
        spec = r["evidence_requirement_spec"]
        self.assertIsNotNone(spec)
        self.assertEqual(spec["request_id"], self._EID)
        self.assertEqual(spec["type"], "EVIDENCE_REQUIREMENT")
        self.assertIn("categories", spec)

    def test_denied_from_integration_file(self):
        data = _load_integration_decision_visibility("results/local-integration/entry_result_deny.txt")
        out = to_evaluation_api_payload(
            data,
            self._EID,
            self._SID,
            completed_at=self._FIXED_TIME,
        )
        r = out["result"]
        self.assertEqual(r["decision"], "DENIED")
        self.assertIn("Denied", r["reason"])
        self.assertIsNone(r["evidence_requirement_spec"])

    def test_unknown_decision_type_raises(self):
        with self.assertRaises(ValueError):
            to_evaluation_api_payload(
                {"decision": {"type": "INVALID", "explanation": "x"}},
                self._EID,
                self._SID,
                completed_at=self._FIXED_TIME,
            )


class ProcessingFailedTests(unittest.TestCase):
    def test_shape(self):
        out = build_processing_failed_response(
            "b80eac98-e26b-4988-9179-c4e84fc4530f",
            "f17f4eab-35e6-4d2a-a802-1e00e51ade3d",
            completed_at="2026-03-27T21:19:48Z",
        )
        self.assertTrue(out["processing_failed"])
        self.assertEqual(out["reason"], "Decision Maker could not process this evaluation.")
        self.assertNotIn("result", out)

    def test_custom_reason(self):
        out = build_processing_failed_response(
            "a",
            "b",
            completed_at="2026-01-01T00:00:00Z",
            reason="Custom failure",
        )
        self.assertEqual(out["reason"], "Custom failure")


class UtcNowZTests(unittest.TestCase):
    def test_format(self):
        s = utc_now_z()
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()

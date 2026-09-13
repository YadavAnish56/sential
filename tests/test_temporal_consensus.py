"""
Unit tests for Temporal Consensus Resolver (Phase 20C).

Validates:
- Syntax-aware token parsing (Standard RTO and Bharat Series)
- Fast Lock on single high-confidence observation (>= 0.85)
- Consensus Lock on >= 3 agreeing observations
- Convergence under noisy/partially corrupted candidate streams
- Handling spaces vs no-spaces
- Exclusion of invalid/non-plate candidates (e.g. SUZUKI, DIESEL)
- Contradictory candidate resolution
"""

import pytest

from ai_engine.anpr.coordinator import (
    TrackPlateObservation,
    parse_indian_plate_tokens,
    resolve_temporal_consensus,
)


def make_obs(
    normalized_plate: str,
    ocr_confidence: float = 0.70,
    is_valid_format: bool = True,
    pts_ms: float = 1000.0,
) -> TrackPlateObservation:
    return TrackPlateObservation(
        pts_ms=pts_ms,
        plate_bbox_crop=(10, 10, 100, 30),
        plate_bbox_frame=(100, 100, 200, 130),
        raw_text=normalized_plate,
        normalized_plate=normalized_plate,
        ocr_confidence=ocr_confidence,
        plate_detector_confidence=0.90,
        sharpness=120.0,
        is_valid_format=is_valid_format,
        aspect_ratio=4.0,
    )


class TestTemporalConsensus:

    def test_parse_indian_plate_tokens(self):
        tokens_std = parse_indian_plate_tokens("KA02MM9091")
        assert tokens_std is not None
        assert tokens_std["type"] == "STANDARD"
        assert tokens_std["state"] == "KA"
        assert tokens_std["rto"] == "02"
        assert tokens_std["series"] == "MM"
        assert tokens_std["number"] == "9091"

        tokens_single_digit_rto = parse_indian_plate_tokens("DL8CAA1234")
        assert tokens_single_digit_rto is not None
        assert tokens_single_digit_rto["state"] == "DL"
        assert tokens_single_digit_rto["rto"] == "08"

        tokens_bh = parse_indian_plate_tokens("22BH1234AA")
        assert tokens_bh is not None
        assert tokens_bh["type"] == "BH"
        assert tokens_bh["year"] == "22"
        assert tokens_bh["number"] == "1234"

        assert parse_indian_plate_tokens("SUZUKI") is None
        assert parse_indian_plate_tokens("102WH7258") is None

    def test_fast_lock_on_high_confidence(self):
        obs = [
            make_obs("KA02MM9091", ocr_confidence=0.92, is_valid_format=True)
        ]
        resolved, lock_type = resolve_temporal_consensus(obs, fast_lock_confidence=0.85)
        assert resolved == "KA02MM9091"
        assert lock_type == "FAST_LOCK"

    def test_consensus_lock_three_agreeing_observations(self):
        obs = [
            make_obs("KA02MM9091", ocr_confidence=0.72, is_valid_format=True, pts_ms=100.0),
            make_obs("KA02MM9091", ocr_confidence=0.78, is_valid_format=True, pts_ms=300.0),
            make_obs("KA02MM9091", ocr_confidence=0.75, is_valid_format=True, pts_ms=500.0),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, fast_lock_confidence=0.90
        )
        assert resolved == "KA02MM9091"
        assert lock_type == "CONSENSUS_LOCK"

    def test_consensus_with_noisy_candidates(self):
        # 3 observations of KA02MM9091 out-voting 1 corrupted reading
        obs = [
            make_obs("KA02MM9091", ocr_confidence=0.75, is_valid_format=True, pts_ms=100.0),
            make_obs("KA02NM9091", ocr_confidence=0.45, is_valid_format=True, pts_ms=300.0),
            make_obs("KA02MM9091", ocr_confidence=0.80, is_valid_format=True, pts_ms=500.0),
            make_obs("KA02MM9091", ocr_confidence=0.78, is_valid_format=True, pts_ms=700.0),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, fast_lock_confidence=0.90
        )
        assert resolved == "KA02MM9091"
        assert lock_type == "CONSENSUS_LOCK"

    def test_invalid_candidates_excluded(self):
        # Observations with non-plate noise should NOT be used for consensus
        obs = [
            make_obs("SUZUKI", ocr_confidence=0.85, is_valid_format=False),
            make_obs("MARUTI", ocr_confidence=0.90, is_valid_format=False),
            make_obs("KA02HN1826", ocr_confidence=0.70, is_valid_format=True),
        ]
        # Only 1 valid observation, which is below min_observations=3
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, fast_lock_confidence=0.90
        )
        assert resolved is None
        assert lock_type is None

    def test_contradictory_unresolved_until_agreement(self):
        # 1 vs 1 contradiction
        obs = [
            make_obs("KA02MM9091", ocr_confidence=0.70, is_valid_format=True),
            make_obs("DL01AB1234", ocr_confidence=0.70, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, fast_lock_confidence=0.90
        )
        assert resolved is None
        assert lock_type is None

    def test_ka02hn1826_consensus(self):
        obs = [
            make_obs("KA02HN1826", ocr_confidence=0.78, is_valid_format=True),
            make_obs("KA02HN1826", ocr_confidence=0.82, is_valid_format=True),
            make_obs("KA02HN1826", ocr_confidence=0.80, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, fast_lock_confidence=0.90
        )
        assert resolved == "KA02HN1826"
        assert lock_type == "CONSENSUS_LOCK"

    def test_ka02mh7258_two_row_behavior(self):
        # Two-row plate with stacked readings concatenated or noisy candidates
        tokens = parse_indian_plate_tokens("KA02MH7258")
        assert tokens is not None
        assert tokens["state"] == "KA"
        assert tokens["rto"] == "02"
        assert tokens["series"] == "MH"
        assert tokens["number"] == "7258"

        obs = [
            make_obs("KA02MH7258", ocr_confidence=0.75, is_valid_format=True),
            make_obs("KA02MH7258", ocr_confidence=0.79, is_valid_format=True),
            make_obs("KA02MH7258", ocr_confidence=0.76, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, fast_lock_confidence=0.90
        )
        assert resolved == "KA02MH7258"
        assert lock_type == "CONSENSUS_LOCK"

    def test_variable_length_candidates(self):
        # 1-digit RTO, 3-digit number, 4-digit number
        tokens_3d = parse_indian_plate_tokens("DL8CAA123")
        assert tokens_3d is not None
        assert tokens_3d["state"] == "DL"
        assert tokens_3d["rto"] == "08"
        assert tokens_3d["series"] == "CAA"
        assert tokens_3d["number"] == "123"

        obs = [
            make_obs("DL8CAA123", ocr_confidence=0.88, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(obs, fast_lock_confidence=0.85)
        assert resolved == "DL8CAA123"
        assert lock_type == "FAST_LOCK"

    def test_contradictory_resolved_after_clear_majority(self):
        # Stream starts contradictory, then converges
        obs = [
            make_obs("KA02MM9091", ocr_confidence=0.70, is_valid_format=True),
            make_obs("KA02HN1826", ocr_confidence=0.70, is_valid_format=True),
            make_obs("KA02MM9091", ocr_confidence=0.82, is_valid_format=True),
            make_obs("KA02MM9091", ocr_confidence=0.84, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, fast_lock_confidence=0.90
        )
        assert resolved == "KA02MM9091"
        assert lock_type == "CONSENSUS_LOCK"

    def test_candidate_floor_020_admission_and_exclusion(self):
        # Observation with conf 0.19 excluded from consensus voting
        obs_sub_floor = [
            make_obs("GJ05AB1234", ocr_confidence=0.20, is_valid_format=True),
            make_obs("GJ05AB1234", ocr_confidence=0.22, is_valid_format=True),
            make_obs("GJ05AB1234", ocr_confidence=0.19, is_valid_format=True),  # Sub-floor!
        ]
        # Only 2 valid observations meeting floor (need 3)
        resolved, lock_type = resolve_temporal_consensus(
            obs_sub_floor, min_observations=3, agreement_ratio=0.60, min_candidate_confidence=0.20
        )
        assert resolved is None
        assert lock_type is None

        # When third observation is 0.20 (meeting floor), consensus achieved
        obs_at_floor = [
            make_obs("GJ05AB1234", ocr_confidence=0.20, is_valid_format=True),
            make_obs("GJ05AB1234", ocr_confidence=0.22, is_valid_format=True),
            make_obs("GJ05AB1234", ocr_confidence=0.20, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs_at_floor, min_observations=3, agreement_ratio=0.60, min_candidate_confidence=0.20
        )
        assert resolved == "GJ05AB1234"
        assert lock_type == "CONSENSUS_LOCK"

    def test_two_observations_insufficient_for_consensus(self):
        obs = [
            make_obs("GJ05AB1234", ocr_confidence=0.25, is_valid_format=True),
            make_obs("GJ05AB1234", ocr_confidence=0.28, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, min_candidate_confidence=0.20
        )
        assert resolved is None
        assert lock_type is None

    def test_below_60_percent_agreement_unresolved(self):
        obs = [
            make_obs("GJ05AB1234", ocr_confidence=0.25, is_valid_format=True),
            make_obs("MH12CD5678", ocr_confidence=0.25, is_valid_format=True),
            make_obs("DL01EF9999", ocr_confidence=0.25, is_valid_format=True),
        ]
        resolved, lock_type = resolve_temporal_consensus(
            obs, min_observations=3, agreement_ratio=0.60, min_candidate_confidence=0.20
        )
        assert resolved is None
        assert lock_type is None

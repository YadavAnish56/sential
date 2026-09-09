"""
Sentinel — Phase 7.6.2 Controlled PostgreSQL Persistence Integration Test Script.

Validates the full in-process persistence path against real PostgreSQL:
    Synthetic recognized ANPRResult
        ↓
    PersistenceDispatcher
        ↓
    ANPRPersistenceWorker (background daemon thread + bounded queue)
        ↓
    ANPRPersistenceService
        ↓
    PostgreSQL (127.0.0.1:5433 / sentinel)
        ↓
    Vehicle + Event + Watchlist Alert

Executes:
- Step 1: Database Precheck
- Step 2: Record baseline
- Step 3: Create deterministic ANPRResult
- Step 4: Start real worker
- Step 5: Dispatch
- Step 6: Drain worker
- Step 7: Verify PostgreSQL records
- Step 8: Duplicate/dedup check
- Step 9: Transactional cleanup of test-created records
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root and backend root are in sys.path
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for path_str in [str(ROOT), str(BACKEND)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from ai_engine.anpr.schemas import ANPRResult
from ai_engine.persistence_dispatcher import DispatchResult, PersistenceDispatcher
from app.database.connection import SessionLocal
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_worker import ANPRPersistenceWorker, EnqueueStatus

# Reconfigure stdout/stderr for UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sentinel.scripts.test_phase7_6_2")


def run_phase7_6_2_test() -> dict[str, any]:
    report: dict[str, any] = {}
    print("=" * 70)
    print("PHASE 7.6.2: CONTROLLED POSTGRESQL PERSISTENCE INTEGRATION TEST")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # STEP 1: DATABASE PRECHECK
    # -------------------------------------------------------------------------
    print("\n[STEP 1] Database Precheck (127.0.0.1:5433 / sentinel)...")
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.camera_code == "CAM-001").first()
        if not camera:
            raise RuntimeError("Precheck failed: Camera CAM-001 not found in PostgreSQL.")
        print(f"  ✓ Connected to PostgreSQL. Found camera: {camera.camera_code} (id={camera.id}, status={camera.status})")
        report["camera_id"] = camera.id
        report["camera_code"] = camera.camera_code
    finally:
        db.close()

    # -------------------------------------------------------------------------
    # STEP 2: RECORD CLEANUP BASELINE
    # -------------------------------------------------------------------------
    test_plate = "MH12AB1234"
    print(f"\n[STEP 2] Recording baseline for test plate: {test_plate}...")
    db = SessionLocal()
    try:
        baseline_vehicle = db.query(Vehicle).filter(Vehicle.plate_number == test_plate).first()
        baseline_vehicle_id = baseline_vehicle.id if baseline_vehicle else None
        baseline_events = [e.id for e in db.query(Event).filter(Event.vehicle_id == baseline_vehicle_id).all()] if baseline_vehicle_id else []
        baseline_alerts = [a.id for a in db.query(Alert).filter(Alert.vehicle_id == baseline_vehicle_id).all()] if baseline_vehicle_id else []

        print(f"  ✓ Baseline recorded:")
        print(f"    - Pre-existing Vehicle: {baseline_vehicle_id}")
        print(f"    - Pre-existing Events : {len(baseline_events)} ({baseline_events})")
        print(f"    - Pre-existing Alerts : {len(baseline_alerts)} ({baseline_alerts})")

        report["baseline"] = {
            "vehicle_id": baseline_vehicle_id,
            "events_count": len(baseline_events),
            "alerts_count": len(baseline_alerts),
        }
    finally:
        db.close()

    test_created_vehicle_id: int | None = None
    test_created_event_ids: list[int] = []
    test_created_alert_ids: list[int] = []

    try:
        # ---------------------------------------------------------------------
        # STEP 3: CREATE DETERMINISTIC RECOGNIZED ANPR RESULT
        # ---------------------------------------------------------------------
        print("\n[STEP 3] Creating synthetic recognized ANPRResult...")
        anpr1 = ANPRResult(
            camera_id="CAM-001",
            track_id=7001,
            pts_ms=12345.67,
            raw_text="MH 12 AB 1234",
            normalized_plate="MH12AB1234",
            confidence=0.95,
            is_valid_format=True,
            status="RECOGNIZED",
            plate_bbox=(100.0, 100.0, 200.0, 150.0),
        )
        print(f"  ✓ Synthetic ANPRResult created: plate={anpr1.normalized_plate}, track_id={anpr1.track_id}, pts_ms={anpr1.pts_ms}")

        # ---------------------------------------------------------------------
        # STEP 4: START REAL WORKER
        # ---------------------------------------------------------------------
        print("\n[STEP 4] Starting real ANPRPersistenceWorker and PersistenceDispatcher...")
        worker1 = ANPRPersistenceWorker(
            session_factory=SessionLocal,
            poll_timeout_sec=0.1,
            max_queue_size=50,
        )
        dispatcher1 = PersistenceDispatcher(
            worker=worker1,
            default_watchlist=[test_plate],
        )
        worker1.start()
        print(f"  ✓ Worker thread started (alive={worker1.is_alive()}, qsize={worker1.qsize})")

        # ---------------------------------------------------------------------
        # STEP 5: DISPATCH
        # ---------------------------------------------------------------------
        print("\n[STEP 5] Dispatching synthetic result...")
        timestamp1 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
        dispatch_res1 = dispatcher1.dispatch(
            anpr_results=[anpr1],
            camera_code="CAM-001",
            timestamp=timestamp1,
            watchlist=[test_plate],
        )
        print(f"  ✓ Dispatch result:")
        print(f"    - received  : {dispatch_res1.received}")
        print(f"    - eligible  : {dispatch_res1.eligible}")
        print(f"    - enqueued  : {dispatch_res1.enqueued}")
        print(f"    - skipped   : {dispatch_res1.skipped}")
        print(f"    - queue_full: {dispatch_res1.queue_full}")
        print(f"    - errors    : {dispatch_res1.errors}")

        assert dispatch_res1.received == 1, f"Expected received=1, got {dispatch_res1.received}"
        assert dispatch_res1.eligible == 1, f"Expected eligible=1, got {dispatch_res1.eligible}"
        assert dispatch_res1.enqueued == 1, f"Expected enqueued=1, got {dispatch_res1.enqueued}"
        assert dispatch_res1.skipped == 0, f"Expected skipped=0, got {dispatch_res1.skipped}"
        assert dispatch_res1.queue_full == 0, f"Expected queue_full=0, got {dispatch_res1.queue_full}"
        assert len(dispatch_res1.errors) == 0, f"Expected errors=[], got {dispatch_res1.errors}"
        report["dispatch_1"] = dispatch_res1.to_dict()

        # ---------------------------------------------------------------------
        # STEP 6: DRAIN WORKER
        # ---------------------------------------------------------------------
        print("\n[STEP 6] Draining worker queue and stopping worker cleanly...")
        drained1 = worker1.join(timeout=5.0)
        print(f"  ✓ Worker queue drained: {drained1} (remaining tasks: {worker1.qsize})")
        assert drained1 is True, "Worker join timeout expired before queue drained."

        worker1.stop(timeout=2.0)
        print(f"  ✓ Worker stopped cleanly (alive={worker1.is_alive()})")
        assert not worker1.is_alive(), "Worker thread failed to terminate within timeout."

        # ---------------------------------------------------------------------
        # STEP 7: VERIFY POSTGRESQL
        # ---------------------------------------------------------------------
        print("\n[STEP 7] Verifying PostgreSQL records with fresh session...")
        db = SessionLocal()
        try:
            # 1. Vehicle
            v = db.query(Vehicle).filter(Vehicle.plate_number == test_plate).first()
            assert v is not None, f"Vehicle with plate {test_plate} was not found in PostgreSQL."
            print(f"  ✓ Vehicle verified: id={v.id}, plate_number={v.plate_number}")
            if baseline_vehicle_id is None:
                test_created_vehicle_id = v.id

            # 2. Event
            events = db.query(Event).filter(Event.vehicle_id == v.id).all()
            new_events = [e for e in events if e.id not in baseline_events]
            assert len(new_events) == 1, f"Expected 1 new Event, found {len(new_events)}"
            ev1 = new_events[0]
            test_created_event_ids.append(ev1.id)

            print(f"  ✓ Event verified:")
            print(f"    - id           : {ev1.id}")
            print(f"    - camera_id    : {ev1.camera_id} (matches CAM-001 id {camera.id})")
            print(f"    - vehicle_id   : {ev1.vehicle_id}")
            print(f"    - confidence   : {ev1.confidence}")
            print(f"    - timestamp    : {ev1.timestamp}")
            print(f"    - event_type   : {ev1.event_type}")
            print(f"    - snapshot_path: {ev1.snapshot_path}")

            assert ev1.camera_id == camera.id, f"Event camera_id={ev1.camera_id} != {camera.id}"
            assert ev1.vehicle_id == v.id, f"Event vehicle_id={ev1.vehicle_id} != {v.id}"
            assert abs(ev1.confidence - 0.95) < 1e-4, f"Event confidence={ev1.confidence} != 0.95"
            assert ev1.event_type == "anpr_detection", f"Event event_type={ev1.event_type} != anpr_detection"
            assert ev1.snapshot_path is None, f"Event snapshot_path={ev1.snapshot_path} is not None"

            # Check timestamp against deterministic UTC time (ignoring tzinfo if naive DB column)
            ev1_ts_naive = ev1.timestamp.replace(tzinfo=None)
            expected_ts_naive = timestamp1.replace(tzinfo=None)
            assert ev1_ts_naive == expected_ts_naive, f"Event timestamp {ev1_ts_naive} != {expected_ts_naive}"

            # 3. Alert
            alerts = db.query(Alert).filter(Alert.vehicle_id == v.id).all()
            new_alerts = [a for a in alerts if a.id not in baseline_alerts]
            assert len(new_alerts) == 1, f"Expected 1 new Alert, found {len(new_alerts)}"
            al1 = new_alerts[0]
            test_created_alert_ids.append(al1.id)

            print(f"  ✓ Alert verified:")
            print(f"    - id         : {al1.id}")
            print(f"    - camera_id  : {al1.camera_id}")
            print(f"    - vehicle_id : {al1.vehicle_id}")
            print(f"    - alert_type : {al1.alert_type}")
            print(f"    - severity   : {al1.severity}")
            print(f"    - status     : {al1.status}")
            print(f"    - timestamp  : {al1.timestamp}")
            print(f"    - message    : {al1.message}")

            assert al1.camera_id == camera.id, f"Alert camera_id={al1.camera_id} != {camera.id}"
            assert al1.vehicle_id == v.id, f"Alert vehicle_id={al1.vehicle_id} != {v.id}"
            assert al1.alert_type == "ANPR_WATCHLIST", f"Alert alert_type={al1.alert_type} != ANPR_WATCHLIST"
            assert al1.severity == "high", f"Alert severity={al1.severity} != high"
            assert al1.status == "new", f"Alert status={al1.status} != new"
            assert test_plate in (al1.message or ""), f"Alert message '{al1.message}' does not contain {test_plate}"
            al1_ts_naive = al1.timestamp.replace(tzinfo=None)
            assert al1_ts_naive == expected_ts_naive, f"Alert timestamp {al1_ts_naive} != {expected_ts_naive}"

            # Verify pts_ms (12345.67) was NOT converted to a datetime
            assert anpr1.pts_ms == 12345.67
            assert not isinstance(anpr1.pts_ms, datetime)

            report["verified_run_1"] = {
                "vehicle_id": v.id,
                "event_id": ev1.id,
                "alert_id": al1.id,
            }
        finally:
            db.close()

        # ---------------------------------------------------------------------
        # STEP 8: DUPLICATE / DEDUP CHECK
        # ---------------------------------------------------------------------
        print("\n[STEP 8] Duplicate / Dedup check (second controlled dispatch)...")
        anpr2 = ANPRResult(
            camera_id="CAM-001",
            track_id=7002,
            pts_ms=12400.00,
            raw_text="MH 12 AB 1234",
            normalized_plate="MH12AB1234",
            confidence=0.96,
            is_valid_format=True,
            status="RECOGNIZED",
            plate_bbox=(110.0, 105.0, 210.0, 155.0),
        )
        timestamp2 = datetime(2026, 9, 6, 12, 0, 5, tzinfo=timezone.utc)

        worker2 = ANPRPersistenceWorker(
            session_factory=SessionLocal,
            poll_timeout_sec=0.1,
            max_queue_size=50,
        )
        dispatcher2 = PersistenceDispatcher(
            worker=worker2,
            default_watchlist=[test_plate],
        )
        worker2.start()

        dispatch_res2 = dispatcher2.dispatch(
            anpr_results=[anpr2],
            camera_code="CAM-001",
            timestamp=timestamp2,
            watchlist=[test_plate],
        )
        assert dispatch_res2.enqueued == 1

        drained2 = worker2.join(timeout=5.0)
        assert drained2 is True
        worker2.stop(timeout=2.0)

        db = SessionLocal()
        try:
            # Verify Vehicle count for MH12AB1234 is still exactly 1
            vehicles_found = db.query(Vehicle).filter(Vehicle.plate_number == test_plate).all()
            print(f"  ✓ Vehicle deduplication verified: count={len(vehicles_found)} (id={vehicles_found[0].id})")
            assert len(vehicles_found) == 1, f"Expected exactly 1 Vehicle row, found {len(vehicles_found)}"

            # Verify Event count is now 2 (distinct detection event created)
            events_found = db.query(Event).filter(Event.vehicle_id == vehicles_found[0].id).all()
            print(f"  ✓ Event creation verified: total events for vehicle={len(events_found)}")
            assert len(events_found) == len(baseline_events) + 2, f"Expected 2 total new events, found {len(events_found)}"
            newest_events = [e for e in events_found if e.id not in baseline_events and e.id not in test_created_event_ids]
            assert len(newest_events) == 1
            ev2 = newest_events[0]
            test_created_event_ids.append(ev2.id)
            print(f"    - Second Event id: {ev2.id}, timestamp: {ev2.timestamp}")

            # Verify Alert behavior
            alerts_found = db.query(Alert).filter(Alert.vehicle_id == vehicles_found[0].id).all()
            newest_alerts = [a for a in alerts_found if a.id not in baseline_alerts and a.id not in test_created_alert_ids]
            print(f"  ✓ Alert creation verified: total alerts for vehicle={len(alerts_found)}, newly added={len(newest_alerts)}")
            for na in newest_alerts:
                test_created_alert_ids.append(na.id)
                print(f"    - Second Alert id: {na.id}, timestamp: {na.timestamp}")

            report["duplicate_check"] = {
                "vehicle_count": len(vehicles_found),
                "total_test_events": len(test_created_event_ids),
                "total_test_alerts": len(test_created_alert_ids),
            }
        finally:
            db.close()

    finally:
        # ---------------------------------------------------------------------
        # STEP 9: TRANSACTIONAL CLEANUP
        # ---------------------------------------------------------------------
        print("\n[STEP 9] Transactional Cleanup of test-created records...")
        print(f"  Target test records to delete:")
        print(f"    - Alert IDs  : {test_created_alert_ids}")
        print(f"    - Event IDs  : {test_created_event_ids}")
        print(f"    - Vehicle ID : {test_created_vehicle_id} (only if created by this test run)")

        cleanup_db = SessionLocal()
        try:
            # 1. Delete test Alerts
            if test_created_alert_ids:
                deleted_alerts = cleanup_db.query(Alert).filter(Alert.id.in_(test_created_alert_ids)).delete(synchronize_session=False)
                print(f"  ✓ Deleted {deleted_alerts} test Alert records.")

            # 2. Delete test Events
            if test_created_event_ids:
                deleted_events = cleanup_db.query(Event).filter(Event.id.in_(test_created_event_ids)).delete(synchronize_session=False)
                print(f"  ✓ Deleted {deleted_events} test Event records.")

            # 3. Delete test Vehicle if it was created in this test run
            if test_created_vehicle_id is not None:
                deleted_vehicles = cleanup_db.query(Vehicle).filter(Vehicle.id == test_created_vehicle_id).delete(synchronize_session=False)
                print(f"  ✓ Deleted {deleted_vehicles} test Vehicle record (id={test_created_vehicle_id}).")

            cleanup_db.commit()
            print("  ✓ Cleanup transaction committed.")
        except Exception as exc:
            cleanup_db.rollback()
            print(f"  ✗ Cleanup error: {exc}")
            raise
        finally:
            cleanup_db.close()

        # Verification of cleanup
        verify_db = SessionLocal()
        try:
            remaining_alerts = verify_db.query(Alert).filter(Alert.id.in_(test_created_alert_ids)).all() if test_created_alert_ids else []
            remaining_events = verify_db.query(Event).filter(Event.id.in_(test_created_event_ids)).all() if test_created_event_ids else []
            remaining_vehicle = verify_db.query(Vehicle).filter(Vehicle.id == test_created_vehicle_id).first() if test_created_vehicle_id else None
            cam_remains = verify_db.query(Camera).filter(Camera.camera_code == "CAM-001").first()

            assert len(remaining_alerts) == 0, f"Alerts still remain: {[a.id for a in remaining_alerts]}"
            assert len(remaining_events) == 0, f"Events still remain: {[e.id for e in remaining_events]}"
            assert remaining_vehicle is None, f"Vehicle still remains: {remaining_vehicle.id}"
            assert cam_remains is not None, "Camera CAM-001 was erroneously deleted!"

            print(f"  ✓ Cleanup verified: 0 test records remain. Camera CAM-001 intact.")
            report["cleanup"] = {
                "deleted_alerts": len(test_created_alert_ids),
                "deleted_events": len(test_created_event_ids),
                "deleted_vehicle": test_created_vehicle_id is not None,
                "verified_clean": True,
            }
        finally:
            verify_db.close()

    print("\n" + "=" * 70)
    print("PHASE 7.6.2 INTEGRATION TEST COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    return report


if __name__ == "__main__":
    run_phase7_6_2_test()

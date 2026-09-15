import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader
from streaming.pts import PTSTracker
from ai_engine.config import DetectorConfig
from ai_engine.detector import VehicleDetector
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.anpr import ANPRCoordinator, ANPRConfig, EasyOCRPlateRecognizer
from ai_engine.pipeline import CameraPipeline

def main():
    print("=== CAM01 Real ANPR Eval ===")
    rtsp_url = "rtsp://127.0.0.1:8554/stream/cam01"
    
    pts_tracker = PTSTracker()
    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    stream_processor = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=5))
    tracker = VehicleTracker(config=TrackerConfig())
    ocr_recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=True, allow_invalid_candidates=True, min_confidence=0.15)
    anpr_coordinator = ANPRCoordinator(recognizer=ocr_recognizer, config=ANPRConfig(min_confidence=0.40, max_attempts=5))
    
    pipeline = CameraPipeline(
        camera_id="cam01",
        stream_processor=stream_processor,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coordinator
    )
    
    rtsp_client = RTSPClient(rtsp_url=rtsp_url, camera_id="cam01", transport="tcp")
    if not rtsp_client.open():
        print("ERROR: RTSPClient failed to open stream.")
        return 1
    
    frame_reader = FrameReader(client=rtsp_client, pts_tracker=pts_tracker)
    
    start_time = time.time()
    frames_read = 0
    
    while time.time() - start_time < 30.0:
        success, packet = frame_reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue
            
        frames_read += 1
        pipe_result = pipeline.process_frame(packet)
        
        if pipe_result.anpr_results:
            for ar in pipe_result.anpr_results:
                print(f"ANPRResult: status={ar.status}, raw='{ar.raw_text}', norm='{ar.normalized_plate}', valid={ar.is_valid_format}, conf={ar.confidence:.4f}")
    
    rtsp_client.close()
    print(f"Done. Processed {frames_read} frames.")

if __name__ == "__main__":
    sys.exit(main() or 0)

import asyncio
import cv2
import time
from typing import List, Dict, Any, Tuple
from .video_service import video_service
from .detection_service import detection_service
from .brain_service import brain_service
from models_fusion import Detection

class StreamManager:
    def __init__(self):
        self.simulation_end_time = 0

    async def stream(self, websocket):
        frame_count = 0 
        previous_frame = None
        motion_detected = False
        last_detections = []
        last_detection_time = 0
        
        loop = asyncio.get_running_loop()
        
        try:
            while True:
                frame = video_service.get_frame()
                if frame is None:
                    await asyncio.sleep(0.01)
                    continue
                
                frame_count += 1
                
                # --- Motion Detection Step (Optimized) ---
                h, w = frame.shape[:2]
                motion_scale = 200 / w
                motion_w = 200
                motion_h = int(h * motion_scale)
                motion_small = cv2.resize(frame, (motion_w, motion_h))
                
                gray = cv2.cvtColor(motion_small, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (5, 5), 0)
                
                if previous_frame is None:
                    previous_frame = gray
                    continue
                    
                frame_delta = cv2.absdiff(previous_frame, gray)
                thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
                motion_pixels = cv2.countNonZero(thresh)
                
                previous_frame = gray
                
                if motion_pixels > 200:
                    motion_detected = True
                else:
                    motion_detected = False
                    
                # Check Simulation
                if time.time() < self.simulation_end_time:
                    motion_detected = True # Force motion to trigger logic

                # Run detection ONLY every 15th frame AND if motion is detected (or heartbeat)
                should_run_detection = (frame_count % 15 == 0 and motion_detected) or (frame_count % 30 == 0)
                
                if should_run_detection:
                    # 1. Resize for speed (640 width standard for YOLO)
                    height, width = frame.shape[:2]
                    scale = 640 / width
                    new_width = 640
                    new_height = int(height * scale)
                    small_frame = cv2.resize(frame, (new_width, new_height))
                    
                    # 2. Run in Executor
                    def run_detect_safe():
                        try:
                            # Standard YOLO Detection
                            annotated, dets = detection_service.detect_persons(small_frame)
                            
                            # Facial Recognition Integration
                            dets = detection_service.recognize_faces(small_frame, dets)
                            
                            return annotated, dets
                        except Exception as e:
                            print(f"CRITICAL DETECTION ERROR: {e}")
                            return small_frame, []
                    
                    # Run detection in thread pool
                    annotated_small, detections = await loop.run_in_executor(None, run_detect_safe)
                    
                    # INJECT SIMULATION DETECTIONS
                    if time.time() < self.simulation_end_time:
                        fake_det = Detection(
                            bbox=[200.0, 100.0, 440.0, 380.0],
                            confidence=0.99,
                            class_name="person",
                            identity="SIMULATION"
                        )
                        detections.append(fake_det)

                    # Update persistent detections (rescale back to original frame)
                    last_detections = []
                    if detections:
                        last_detection_time = time.time()
                        for det in detections:
                            scale_x = width / 640
                            scale_y = height / new_height
                            x1, y1, x2, y2 = det.bbox
                            real_x1 = int(x1 * scale_x)
                            real_y1 = int(y1 * scale_y)
                            real_x2 = int(x2 * scale_x)
                            real_y2 = int(y2 * scale_y)
                            
                            label_text = f"{det.identity or 'Person'} {det.confidence:.2f}"
                            last_detections.append({
                                "bbox": (real_x1, real_y1, real_x2, real_y2),
                                "label": label_text
                            })

                    # Brain Processing (Orchestration) - Offloaded
                    await loop.run_in_executor(None, lambda: brain_service.process_frame(detections, loop))
                
                # --- Draw Persistent Detections on Display Frame ---
                if time.time() - last_detection_time > 2.0:
                     last_detections = []
                
                display_frame = frame.copy()
                for item in last_detections:
                    x1, y1, x2, y2 = item['bbox']
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                    cv2.rectangle(display_frame, (x1, y1-20), (x2, y1), (255, 0, 0), -1)
                    cv2.putText(display_frame, item['label'], (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

                # PERFORMANCE: Resize display frame
                d_h, d_w = display_frame.shape[:2]
                if d_w > 640:  # Restrict display size to 640px max width for speed
                    scale = 640 / d_w
                    display_frame = cv2.resize(display_frame, (640, int(d_h * scale)))

                if frame_count % 100 == 0:
                    print(f"Server Heartbeat: Processed {frame_count} frames.")

                # Encode frame to JPEG - Offload to thread pool to avoid blocking async loop
                def encode_frame(img):
                    return cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                
                ret, buffer = await loop.run_in_executor(None, encode_frame, display_frame)
                if not ret:
                    continue
                
                await websocket.send_bytes(buffer.tobytes())
                await asyncio.sleep(0.005) # Increased yield time for smoother concurrency
                
        except Exception as e:
            raise e

    def trigger_simulation(self, duration: float = 10.0):
        self.simulation_end_time = time.time() + duration

stream_manager = StreamManager()

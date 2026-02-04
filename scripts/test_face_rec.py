import sys
import os
import cv2
import numpy as np
from unittest.mock import MagicMock

# Add 'backend' to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from services.detection_service import DetectionService
from models_fusion import Detection

def test_facial_recognition():
    print("Initializing DetectionService...")
    # Initialize without loading YOLO for speed (mocking YOLO not needed for this test)
    # Actually DetectionService loads YOLO in init. We let it load or mock it if too slow.
    # For now, let's just instantiate it.
    ds = DetectionService()
    
    # Create a dummy image
    # Black image 640x480
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    
    # Create a dummy detection
    # A box in the middle
    det = Detection(
        class_name="person",
        confidence=0.9,
        bbox=[100, 100, 300, 300], # 200x200 box, large enough
        identity=None,
        known=False
    )
    
    print("Testing recognize_faces with empty DB...")
    # Should complete without error and return Unknown
    results = ds.recognize_faces(frame, [det])
    print(f"Result 1: {results[0].identity} (Expected: Unknown)")
    
    # Setup multiple detections
    det2 = Detection(
        class_name="person",
        confidence=0.8,
        bbox=[400, 100, 420, 120], # Small box, should skip
        identity=None,
        known=False
    )
    
    print("Testing small box skipping...")
    results = ds.recognize_faces(frame, [det2])
    # Should remain None or Unknown depending on logic, but logic only touches if size > 50
    # Current logic: "for det in detections... if size > 50... else..."
    # Actually if size < 50, it does NOTHING to det.identity. 
    # Let's check what checking logic does.
    # Ah, if size < 50, the loop continues. det.identity remains None.
    print(f"Result 2: {results[0].identity} (Expected: None)")

    print("Verification Script Completed.")

if __name__ == "__main__":
    test_facial_recognition()

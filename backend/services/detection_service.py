from ultralytics import YOLO
import cv2
import numpy as np
from typing import List, Tuple
from models_fusion import Detection

class DetectionService:
    def __init__(self, model_path='yolov8n.pt'):
        print(f"Loading YOLO model: {model_path}...")
        self.model = YOLO(model_path)
        self.classes = self.model.names
        
        # Check DeepFace availability once
        self.deepface_available = False
        try:
            from deepface import DeepFace
            self.DeepFace = DeepFace
            self.deepface_available = True
            print("DeepFace initialized successfully.")
        except ImportError:
            print("DeepFace not installed. Facial recognition disabled.")

    def detect_persons(self, frame) -> Tuple[np.ndarray, List[Detection]]:
        """
        Detects persons in the frame.
        Returns:
            annotated_frame: Frame with bounding boxes
            detections: List of Detection objects
        """
        results = self.model(frame, classes=[0], conf=0.7, verbose=False) # 0 is person class in COCO
        detections: List[Detection] = []
        
        for result in results:
            boxes = result.boxes
            for box in boxes:
                # Get box coordinates
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                class_name = self.classes[cls]
                
                # Filter small boxes
                if (x2 - x1) < 100 or (y2 - y1) < 100:
                    continue

                detections.append(Detection(
                    bbox=[float(x1), float(y1), float(x2), float(y2)],
                    confidence=conf,
                    class_name=class_name
                ))

        # annotating frame
        annotated_frame = results[0].plot()
        return annotated_frame, detections

    def recognize_faces(self, frame, detections: List[Detection]) -> List[Detection]:
        """
        Uses DeepFace to recognize faces in the detected person bounding boxes.
        """
        if not self.deepface_available:
            return detections
            
        import os

        db_path = os.path.join(os.getcwd(), "backend", "faces_db")
        if not os.path.exists(db_path):
            os.makedirs(db_path, exist_ok=True)
            # If empty, return safely
            return detections
            
        # Check if DB is empty
        if not os.listdir(db_path):
             return detections

        for det in detections:
            if det.class_name == 'person':
                x1, y1, x2, y2 = map(int, det.bbox)
                h, w, _ = frame.shape
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                
                # Minimum size check for face recognition efficiency
                if x2 - x1 > 50 and y2 - y1 > 50: 
                    face_roi = frame[y1:y2, x1:x2] 
                    try:
                        # DeepFace find expects an image path or numpy array
                        dfs = self.DeepFace.find(img_path=face_roi, 
                                          db_path=db_path, 
                                          model_name="VGG-Face", 
                                          distance_metric="cosine", 
                                          enforce_detection=False, 
                                          silent=True)
                        
                        found_identity = False
                        if len(dfs) > 0 and not dfs[0].empty:
                             # DeepFace returns a list of DataFrames. dfs[0] contains matches.
                             # identity column has the full path
                             identity_path = dfs[0].iloc[0]['identity']
                             
                             # Extract name: .../faces_db/PersonName/image.jpg -> PersonName
                             # Handle both directory structure and flat file structure
                             rel_path = os.path.relpath(identity_path, db_path)
                             parts = rel_path.split(os.sep)
                             
                             if len(parts) > 1:
                                 identity_name = parts[0]
                             else:
                                 # Flat file, use filename without extension
                                 identity_name = os.path.splitext(parts[0])[0]

                             det.identity = identity_name
                             det.known = True
                             found_identity = True
                        
                        if not found_identity:
                             det.identity = "Unknown"
                             det.known = False
                             
                    except Exception as e:
                        # print(f"DeepFace Error: {e}")
                        det.identity = "Unknown"
                        det.known = False
                        
        return detections

    def check_whitelist(self, identity):
        return False

detection_service = DetectionService()

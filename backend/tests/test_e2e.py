import time
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

# Add 'backend' to sys.path to simulate running from inside backend/ directory
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from models.domain import Detection

# Set dummy env var to prevent OpenAI init crash
os.environ["OPENAI_API_KEY"] = "sk-dummy-key"

import asyncio
from unittest.mock import MagicMock, patch

# Mock asyncio.create_task globally to avoid "no running loop" errors in sync tests
asyncio.create_task = MagicMock()

# Mock dependencies
import sys
mock_ai_module = MagicMock()
mock_notify_module = MagicMock()
mock_record_module = MagicMock()

sys.modules["services.ai_service"] = mock_ai_module
sys.modules["services.notification_service"] = mock_notify_module
sys.modules["services.recording_service"] = mock_record_module

# Now import BrainService
try:
    from services.brain_service import BrainService
except ImportError:
    import sys
    sys.path.append(".")
    from services.brain_service import BrainService

@pytest.fixture
def brain():
    return BrainService()

def test_escalation_flow(brain):
        # Mock the AI task to prevent any async coroutine creation
        brain._run_ai_task = MagicMock(side_effect=lambda x: None)
        
        # Test Data: A person detection
        person_detection = Detection(class_name='person', bbox=[100, 100, 200, 200], confidence=0.9)
        detections = [person_detection]
        
        # 1. Initial Detection (t=0)
        print("Step 1: Initial Detection")
        brain.process_frame(detections)
        assert brain.threat_level == 1
        
        # Simulate time passing (5s) -> Medium Threat
        print("Step 2: Time passes (6s)...")
        brain.first_detection_time -= 6
        brain.process_frame(detections)
        assert brain.threat_level == 2
        
        # Simulate time passing (11s) -> High Threat
        print("Step 3: Time passes (12s total)...")
        brain.first_detection_time -= 6 
        brain.process_frame(detections)
        assert brain.threat_level == 3
        
        # Simulate time passing (16s) -> Critical
        print("Step 4: Time passes (17s total)...")
        brain.first_detection_time -= 5
        brain.process_frame(detections)
        assert brain.threat_level == 4
        
        print("--- E2E Test Passed: Escalation Logic Verified ---")

if __name__ == "__main__":
    # fast manual run if pytest not available
    print("Running in manual mode...")
    b = BrainService()
    # Mocking external calls for manual run
    b._run_ai_task = lambda *args, **kwargs: None
    b.notification_service.send_alert = lambda *args, **kwargs: print(f"MOCK ALERT: {args[0] if args else 'Unknown'}")
    b.recording_service.start_recording = lambda *args, **kwargs: print(f"MOCK RECORDING START: {kwargs.get('camera_id', 'unknown')}")
    b.recording_service.stop_recording = lambda *args, **kwargs: print(f"MOCK RECORDING STOP: {args[0] if args else 'unknown'}")
    
    det = [Detection(class_name='person', bbox=[0,0,100,100], confidence=0.9)]
    
    print("t=0s: Detection")
    b.process_frame(det)
    
    print("t=6s simulated: Detection")
    b.first_detection_time -= 6
    b.process_frame(det)
    
    print("t=11s simulated: Detection")
    b.first_detection_time -= 5
    b.process_frame(det)
    
    print("Manual E2E check complete.")

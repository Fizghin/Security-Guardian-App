import unittest
from unittest.mock import MagicMock
import time
import sys
import os

# Add 'backend' to sys.path to simulate running from inside backend/ directory
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from services.brain_service import BrainService
from models.domain import Detection

class TestBrainService(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_ai = MagicMock()
        self.mock_notify = MagicMock()
        self.mock_record = MagicMock()
        
        self.brain = BrainService(
            ai_service=self.mock_ai,
            notification_service=self.mock_notify,
            recording_service=self.mock_record
        )
        # Disable monitoring checks in process_frame if any (it is True by default)
        
    def test_escalation(self):
        # Create a Detection object
        p1 = Detection(class_name="person", confidence=0.9, bbox=[0,0,100,100])
        
        # t=0
        self.brain.process_frame([p1])
        self.assertEqual(self.brain.threat_level, 1)
        
        # Simulate t=+6s (Medium)
        # We cheat by hacking first_detection_time because process_frame uses time.time()
        self.brain.first_detection_time -= 6
        self.brain.process_frame([p1])
        self.assertEqual(self.brain.threat_level, 2)
        
        # Simulate t=+11s (High)
        self.brain.first_detection_time -= 5
        self.brain.process_frame([p1])
        self.assertEqual(self.brain.threat_level, 3)
        
        # Check if alert was sent (Throttle might prevent it if we aren't careful with time)
        # BrainService checks: current_time - last_alert_time > 30.0
        # last_alert_time starts at 0. so it should fire.
        self.mock_notify.send_alert.assert_called()

    def test_no_detection_reset(self):
        p1 = Detection(class_name="person", confidence=0.9, bbox=[0,0,100,100])
        
        # Trigger detection
        self.brain.process_frame([p1])
        self.assertEqual(self.brain.threat_level, 1)
        
        # Simulate no detection for 11 seconds
        # We need to simulate time passing for logic using time.time()
        # Since we can't easily mock time.time() without patching, 
        # we can assume the logic: current - last_detection > 10
        
        # But wait, logic uses time.time() inside process_frame.
        # I'll rely on the logic check: if self.last_detection_time > 0...
        
        # Let's manually set last_detection_time to 11s ago
        self.brain.last_detection_time = time.time() - 11
        
        self.brain.process_frame([]) # Empty list
        self.assertEqual(self.brain.threat_level, 0)
        self.mock_record.stop_recording.assert_called()

if __name__ == '__main__':
    unittest.main()

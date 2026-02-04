import time
import threading
from typing import List, Optional
from models_fusion import Detection, SecurityEvent, SessionLocal
from .ai_service import ai_service as default_ai_service
from .notification_service import notification_service as default_notification_service
from .recording_service import recording_service as default_recording_service
from .siren_service import siren_service

class BrainService:
    def __init__(self, ai_service=None, notification_service=None, recording_service=None):
        self.ai_service = ai_service or default_ai_service
        self.notification_service = notification_service or default_notification_service
        self.recording_service = recording_service or default_recording_service
        
        self.threat_level = 0  # 0: Safe, 1: Low, 2: Medium, 3: High, 4: Critical
        self.last_detection_time = 0
        self.first_detection_time = 0
        self.is_monitoring = True
        self.lock = threading.Lock()
        
        # Cooldowns to prevent spam
        self.last_ai_response_time = 0
        self.last_alert_time = 0
        
        # Subtitle State
        self.last_spoken_text = ""
        self.last_spoken_time = 0
        
        print("BrainService Initialized: Logic Core Online")

    def log_event(self, event_type: str, description: str, severity: str = "LOW"):
        """
        Persist security event to database.
        """
        db = None
        try:
            db = SessionLocal()
            event = SecurityEvent(
                event_type=event_type,
                description=description,
                severity=severity
            )
            db.add(event)
            db.commit()
        except Exception as e:
            print(f"Failed to log event: {e}")
        finally:
            if db:
                db.close()

    def process_frame(self, detections: List[Detection], loop=None):
        """
        Main logic loop called for each processed frame.
        """
        if not self.is_monitoring:
            return

        with self.lock:
            current_time = time.time()
            # Filter for persons using Pydantic model
            persons_detected = [d for d in detections if d.class_name == 'person']

            if persons_detected:
                self._handle_person_detection(persons_detected, current_time, loop)
            else:
                self._handle_no_detection(current_time)

    def _handle_person_detection(self, detections: List[Detection], current_time: float, loop=None):
        """
        Escalation logic when a person is seen.
        """
        # --- INSIDER LOGIC ---
        # Analyze detections to see if we have unknown intruders
        unknown_persons = [d for d in detections if not d.known]
        known_persons = [d for d in detections if d.known]
        
        # If there are NO unknown persons (i.e., everyone is an insider), we do NOT escalate.
        if not unknown_persons and known_persons:
             # Log insider sighting occasionally
             if current_time - self.last_detection_time > 10.0:
                 names = ", ".join([d.identity for d in known_persons])
                 print(f"Insider(s) Detected: {names}. Systems at ease.")
                 self.log_event("INSIDER_ACCESS", f"Authorized personnel identified: {names}", "INFO")
             
             # Reset threat level as we are safe
             self.threat_level = 0
             self.first_detection_time = 0 # specific to threat escalation
             self.last_detection_time = current_time
             return # EXIT EARLY - Do not escalate

        # If we have unknown persons, proceed with normal escalation
        
        # Logic: If no detection for > 10 seconds (increased for robustness), we treat it as a NEW event.
        if self.last_detection_time == 0 or (current_time - self.last_detection_time > 10.0):
            self.first_detection_time = current_time # Start clock NOW
            self.threat_level = 1 # Reset to Low
            print(f"Target Acquired (Reset): Time {current_time}")
            self.log_event("DETECTION", "New subject identified.", "LOW")
        
        self.last_detection_time = current_time
        duration = current_time - self.first_detection_time
        
        # Escalation Logic based on duration
        previous_level = self.threat_level
        
        if duration > 15: # Critical after 15s
            self.threat_level = 4
        elif duration > 10: # High after 10s
            self.threat_level = 3
        elif duration > 5: # Medium after 5s
            self.threat_level = 2
        
        if self.threat_level > previous_level:
            print(f"Escalating Threat Level: {previous_level} -> {self.threat_level}")
            severity_map = {2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}
            self.log_event("ESCALATION", f"Threat Level escalated to {self.threat_level}", severity_map.get(self.threat_level, "LOW"))
 
        # Actions based on Threat Level
        self._execute_countermeasures(current_time, loop)

    def _handle_no_detection(self, current_time: float):
        """
        De-escalation logic.
        """
        if self.last_detection_time > 0 and (current_time - self.last_detection_time > 15.0):
            if self.threat_level > 0:
                print("Target Lost: De-escalating")
                self.log_event("DE-ESCALATION", "Target lost. Returning to safe state.", "LOW")
                self.threat_level = 0
                self.first_detection_time = 0
                self.last_detection_time = 0
                self.recording_service.stop_recording("cam1") # Stop if recording
                siren_service.stop_siren()  # Stop siren if playing

    def _execute_countermeasures(self, current_time: float, loop=None):
        """
        Triggers AI, Alerts, Recording based on threat level.
        """
        # AI Response (Voice/Text) - regulated by cooldown
        if current_time - self.last_ai_response_time > 8.0:
            prompt = self._get_ai_prompt()
            if prompt:
                print(f"Triggering AI Response (Level {self.threat_level})...")
                # Fire and forget async task safely from thread
                import asyncio
                if loop:
                    asyncio.run_coroutine_threadsafe(self._run_ai_task(prompt), loop)
                else:
                    # Fallback (unsafe if no loop, but shouldn't happen with new StreamService)
                    asyncio.create_task(self._run_ai_task(prompt))
                self.last_ai_response_time = current_time

        # External Notification - regulated by cooldown
        if self.threat_level >= 3:
             # Trigger Recording if not already active
             self.recording_service.start_recording(camera_id="cam1")
             
             if (current_time - self.last_alert_time > 30.0):
                 msg = f"Security Breach Detected! Threat Level {self.threat_level}. Duration: {int(current_time - self.first_detection_time)}s"
                 self.notification_service.send_alert("Intruder Alert", msg, severity="high")
                 self.log_event("ALERT", msg, "HIGH")
                 self.last_alert_time = current_time
        
        # CRITICAL LEVEL 4: Siren + Email with video
        if self.threat_level >= 4:
            # Start siren alarm
            siren_service.start_siren(duration_seconds=60)
            
            # Send email with video clip if not sent recently
            if not hasattr(self, '_last_email_time') or (current_time - self._last_email_time > 120.0):
                self._last_email_time = current_time
                # Find the most recent recording
                import glob
                recordings = glob.glob("recordings/event_cam1_*.mp4")
                video_path = max(recordings, key=lambda x: x) if recordings else None
                
                msg = f"CRITICAL INTRUDER ALERT! Duration: {int(current_time - self.first_detection_time)}s. Immediate action required!"
                self.notification_service.send_email_with_video(
                    subject="CRITICAL INTRUDER - Immediate Response Required",
                    body=msg,
                    video_path=video_path
                )
                self.log_event("EMAIL_ALERT", "Email with video sent to owner", "CRITICAL")

    def _get_ai_prompt(self):
        style = "polite"
        if self.ai_service.personality["intimidation"] > 70:
            style = "very aggressive and intimidating"
        elif self.ai_service.personality["humor"] > 50:
            style = "witty and slightly sarcastic but firm"
            
        if self.threat_level == 1:
            return f"A stranger has been spotted. {style.capitalize()}ly ask them to identify themselves."
        elif self.threat_level == 2:
            return f"The stranger is still there. In a {style} style, firmly state that this is a private area."
        elif self.threat_level == 3:
            return f"The intruder is persisting. Issue a stern warning that security has been notified. Style: {style}."
        elif self.threat_level == 4:
            return f"CRITICAL: Issue a final warning. Style: {style}. Order the intruder to leave immediately or face consequences."
        return None

    async def _run_ai_task(self, prompt):
        """
        Helper to run AI generation asynchronously.
        """
        try:
           # Set current threat level on AI service for fallback messages
           self.ai_service._current_threat_level = self.threat_level
           
           response = await self.ai_service.generate_response(prompt)
           print(f"AI: {response}")
           
           # Update Subtitle State (Prioritize this over audio)
           self.last_spoken_text = response
           self.last_spoken_time = time.time()
           
           # Speak the response locally
           try:
               self.ai_service.speak_local(response)
           except Exception as tts_error:
               print(f"TTS Error (Non-fatal): {tts_error}")
           
           self.log_event("AI_RESPONSE", response[:100], "MEDIUM")
        except Exception as e:
           print(f"AI Task Error: {e}")
           import traceback
           traceback.print_exc()

brain_service = BrainService()

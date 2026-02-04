"""
Siren Alarm Service - Plays loud alarm sounds
"""
import os
import threading
import time

class SirenService:
    def __init__(self):
        self.is_playing = False
        self._thread = None
        self._stop_event = threading.Event()
        
    def start_siren(self, duration_seconds: int = 30):
        """
        Starts playing a loud siren alarm.
        Uses Windows Beep for maximum compatibility.
        """
        if self.is_playing:
            print("[SirenService] Siren already playing")
            return
            
        print(f"[SirenService] ACTIVATING SIREN for {duration_seconds} seconds!")
        self.is_playing = True
        self._stop_event.clear()
        
        self._thread = threading.Thread(
            target=self._play_siren_loop,
            args=(duration_seconds,),
            daemon=True
        )
        self._thread.start()
        
    def _play_siren_loop(self, duration_seconds: int):
        """
        Internal loop that plays alternating high/low frequency beeps.
        """
        import winsound
        
        start_time = time.time()
        high_freq = 2500  # High pitch
        low_freq = 1000   # Low pitch
        beep_duration = 200  # milliseconds
        
        try:
            while time.time() - start_time < duration_seconds:
                if self._stop_event.is_set():
                    break
                    
                # Alternating siren effect
                winsound.Beep(high_freq, beep_duration)
                if self._stop_event.is_set():
                    break
                winsound.Beep(low_freq, beep_duration)
                
        except Exception as e:
            print(f"[SirenService] Error playing siren: {e}")
        finally:
            self.is_playing = False
            print("[SirenService] Siren stopped")
    
    def stop_siren(self):
        """
        Stops the siren if playing.
        """
        if self.is_playing:
            print("[SirenService] Stopping siren...")
            self._stop_event.set()
            self.is_playing = False

# Singleton instance
siren_service = SirenService()

import subprocess
import os
from datetime import datetime
import threading
import time

class RecordingService:
    def __init__(self, output_dir="recordings"):
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        self.active_recordings = {}

    def start_recording(self, stream_url="http://localhost:8000/stream", camera_id="cam1"):
        """
        Starts recording a stream using FFmpeg.
        """
        if camera_id in self.active_recordings:
            print(f"Recording already active for {camera_id}")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.output_dir}/event_{camera_id}_{timestamp}.mp4"
        
        # FFmpeg command to record stream
        # This is a basic command, might need tuning for latency/codecs
        command = [
            "ffmpeg",
            "-y", # Overwrite
            "-f", "mjpeg", # Input format (assuming MJPEG stream)
            "-i", stream_url,
            "-c:v", "libx264", # Re-encode to H.264
            "-preset", "ultrafast",
            "-t", "60", # Record for 60 seconds (or logic to stop)
            filename
        ]
        
        print(f"Starting recording: {filename}")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.active_recordings[camera_id] = process
        
        # Auto-stop thread (optional, if we want fixed duration events)
        # threading.Timer(60, self.stop_recording, args=[camera_id]).start()

    def stop_recording(self, camera_id):
        if camera_id in self.active_recordings:
            process = self.active_recordings[camera_id]
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            del self.active_recordings[camera_id]
            print(f"Stopped recording for {camera_id}")

    def stop_all(self):
        """
        Cleans up all active recordings (Shutdown safety).
        """
        if not self.active_recordings:
            return
        print(f"RecordingService: Stopping {len(self.active_recordings)} active recordings...")
        # Materialize keys to avoid mutation during iteration
        for camera_id in list(self.active_recordings.keys()):
            self.stop_recording(camera_id)

recording_service = RecordingService()

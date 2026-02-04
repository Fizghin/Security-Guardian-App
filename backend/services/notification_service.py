import requests
import smtplib
import datetime
import os
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

class NotificationService:
    def __init__(self):
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK", "")
        
        # Email configuration from environment
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.alert_email = os.getenv("ALERT_EMAIL", "")
        
        if self.smtp_user and self.alert_email:
            print(f"[NotificationService] Email configured: {self.smtp_user} -> {self.alert_email}")
        else:
            print("[NotificationService] Email not configured (missing SMTP_USER or ALERT_EMAIL)")

    def send_alert(self, title, message, severity="medium", image_path=None):
        """
        Dispatches alerts to configured channels (Non-blocking).
        """
        print(f"[ALERT - {severity.upper()}] {title}: {message}")
        
        if self.discord_webhook:
            threading.Thread(target=self.send_discord, args=(title, message, severity), daemon=True).start()
    
    def send_email_with_video(self, subject: str, body: str, video_path: str = None):
        """
        Sends an email alert with optional video attachment.
        Runs in a background thread to avoid blocking.
        """
        if not self.smtp_user or not self.alert_email:
            print("[NotificationService] Cannot send email - not configured")
            return
            
        threading.Thread(
            target=self._send_email_thread,
            args=(subject, body, video_path),
            daemon=True
        ).start()
    
    def _send_email_thread(self, subject: str, body: str, video_path: str = None):
        """
        Internal thread method to send email with optional attachment.
        """
        try:
            print(f"[NotificationService] Sending email to {self.alert_email}...")
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = self.alert_email
            msg['Subject'] = f"🚨 SECURITY ALERT: {subject}"
            
            # Email body with HTML formatting
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #d32f2f;">🚨 Security Alert</h2>
                <p><strong>Time:</strong> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Event:</strong> {subject}</p>
                <hr>
                <p>{body}</p>
                <hr>
                <p style="color: #666; font-size: 12px;">
                    This is an automated alert from AI Security Guardian.
                </p>
            </body>
            </html>
            """
            msg.attach(MIMEText(html_body, 'html'))
            
            # Attach video if provided and exists
            if video_path and os.path.exists(video_path):
                print(f"[NotificationService] Attaching video: {video_path}")
                try:
                    with open(video_path, 'rb') as attachment:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(attachment.read())
                    
                    encoders.encode_base64(part)
                    filename = os.path.basename(video_path)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename= {filename}'
                    )
                    msg.attach(part)
                    print(f"[NotificationService] Video attached: {filename}")
                except Exception as e:
                    print(f"[NotificationService] Failed to attach video: {e}")
            
            # Send email via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            print(f"[NotificationService] ✅ Email sent successfully to {self.alert_email}")
            
        except Exception as e:
            print(f"[NotificationService] ❌ Email failed: {e}")
    
    def send_discord(self, title, message, severity):
        try:
            color = 16711680 if severity == "critical" else 3447003
            payload = {
                "embeds": [{
                    "title": title,
                    "description": message,
                    "color": color,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }]
            }
            requests.post(self.discord_webhook, json=payload, timeout=10)
        except Exception as e:
            print(f"Discord Alert Failed: {e}")

notification_service = NotificationService()

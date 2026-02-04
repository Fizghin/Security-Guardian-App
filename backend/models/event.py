from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from .database import Base

class SecurityEvent(Base):
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    event_type = Column(String, index=True) # "DETECTION", "ALERT", "PANIC", "AI_RESPONSE"
    description = Column(String)
    severity = Column(String) # "LOW", "MEDIUM", "HIGH", "CRITICAL"

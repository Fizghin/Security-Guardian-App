from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, Float, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    role = Column(String, default="viewer") # admin, viewer
    is_active = Column(Boolean, default=True)
    face_encoding = Column(Text, nullable=True) # Stored as JSON or blob

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    source = Column(String) # RTSP URL or int index
    zone_config = Column(Text, nullable=True) # JSON coordinates
    is_active = Column(Boolean, default=True)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    role = Column(String, default="viewer") # admin, viewer
    is_active = Column(Boolean, default=True)
    face_encoding = Column(Text, nullable=True) # Stored as JSON or blob
    created_at = Column(DateTime, default=datetime.utcnow)

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    type = Column(String) # detection, alert, system
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    description = Column(String)
    severity = Column(String) # low, medium, high, critical
    snapshot_path = Column(String, nullable=True)
    video_path = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True) # JSON string for extra data

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    summary = Column(Text, nullable=True) 
    participant_id = Column(String, nullable=True) # If known user
    transcript = Column(Text, nullable=True) # JSON of messages

class Config(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True, index=True)
    value = Column(Text)
    type = Column(String) # string, int, float, bool, json
    category = Column(String, default="general") # ai, video, system

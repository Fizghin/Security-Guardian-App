from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Detection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    class_name: str = Field("person", alias="class")
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2] in full-frame pixels
    identity: Optional[str] = None
    known: bool = False
    face_visible: bool = False
    simulated: bool = False
    # Filled in by the tracker
    track_id: Optional[int] = None
    status: Literal["known", "unknown", "pending"] = "unknown"
    match_score: Optional[float] = None


class ForensicTimelineEvent(BaseModel):
    timestamp: str
    event_type: str
    description: str
    severity: str = "INFO"


class ForensicDigest(BaseModel):
    file: str
    camera: str
    summary: str
    threat_level_peak: int
    duration_seconds: float
    key_findings: List[str]
    timeline: List[ForensicTimelineEvent]
    created_at: str


class CryptographicVerificationReceipt(BaseModel):
    file: str
    sha256_hash: str
    signature: str
    verified: bool
    tampered: bool
    chain_index: int
    timestamp: str

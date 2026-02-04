from pydantic import BaseModel, Field
from typing import List, Optional, Tuple

class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def as_list(self) -> List[float]:
        return [self.x1, self.y1, self.x2, self.y2]

class Detection(BaseModel):
    class_name: str = Field(..., alias="class")
    confidence: float
    bbox: List[float] # [x1, y1, x2, y2]
    identity: Optional[str] = None
    known: bool = False

    model_config = {
        "populate_by_name": True
    }

    @property
    def box_tuple(self) -> Tuple[float, float, float, float]:
        return tuple(self.bbox)

class SecurityEventLog(BaseModel):
    event_type: str
    description: str
    severity: str
    timestamp: Optional[float] = None

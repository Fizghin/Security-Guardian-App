from typing import List, Optional

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

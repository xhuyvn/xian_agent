from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class LifecycleEventType(str, Enum):
    ACQUISITION = "ACQUISITION"
    UPGRADE = "UPGRADE"
    CLASH = "CLASH"
    DAMAGED = "DAMAGED"
    TRANSFERRED = "TRANSFERRED"
    DESTROYED = "DESTROYED"
    FALLEN_OR_SEALED = "FALLEN_OR_SEALED"

class EntityLifecycleRecord(BaseModel):
    id: Optional[int] = None
    chapter_id: int
    entity_name: str
    entity_type: str
    event_type: LifecycleEventType
    target_or_holder: Optional[str] = Field(default=None, description="Chủ nhân, người sử dụng hoặc đối thủ")
    power_tier: Optional[str] = Field(default=None, description="Phẩm giai/Cảnh giới lúc diễn ra biến cố")
    context_details: str = Field(description="Chi tiết cụ thể của biến cố trong chương")

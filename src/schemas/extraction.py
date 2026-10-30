from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class EntityCategory(str, Enum):
    CHARACTER = "character"
    WEAPON_TREASURE = "weapon_treasure"
    PUPPET_BEAST = "puppet_beast"
    TECHNIQUE = "technique"
    DUNGEON = "dungeon"

class RawEntityAction(BaseModel):
    name: str = Field(description="Tên thực thể chuẩn hóa (ví dụ: Hàn Lập, Thanh Trúc Phong Vân Kiếm, Khôi Lỗi Cự Viên)")
    category: EntityCategory = Field(description="Phân loại thực thể")
    action_or_event: str = Field(description="Mô tả hành động hoặc biến cố cụ thể liên quan trong chương")
    tier_or_realm: Optional[str] = Field(default=None, description="Cảnh giới hoặc phẩm cấp ghi nhận trong chương")

class ChapterExtraction(BaseModel):
    chapter_id: int = Field(description="Số thứ tự chương")
    chapter_title: str = Field(description="Tiêu đề chương")
    map_location: str = Field(description="Địa danh chính diễn ra sự việc")
    time_skip_years: float = Field(default=0.0, description="Số năm trôi qua nếu có bước nhảy thời gian")
    is_map_change: bool = Field(description="Có chuyển đổi sang bản đồ/tông môn/giới diện mới không")
    is_realm_breakthrough: bool = Field(description="Có đột phá đại cảnh giới lớn không")
    is_major_conflict: bool = Field(description="Có trận đại chiến hoặc vượt cấm địa lớn không")
    micro_summary: str = Field(description="Tóm tắt vi mô 100-150 từ, cô đọng biến cố then chốt")
    entities: List[RawEntityAction] = Field(default_factory=list, description="Danh sách thực thể và biến cố liên quan")

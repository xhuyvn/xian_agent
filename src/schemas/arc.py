from pydantic import BaseModel, Field
from typing import List

class StoryArc(BaseModel):
    arc_id: int = Field(description="Thứ tự mốc đại sự kiện")
    arc_title: str = Field(description="Tên đại sự kiện (vd: Huyết Sắc Cấm Địa, Đại Chiến Loạn Tinh Hải)")
    start_chapter: int = Field(description="Chương mở đầu")
    end_chapter: int = Field(description="Chương kết thúc")
    primary_locations: List[str] = Field(description="Các địa bàn chính")
    core_conflict: str = Field(description="Mâu thuẫn hoặc mục tiêu sinh tồn cốt lõi")
    climax_event: str = Field(description="Sự kiện cao trào then chốt")
    resolution: str = Field(description="Kết cục và ảnh hưởng dài hạn")
    power_progression: str = Field(description="Sự thay đổi cảnh giới thực lực của nhân vật chính")
    key_entities_involved: List[str] = Field(description="Nhân vật, bảo vật, phó bản then chốt")
    macro_summary: str = Field(description="Tóm tắt toàn cảnh mốc sự kiện (300-500 từ)")

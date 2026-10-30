from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from src.config import settings
from src.schemas.extraction import ChapterExtraction
from src.schemas.arc import StoryArc

# 1. Ollama Model qua chuẩn OpenAI-compatible endpoint
ollama_model = OpenAIModel(
    model_name=settings.ollama_model,
    base_url=settings.ollama_base_url,
    api_key="ollama"
)

# 2. OpenRouter Model
openrouter_model = OpenAIModel(
    model_name=settings.macro_model,
    base_url=settings.openrouter_base_url,
    api_key=settings.openrouter_api_key or "sk-dummy"
)

# Agent bóc tách chương truyện (Type-safe với Pydantic-AI)
chapter_extraction_agent = Agent(
    model=ollama_model,
    result_type=ChapterExtraction,
    system_prompt=(
        "Bạn là Chuyên gia Khảo sát Dữ liệu Tiểu Thuyết Tiên Hiệp. "
        "Hãy đọc nội dung chương truyện và trích xuất thông tin cấu trúc chuẩn xác theo schema."
    ),
    retries=3
)

# Agent tổng hợp mốc sự kiện Arc
arc_synthesizer_agent = Agent(
    model=openrouter_model,
    result_type=StoryArc,
    system_prompt=(
        "Bạn là Chuyên gia Biên niên sử Tiên Hiệp. "
        "Hãy tổng hợp chuỗi tóm tắt vi mô thành một đại sự kiện (Story Arc) hoàn chỉnh."
    ),
    retries=2
)

# Agent giám định chuyên sâu thực thể
deep_dive_evaluator_agent = Agent(
    model=openrouter_model,
    result_type=str,
    system_prompt=(
        "Bạn là Chuyên gia Thẩm định Nghệ thuật & Thiết kế Thế giới Tiên Hiệp. "
        "Cung cấp bài giám định chuyên sâu 4 phần: Lai lịch & Bản chất, "
        "Biên niên sử Tiến hóa, Hiệu quả Thực chiến, và Giá trị Nghệ thuật/Ý đồ Cốt truyện."
    )
)

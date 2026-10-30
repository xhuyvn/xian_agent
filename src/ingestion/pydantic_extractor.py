import os
from typing import Optional
from src.schemas.extraction import ChapterExtraction
from src.agents.pydantic_agents import chapter_extraction_agent
from rich.console import Console

console = Console()

class PydanticBatchExtractor:
    """Bóc tách dữ liệu chương sử dụng Pydantic-AI Agent có khả năng tự sửa lỗi (Self-Correction)"""
    def __init__(self, output_dir: str = "./data/extracted_json"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def is_processed(self, chapter_id: int) -> bool:
        return os.path.exists(os.path.join(self.output_dir, f"chap_{chapter_id:04d}.json"))

    async def extract_chapter(self, chapter_id: int, title: str, text: str) -> Optional[ChapterExtraction]:
        checkpoint_path = os.path.join(self.output_dir, f"chap_{chapter_id:04d}.json")
        if self.is_processed(chapter_id):
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                return ChapterExtraction.model_validate_json(f.read())

        prompt = f"""Phân tích chương sau:
[CHƯƠNG {chapter_id}: {title}]
{text[:9000]}
"""
        try:
            # Pydantic-AI tự động thực thi schema validation và retry nếu LLM trả thiếu
            result = await chapter_extraction_agent.run(prompt)
            data: ChapterExtraction = result.data

            with open(checkpoint_path, "w", encoding="utf-8") as f:
                f.write(data.model_dump_json(indent=2))

            return data
        except Exception as e:
            console.print(f"[bold red]Lỗi Pydantic-AI khi trích xuất chương {chapter_id}: {e}[/bold red]")
            return None

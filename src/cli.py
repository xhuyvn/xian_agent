import typer
import asyncio
import os
import glob
from rich.console import Console
from src.ingestion.pydantic_extractor import PydanticBatchExtractor
from src.storage.sqlite_ledger import SQLiteLedger
from src.schemas.lifecycle import EntityLifecycleRecord, LifecycleEventType
from src.agents.graph_workflow import novel_agent_graph
from src.schemas.state import AgentState

app = typer.Typer(help="XN-Agent v2 CLI: Hệ thống Phân tích Tiên Hiệp với LangGraph & Pydantic-AI")
console = Console()

@app.command()
def ingest(raw_dir: str = typer.Option("./data/raw_chapters", help="Thư mục chứa các file text từng chương")):
    """Chạy trích xuất hàng loạt qua Pydantic-AI và Ollama Local"""
    extractor = PydanticBatchExtractor()
    ledger = SQLiteLedger()
    
    files = sorted(glob.glob(os.path.join(raw_dir, "*.txt")))
    if not files:
        console.print(f"[yellow]Không tìm thấy file txt trong {raw_dir}. Vui lòng tạo thư mục và đưa file vào.[/yellow]")
        return

    async def _process():
        console.print(f"[green]Bắt đầu batch ingestion bằng Pydantic-AI cho {len(files)} chương...[/green]")
        for idx, fpath in enumerate(files, start=1):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            title = os.path.basename(fpath).replace(".txt", "")
            extraction = await extractor.extract_chapter(chapter_id=idx, title=title, text=content)
            
            if extraction:
                for ent in extraction.entities:
                    record = EntityLifecycleRecord(
                        chapter_id=idx,
                        entity_name=ent.name,
                        entity_type=ent.category.value,
                        event_type=LifecycleEventType.CLASH,
                        power_tier=ent.tier_or_realm,
                        context_details=ent.action_or_event
                    )
                    ledger.record_event(record)
                console.print(f"[cyan]Đã xử lý & validate xong chương {idx}: {title}[/cyan]")

    asyncio.run(_process())

@app.command()
def query(prompt: str = typer.Option(..., help="Nội dung câu hỏi phân tích hoặc tóm tắt mốc")):
    """Truy vấn thông qua LangGraph State Machine"""
    console.print(f"[bold green]Kích hoạt LangGraph điều phối yêu cầu:[/bold green] {prompt}")
    
    initial_state: AgentState = {
        "query": prompt,
        "intent": None,
        "target_entity": None,
        "target_aspect": None,
        "retrieved_events": [],
        "retrieved_arcs": [],
        "final_output": None,
        "errors": []
    }

    async def _run():
        result = await novel_agent_graph.ainvoke(initial_state)
        console.print("\n[bold yellow]=== KẾT QUẢ TỪ LANGGRAPH + PYDANTIC-AI ===[/bold yellow]\n")
        console.print(result.get("final_output", "Không nhận được phản hồi."))

    asyncio.run(_run())

if __name__ == "__main__":
    app()

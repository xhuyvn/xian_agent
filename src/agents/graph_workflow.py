from langgraph.graph import StateGraph, END
from src.schemas.state import AgentState
from src.storage.sqlite_ledger import SQLiteLedger
from src.storage.qdrant_client import QdrantManager
from src.agents.pydantic_agents import deep_dive_evaluator_agent
import httpx
from src.config import settings

ledger = SQLiteLedger()
qdrant = QdrantManager()

def router_node(state: AgentState) -> AgentState:
    """Phân tích intent của người dùng"""
    query = state["query"].lower()
    if any(k in query for k in ["mốc", "thời gian", "giai đoạn", "tóm tắt cốt truyện", "arc"]):
        state["intent"] = "timeline"
    else:
        state["intent"] = "deep_dive"
        state["target_entity"] = state["query"]
        state["target_aspect"] = "toàn diện"
    return state

def route_condition(state: AgentState) -> str:
    return "timeline_node" if state.get("intent") == "timeline" else "deep_dive_node"

def timeline_node(state: AgentState) -> AgentState:
    """Xử lý truy vấn dòng thời gian"""
    prompt = f"""Tra cứu các mốc đại sự kiện tương ứng với yêu cầu:
'{state['query']}'
Trình bày tóm tắt: Bối cảnh -> Diễn biến cao trào -> Kết cục và ảnh hưởng dài hạn."""
    
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json"
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json={
                "model": settings.macro_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }
        )
        state["final_output"] = resp.json()["choices"][0]["message"]["content"]
    return state

async def deep_dive_node(state: AgentState) -> AgentState:
    """Xử lý truy vấn giám định chuyên sâu qua Pydantic-AI Agent"""
    entity_name = state.get("target_entity", state["query"])
    records = ledger.get_timeline_for_entity(entity_name)
    
    if records:
        timeline_text = "\n".join([
            f"[Chương {r.chapter_id}] ({r.event_type}): {r.context_details} | Phẩm giai: {r.power_tier or 'N/A'}"
            for r in records
        ])
    else:
        timeline_text = f"Không có bản ghi trực tiếp trong sổ cái cho '{entity_name}'. Phân tích theo tri thức nền tảng."

    prompt = f"""Yêu cầu giám định đối tượng: [{entity_name}]
Dữ liệu sổ cái lịch sử biến cố:
{timeline_text}

Hãy đưa ra bài đánh giá chuyên sâu gồm 4 phần:
1. Lai lịch & Bản chất Cốt lõi
2. Biên niên sử Tiến hóa & Biến chuyển Thời gian
3. Đánh giá Hiệu quả Thực chiến & Chiến tích Điển hình
4. Giá trị Nghệ thuật & Ý đồ Cốt truyện của Tác giả
"""
    result = await deep_dive_evaluator_agent.run(prompt)
    state["final_output"] = result.data
    return state

# Khởi tạo LangGraph StateGraph
workflow = StateGraph(AgentState)
workflow.add_node("router", router_node)
workflow.add_node("timeline_node", timeline_node)
workflow.add_node("deep_dive_node", deep_dive_node)

workflow.set_entry_point("router")
workflow.add_conditional_edges(
    "router",
    route_condition,
    {
        "timeline_node": "timeline_node",
        "deep_dive_node": "deep_dive_node"
    }
)
workflow.add_edge("timeline_node", END)
workflow.add_edge("deep_dive_node", END)

novel_agent_graph = workflow.compile()

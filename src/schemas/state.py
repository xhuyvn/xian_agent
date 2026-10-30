from typing import TypedDict, List, Optional, Dict, Any

class AgentState(TypedDict):
    """Trạng thái luồng điều phối của LangGraph"""
    query: str
    intent: Optional[str]                     # 'timeline' | 'deep_dive'
    target_entity: Optional[str]              # Tên nhân vật/pháp bảo/phó bản
    target_aspect: Optional[str]              # Khía cạnh cần tập trung
    retrieved_events: List[Dict[str, Any]]    # Bản ghi từ SQLite Ledger
    retrieved_arcs: List[Dict[str, Any]]      # Bản ghi từ Qdrant Arcs
    final_output: Optional[str]               # Kết quả trả về cuối cùng
    errors: List[str]

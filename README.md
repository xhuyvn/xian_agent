# Xianxia & Long-Novel Agentic Architecture System (XN-Agent v2)
## Nâng cấp: Tích hợp LangGraph & Pydantic-AI

Hệ thống AI Agent phân tích và tóm tắt tiểu thuyết trường thiên (hàng triệu chữ, hàng nghìn chương) được chuẩn hóa theo kiến trúc công nghiệp:
- **LangGraph**: Điều phối đồ thị trạng thái vĩ mô (StateGraph, checkpointing, branching, multi-agent cyclical flow).
- **Pydantic-AI**: Quản trị thực thi vi mô của từng Agent, đảm bảo type-safe structured output, retry tự động khi sai schema, hỗ trợ cả Ollama (qua OpenAI-compatible endpoint) và OpenRouter.
- **Dynamic Ledger & Vector Store**: Kết hợp SQLite (Temporal Lifecycle Ledger) và Qdrant (Hybrid search & Payload filtering).

---

## Phân công trách nhiệm: LangGraph vs Pydantic-AI

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LANGGRAPH (StateGraph)                          │
│   - Quản trị luồng trạng thái tổng thể (Workflow State)                │
│   - Điều phối vòng lặp Ingestion Loop & Phát hiện ranh giới Arc        │
│   - Định tuyến linh hoạt giữa Timeline Agent và Deep-Dive Agent        │
│   - Checkpoint trạng thái đồ thị (Graph Memory & Resilience)           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Kích hoạt từng Node
┌───────────────────────────────────▼────────────────────────────────────┐
│                       PYDANTIC-AI (Node Agents)                        │
│   - Extraction Agent: Ép kiểu dữ liệu chương truyện với Schema Pydantic│
│   - Arc Synthesizer Agent: Tổng hợp mốc sự kiện với validation chặt chẽ│
│   - Deep-Dive Evaluator Agent: Giám định pháp bảo, vũ khí, nhân vật     │
│   - Tự động sửa lỗi (Self-Correction/Retry) khi LLM sinh JSON lệch chuẩn│
└────────────────────────────────────────────────────────────────────────┘
```

## Cấu trúc thư mục

```
novel_agent_architecture/
├── ARCHITECTURE_BLUEPRINT.md        # Đặc tả kiến trúc LangGraph + Pydantic-AI
├── docker-compose.yml               # Qdrant & Redis
├── requirements.txt                 # LangGraph, Pydantic-AI, Qdrant, etc.
├── .env.example                     # Cấu hình môi trường mẫu
├── configs/
│   └── config.yaml                  # Tham số hệ thống, trọng số cắt Arc
└── src/
    ├── config.py                    # Cấu hình Pydantic Settings
    ├── cli.py                       # CLI thực thi Ingestion & Query
    ├── schemas/
    │   ├── state.py                 # LangGraph AgentState schema
    │   ├── extraction.py            # Pydantic-AI output schema cho chương
    │   ├── lifecycle.py             # Schema sổ cái biến cố thực thể
    │   └── arc.py                   # Schema mốc sự kiện StoryArc
    ├── storage/
    │   ├── qdrant_client.py         # Quản lý 3 collections Qdrant
    │   └── sqlite_ledger.py         # Sổ cái biến cố theo thời gian (SQLite)
    ├── ingestion/
    │   └── pydantic_extractor.py    # Extractor sử dụng Pydantic-AI Agent
    ├── timeline/
    │   └── arc_detector.py          # Thuật toán tính điểm Changepoint
    └── agents/
        ├── pydantic_agents.py       # Khởi tạo các Pydantic-AI Agents chuyên trách
        └── graph_workflow.py        # LangGraph StateGraph điều phối toàn hệ thống
```

## Khởi chạy nhanh

1. Khởi động Qdrant:
   ```bash
   docker-compose up -d
   ```
2. Cài đặt thư viện:
   ```bash
   pip install -r requirements.txt
   ```
3. Chạy Ingestion qua Pydantic-AI & LangGraph:
   ```bash
   python -m src.cli ingest --raw-dir ./data/raw_chapters/
   ```
4. Truy vấn điều phối qua LangGraph:
   ```bash
   python -m src.cli query --prompt "Phân tích quá trình tiến hóa và chiến tích của Thanh Trúc Phong Vân Kiếm"
   ```

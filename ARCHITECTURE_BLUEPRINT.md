# BẢN ĐẶC TẢ KIẾN TRÚC KỸ THUẬT v2 (SYSTEM ARCHITECTURE BLUEPRINT)
## Nâng cấp: Hợp nhất Kiến trúc LangGraph & Pydantic-AI

---

## 1. Lý do tích hợp LangGraph & Pydantic-AI

Trong bản phác thảo ban đầu, hệ thống sử dụng code thuần (`httpx` + scripts tuyến tính) nhằm minh họa cơ chế bóc tách dữ liệu. Tuy nhiên, khi đối mặt với **2.673 chương truyện (~5,6 triệu từ)**, mô hình thuần scripts sẽ gặp các giới hạn:
1. **Thiếu khả năng phục hồi luồng phức tạp (Graph State Resilience)**: Nếu quá trình tổng hợp Arc hoặc phân tích chéo nhiều thực thể gặp lỗi, scripts tuyến tính khó rollback hoặc rẽ nhánh điều kiện.
2. **Schema Drift**: LLM cục bộ (9B/14B) thỉnh thoảng sinh sai cấu trúc JSON. Việc xử lý lỗi thủ công làm code phình to và dễ crash.

### Sự kết hợp hoàn hảo giữa LangGraph và Pydantic-AI:
* **LangGraph đóng vai trò "Bộ não điều phối luồng" (Orchestration Engine)**:
  - Quản lý trạng thái đa tầng (`AgentState`).
  - Hỗ trợ rẽ nhánh có điều kiện (Conditional Edges): phân luồng câu hỏi, tự động chuyển từ tóm tắt mốc sang điều tra bảo vật khi phát hiện đối tượng liên quan.
  - Cung cấp cơ chế Checkpoint/Persistence: lưu giữ trạng thái đồ thị vào SQLite/Postgres.
* **Pydantic-AI đóng vai trò "Công cụ thực thi type-safe" (Execution Engine)**:
  - Được xây dựng bởi chính đội ngũ Pydantic, tối ưu hóa triệt để cho việc xuất dữ liệu có cấu trúc.
  - Tự động bắt lỗi validation của Pydantic và gửi lại LLM để sửa lỗi (Self-Correction Loop).
  - Tương thích liền mạch với cả Ollama (thông qua giao thức OpenAI-compatible tại `http://localhost:11434/v1`) và OpenRouter.

---

## 2. Kiến trúc Đồ thị LangGraph (StateGraph Topology)

```
                       [User Query / Ingestion Task]
                                     │
                                     ▼
                            (Input Router Node)
                                     │
                    ┌────────────────┴────────────────┐
                    │ [intent == 'timeline']          │ [intent == 'deep_dive']
                    ▼                                 ▼
         (Timeline Retrieval Node)         (Entity Timeline Query Node)
                    │                                 │
                    ▼                                 ▼
          (Arc Synthesizer Node)            (Deep-Dive Evaluator Node)
           (Pydantic-AI Agent)                 (Pydantic-AI Agent)
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                                     ▼
                         (Final Response Formatter)
                                     │
                                     ▼
                                   [END]
```

---

## 3. Quản trị State trong LangGraph (`AgentState`)

```python
class AgentState(TypedDict):
    query: str
    intent: str  # "timeline" | "deep_dive" | "ingest"
    target_entity: Optional[str]
    target_aspect: Optional[str]
    retrieved_events: List[dict]
    retrieved_arcs: List[dict]
    final_output: str
    errors: List[str]
```

---

## 4. Đặc tả Pydantic-AI Agents

### A. Extraction Agent (Chạy trên Ollama Qwen Local)
* Nhiệm vụ: Đọc raw text chương $ightarrow$ trả về đối tượng `ChapterExtraction`.
* Cấu hình: `result_type=ChapterExtraction`, `retries=3`. Nếu Ollama trả thiếu trường, Pydantic-AI tự phản hồi lỗi validation để Qwen sửa lại ngay lập tức.

### B. Story Arc Synthesizer Agent (Chạy trên OpenRouter DeepSeek)
* Nhiệm vụ: Tiếp nhận danh sách `micro_summaries` $ightarrow$ sinh ra đối tượng `StoryArc` hoàn chỉnh với logic nhân quả, mâu thuẫn cao trào và chuyển biến thực lực.

### C. Deep-Dive Evaluator Agent (Chạy trên OpenRouter DeepSeek/GPT-4o)
* Nhiệm vụ: Đọc chuỗi biên niên sử thời gian của thực thể $ightarrow$ xuất bài thẩm định chuyên sâu theo 4 phần chuẩn mực (Lai lịch, Tiến hóa, Thực chiến, Ý đồ tác giả).

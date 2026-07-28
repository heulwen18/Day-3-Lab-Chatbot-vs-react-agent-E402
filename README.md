# 🔮 AstroAgent — Luận giải tử vi bằng ReAct Agent

AstroAgent là ứng dụng minh họa sự khác nhau giữa **Chatbot Baseline** và **ReAct Agent** trong bài Lab 3. Hệ thống nhận thông tin sinh, kiểm tra dữ liệu, gọi công cụ luận giải và trả kết quả bằng tiếng Việt.

> **Lưu ý:** Kết quả chỉ mang tính tham khảo và giải trí. Ứng dụng không thay thế tư vấn y tế, tài chính hoặc pháp lý.

---

## 1. Chức năng chính

- So sánh Chatbot Baseline và ReAct Agent.
- Kiểm tra thông tin ngày sinh, giờ sinh, giới tính, nơi sinh.
- Luận giải:
  - Tổng quan.
  - Học tập và sự nghiệp.
  - Tình cảm và giao tiếp.
  - Xu hướng theo năm.
  - Độ tương hợp giữa hai người.
- Hỗ trợ Gemini, OpenAI, Anthropic, OpenRouter và Mock offline.
- Lưu số vòng lặp, số lần gọi tool, trạng thái Guardrail và ReAct trace.
- Chạy bằng Terminal hoặc giao diện web.

---

## 2. Cấu trúc dự án

```text
project/
├── config/
│   └── test_cases.json
├── docs/
│   ├── CODELAB.md
│   ├── PHAN_CONG_CONG_VIEC.md
│   └── trace_eval.md
├── src/
│   ├── app.py
│   ├── prompts.py
│   ├── providers.py
│   ├── tools.py
│   └── web.html
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
```

| File | Vai trò |
|---|---|
| `src/app.py` | Chạy Baseline, ReAct loop, tool, Guardrail, CLI và web server |
| `src/prompts.py` | Prompt cho Baseline, ReAct Agent và cấu hình Guardrail |
| `src/providers.py` | Kết nối Gemini, OpenAI, Anthropic, OpenRouter hoặc Mock |
| `src/tools.py` | Kiểm tra dữ liệu sinh và các tool luận giải |
| `src/web.html` | Giao diện web |
| `config/test_cases.json` | Bộ 5 test case của bài Lab |
| `docs/trace_eval.md` | Ghi trace và đánh giá kết quả |

---

## 3. Luồng hoạt động

### Chatbot Baseline

```text
Câu hỏi → LLM → Câu trả lời
```

Baseline chỉ gọi LLM một lần và không dùng tool.

### ReAct Agent

```text
Question → Thought → Action → Tool → Observation → Final Answer
```

Agent chỉ được gọi các tool có trong `AVAILABLE_TOOLS`.

---

## 4. Danh sách tool

| Tool | Chức năng |
|---|---|
| `validate_birth_info` | Kiểm tra và chuẩn hóa thông tin sinh |
| `interpret_tuvi_overview` | Luận giải tổng quan |
| `interpret_study_and_career` | Luận giải học tập và sự nghiệp |
| `interpret_relationships` | Luận giải tình cảm và giao tiếp |
| `interpret_yearly_fortune` | Luận giải xu hướng theo năm |
| `interpret_compatibility` | So sánh độ tương hợp giữa hai người |

Dữ liệu đầu vào:

```text
birth_date: DD/MM/YYYY
birth_time: HH:MM
gender: nam hoặc nữ
birth_place: nơi sinh
calendar_type: solar hoặc lunar
```

---

## 5. Cài đặt

### Tạo môi trường ảo trên Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### Cài thư viện

```powershell
python -m pip install -r requirements.txt
```

Các thư viện chính:

```text
python-dotenv
google-genai
requests
```

Khi dùng OpenAI hoặc Anthropic:

```powershell
python -m pip install openai
python -m pip install anthropic
```

---

## 6. Cấu hình `.env`

Ví dụ dùng Gemini:

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.6-flash

GEMINI_API_KEY=your_api_key
GEMINI_MODEL=gemini-3.6-flash
```

Các giá trị hỗ trợ cho `LLM_PROVIDER`:

```text
gemini
openai
anthropic
openrouter
mock
```

API key tương ứng:

```env
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...
```

Lưu ý:

- `LLM_PROVIDER` chọn mô hình điều khiển Baseline và ReAct Agent.
- Các tool `interpret_*` trong `tools.py` vẫn gọi Gemini API.
- Muốn chạy offline, dùng:

```env
LLM_PROVIDER=mock
```

---

## 7. Chạy ứng dụng

### Chạy toàn bộ test case

```powershell
python src/app.py
```

### Chỉ chạy ReAct Agent

```powershell
python src/app.py --mode agent
```

### Chỉ chạy Baseline

```powershell
python src/app.py --mode baseline
```

### Chạy một test case

```powershell
python src/app.py --mode agent --case 3
```

### Nhập thông tin trực tiếp trong Terminal

```powershell
python src/app.py --interactive
```

### Chạy chế độ offline

```powershell
$env:LLM_PROVIDER="mock"
python src/app.py --mode agent
```

### Chạy giao diện web

```powershell
python src/app.py --serve --host 127.0.0.1 --port 8000
```

Mở trình duyệt tại:

```text
http://127.0.0.1:8000/
```

Dừng ứng dụng bằng:

```text
Ctrl + C
```

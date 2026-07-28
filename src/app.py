"""Core application for comparing a baseline chatbot with a ReAct agent."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

# Allow ``python src/app.py`` to import modules next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from prompts import CHATBOT_BASELINE_PROMPT, MAX_ITERATIONS, REACT_SYSTEM_PROMPT
from providers import get_llm_provider
from tools import AVAILABLE_TOOLS

try:
    from prompts import GUARDRAIL_FALLBACK_MESSAGE
except ImportError:
    GUARDRAIL_FALLBACK_MESSAGE = (
        "Hệ thống đã đạt giới hạn số bước suy luận an toàn. "
        "Mình chưa đủ dữ liệu để đưa ra kết luận."
    )

load_dotenv()

DISCLAIMER = "Kết quả chỉ mang tính tham khảo và giải trí."

ACTION_RE = re.compile(
    r"^\s*Action\s*:\s*([A-Za-z_]\w*)\s*(\[[^\n]*\]|\([^\n]*\))\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FINAL_RE = re.compile(
    r"^\s*Final\s+Answer\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE | re.DOTALL
)


@dataclass
class AgentResult:
    """Observable result returned by one ReAct run."""

    answer: str
    iterations: int
    tool_calls: int
    stopped_by_guardrail: bool = False
    trace: list[dict[str, str]] = field(default_factory=list)


def load_test_cases() -> list[dict[str, Any]]:
    """Load and validate test cases from ``config/test_cases.json``."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config", "test_cases.json")

    try:
        with open(config_path, encoding="utf-8") as file:
            test_cases = json.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Không tìm thấy file test case: {config_path}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"File config/test_cases.json không phải JSON hợp lệ: {error}"
        ) from error

    if not isinstance(test_cases, list):
        raise ValueError("config/test_cases.json phải chứa một JSON array.")
    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Test case #{index} phải là một JSON object.")
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError(f"Test case #{index} thiếu question dạng chuỗi.")

    return test_cases


class OfflineAstrologyProvider:
    """Deterministic ReAct provider used when ``LLM_PROVIDER=mock``."""

    model_name = "Offline Astrology Mock"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        text = prompt.lower()
        is_baseline = system_prompt == CHATBOT_BASELINE_PROMPT

        if is_baseline:
            if "khoa học chứng minh" in text:
                return f"Tử vi chưa được khoa học chứng minh là phương pháp dự đoán. {DISCLAIMER}"
            if "bệnh nặng" in text:
                return (
                    "Mình không thể dự đoán bệnh tật. Bạn nên trao đổi với chuyên gia "
                    f"y tế khi có lo ngại về sức khỏe. {DISCLAIMER}"
                )
            return (
                "Mình không có công cụ kiểm chứng dữ liệu sinh nên không thể đưa ra "
                f"phân tích cá nhân hóa có căn cứ. {DISCLAIMER}"
            )

        if "observation:" in text:
            observation = prompt.rsplit("Observation:", 1)[-1].strip()
            if observation.startswith(("TOOL_ERROR", "FORMAT_ERROR")):
                return (
                    "Thought: Observation báo lỗi nên tôi phải dừng thay vì bịa kết quả.\n"
                    "Final Answer: Dữ liệu hoặc công cụ hiện chưa hợp lệ. Vui lòng "
                    f"kiểm tra thông tin và thử lại. {DISCLAIMER}"
                )
            return f"Thought: Tôi đã có dữ liệu từ tool.\nFinal Answer: {observation}"

        if "khoa học chứng minh" in text:
            return (
                "Thought: Đây là kiến thức chung, không cần tool.\n"
                "Final Answer: Tử vi chưa được khoa học chứng minh là phương pháp "
                f"dự đoán và không thay thế tư vấn chuyên môn. {DISCLAIMER}"
            )
        if "bệnh nặng" in text:
            return (
                "Thought: Đây là yêu cầu dự đoán y khoa bị cấm và dữ liệu sinh không hợp lệ.\n"
                "Final Answer: Mình không thể dự đoán bệnh tật hoặc tuổi thọ. Ngày "
                "32/13/2026 và giờ 25:99 cũng không hợp lệ; hãy tham khảo chuyên gia "
                f"y tế nếu bạn lo về sức khỏe. {DISCLAIMER}"
            )
        if "độ tương thích" in text:
            profiles = re.findall(
                r"người\s+[12]:\s*(nam|nữ),\s*sinh\s+"
                r"(\d{2}/\d{2}/\d{4})\s+lúc\s+(\d{2}:\d{2})\s+tại\s+(.+?)"
                r"(?=;\s*người\s+[12]:|;\s*cả hai)",
                prompt,
                re.IGNORECASE,
            )
            if len(profiles) == 2:
                first_gender, first_date, first_time, first_place = profiles[0]
                second_gender, second_date, second_time, second_place = profiles[1]
                calendar_type = "lunar" if "âm lịch" in text else "solar"
                arguments = [
                    first_date,
                    first_time,
                    first_gender.lower(),
                    first_place.strip(),
                    second_date,
                    second_time,
                    second_gender.lower(),
                    second_place.strip(),
                    calendar_type,
                    "Phân tích độ tương thích tình cảm",
                ]
                action_arguments = ", ".join(repr(value) for value in arguments)
                return (
                    "Thought: Cần phân tích dữ liệu sinh của cả hai người.\n"
                    f"Action: interpret_compatibility[{action_arguments}]"
                )
            return (
                "Thought: Cần phân tích dữ liệu sinh của cả hai người.\n"
                "Action: interpret_compatibility[\"12/08/2003\", \"14:30\", \"nữ\", "
                "\"Hà Nội\", \"05/11/2001\", \"08:15\", \"nam\", \"Đà Nẵng\", "
                "\"solar\", \"Phân tích độ tương thích\"]"
            )
        if "công nghệ thông tin" in text:
            return (
                "Thought: Cần tool phân tích học tập và sự nghiệp.\n"
                "Action: interpret_study_and_career[\"05/11/2001\", \"08:15\", "
                "\"nam\", \"Đà Nẵng\", \"solar\", \"công nghệ thông tin\"]"
            )
        if "tử vi tổng quan" in text:
            date_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", prompt)
            time_match = re.search(r"\b(\d{2}:\d{2})\b", prompt)
            gender_match = re.search(r"cho\s+(nam|nữ)\s+sinh", text)
            place_match = re.search(r"\btại\s+(.+?),\s*dùng\s+", prompt, re.IGNORECASE)
            calendar_type = "lunar" if "âm lịch" in text else "solar"
            if not all((date_match, time_match, gender_match, place_match)):
                return (
                    "Thought: Chưa trích xuất đủ thông tin sinh.\n"
                    "Final Answer: Vui lòng kiểm tra ngày, giờ, giới tính và nơi sinh. "
                    f"{DISCLAIMER}"
                )
            arguments = [
                date_match.group(1),
                time_match.group(1),
                gender_match.group(1),
                place_match.group(1).strip(),
                calendar_type,
                "Luận giải tổng quan",
            ]
            return (
                "Thought: Cần tool luận giải tổng quan theo thông tin sinh.\n"
                f"Action: interpret_tuvi_overview[{', '.join(repr(value) for value in arguments)}]"
            )
        return (
            "Thought: Chưa đủ dữ liệu sinh bắt buộc.\n"
            "Final Answer: Vui lòng cung cấp ngày, giờ, giới tính, nơi sinh và loại "
            f"lịch. {DISCLAIMER}"
        )


def _tool_contract() -> str:
    """Describe the live registry so stale prompt tool names cannot be executed."""
    lines = []
    for name, function in AVAILABLE_TOOLS.items():
        summary = inspect.getdoc(function) or "Không có mô tả."
        summary = summary.splitlines()[0]
        lines.append(f"- {name}{inspect.signature(function)}: {summary}")
    return "\n".join(lines)


def _agent_system_prompt() -> str:
    return f"""{REACT_SYSTEM_PROMPT.rstrip()}

### TOOL REGISTRY THỰC TẾ (ưu tiên tuyệt đối nếu danh sách phía trên bị cũ)
{_tool_contract()}

Chỉ được gọi tool trong TOOL REGISTRY THỰC TẾ.
Action chấp nhận một trong hai dạng:
- Action: tool_name["arg1", "arg2"]
- Action: tool_name[{{"parameter_name": "value"}}]
Mỗi phản hồi chỉ có một Action hoặc một Final Answer, không tự tạo Observation.
"""


def run_baseline_chatbot(user_query: str, provider, *, verbose: bool = True) -> str:
    """Make exactly one LLM call and never expose tools to the baseline."""
    if verbose:
        print(f"\n[CHATBOT BASELINE] Câu hỏi: {user_query}")
    response = provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)
    response = str(response or "").strip() or "Chatbot không trả về nội dung."
    if verbose:
        print(f"Chatbot trả lời:\n{response}")
    return response


def _parse_arguments(raw_arguments: str) -> tuple[list[Any], dict[str, Any]]:
    """Parse action arguments without evaluating executable Python code."""
    opening, closing = raw_arguments[0], raw_arguments[-1]
    if (opening, closing) not in {("[", "]"), ("(", ")")}:
        raise ValueError("tham số phải nằm trong [] hoặc ().")

    content = raw_arguments[1:-1].strip()
    if not content:
        return [], {}

    try:
        value = ast.literal_eval(f"[{content}]")
    except (SyntaxError, ValueError) as error:
        raise ValueError("tham số không đúng cú pháp chuỗi/list/dict Python.") from error

    if len(value) == 1 and isinstance(value[0], dict):
        return [], value[0]
    return value, {}


def _parse_response(response: str) -> tuple[str, Any]:
    final_match = FINAL_RE.search(response)
    if final_match:
        return "final", final_match.group(1).strip()

    action_match = ACTION_RE.search(response)
    if action_match:
        args, kwargs = _parse_arguments(action_match.group(2))
        return "action", (action_match.group(1), args, kwargs)

    raise ValueError("phản hồi phải chứa đúng 'Action:' hoặc 'Final Answer:'.")


def _offline_tool_result(name: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    """Produce a grounded-looking demo Observation without external API calls."""
    values = kwargs or {str(index): value for index, value in enumerate(args)}
    if name == "interpret_compatibility":
        body = (
            "Hai người có thể bổ trợ nhau khi giao tiếp thẳng thắn. Khác biệt trong "
            "cách ra quyết định có thể gây hiểu lầm, vì vậy nên thống nhất kỳ vọng và "
            "không dùng kết quả này để quyết định mối quan hệ."
        )
    elif name == "interpret_study_and_career":
        body = (
            "Điểm mạnh tham khảo gồm tư duy phân tích và khả năng học qua thực hành. "
            "Nên đánh giá nghề nghiệp bằng năng lực, sở thích và trải nghiệm thực tế, "
            "không dựa riêng vào tử vi."
        )
    elif name == "interpret_relationships":
        body = "Nên giao tiếp trực tiếp, lắng nghe và tôn trọng ranh giới của nhau."
    elif name == "interpret_yearly_fortune":
        body = "Nên ưu tiên kế hoạch linh hoạt, phát triển kỹ năng và quyết định dựa trên dữ liệu."
    else:
        body = (
            "Bản mô phỏng cho thấy xu hướng chủ động học hỏi và phản tỉnh. Không nên "
            "suy diễn nội dung này thành dự đoán chắc chắn về tương lai."
        )
    return f"OFFLINE_MOCK_RESULT: {body}\nDữ liệu tool: {values}\n{DISCLAIMER}"


def _execute_tool(
    name: str,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    offline: bool = False,
) -> str:
    function = AVAILABLE_TOOLS.get(name)
    if function is None:
        valid_names = ", ".join(sorted(AVAILABLE_TOOLS))
        return f"TOOL_ERROR: Tool '{name}' không tồn tại. Tool hợp lệ: {valid_names}."

    try:
        inspect.signature(function).bind(*args, **kwargs)
    except TypeError as error:
        return f"TOOL_ERROR: Tham số của '{name}' không hợp lệ: {error}"

    if offline and name != "validate_birth_info":
        return _offline_tool_result(name, args, kwargs)

    try:
        result = function(*args, **kwargs)
        return str(result) if result is not None else "TOOL_ERROR: Tool không trả về dữ liệu."
    except Exception as error:  # Tool failures are observations, never app crashes.
        return f"TOOL_ERROR: Tool '{name}' gặp lỗi: {type(error).__name__}: {error}"


def run_react_agent(user_query: str, provider, *, verbose: bool = True) -> AgentResult:
    """Run the Thought -> Action -> Observation loop with bounded recovery."""
    transcript = f"Question: {user_query}"
    trace: list[dict[str, str]] = []
    seen_actions: dict[str, int] = {}
    tool_calls = 0

    if verbose:
        print(f"\n[REACT AGENT] Câu hỏi: {user_query}")

    for step in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n--- Vòng lặp ReAct ({step}/{MAX_ITERATIONS}) ---")

        raw_response = provider.generate(transcript, system_prompt=_agent_system_prompt())
        response = str(raw_response or "").strip()
        if verbose:
            print(response or "[Phản hồi rỗng]")

        event = {"step": str(step), "model_response": response}
        try:
            kind, payload = _parse_response(response)
        except ValueError as error:
            observation = f"FORMAT_ERROR: {error}"
            event["observation"] = observation
            trace.append(event)
            transcript += f"\n\n{response}\nObservation: {observation}"
            if verbose:
                print(f"Observation: {observation}")
            continue

        if kind == "final":
            event["final_answer"] = payload
            trace.append(event)
            return AgentResult(payload, step, tool_calls, trace=trace)

        tool_name, args, kwargs = payload
        action_key = json.dumps(
            [tool_name, args, kwargs], ensure_ascii=False, sort_keys=True, default=str
        )
        seen_actions[action_key] = seen_actions.get(action_key, 0) + 1

        if seen_actions[action_key] > 1:
            observation = (
                "TOOL_ERROR: Action này đã được thực hiện trước đó. "
                "Hãy dùng Observation đã có, đổi Action, hoặc trả Final Answer."
            )
        else:
            observation = _execute_tool(
                tool_name,
                args,
                kwargs,
                offline=isinstance(provider, OfflineAstrologyProvider),
            )
            tool_calls += 1

        event.update({"action": action_key, "observation": observation})
        trace.append(event)
        transcript += f"\n\n{response}\nObservation: {observation}"
        if verbose:
            print(f"Observation: {observation}")

    if verbose:
        print(f"\nGUARDRAIL: Đã đạt giới hạn {MAX_ITERATIONS} bước.")
        print(GUARDRAIL_FALLBACK_MESSAGE)
    return AgentResult(
        GUARDRAIL_FALLBACK_MESSAGE,
        MAX_ITERATIONS,
        tool_calls,
        stopped_by_guardrail=True,
        trace=trace,
    )


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo Chatbot Baseline và ReAct Agent")
    parser.add_argument("--mode", choices=("baseline", "agent", "both"), default="both")
    parser.add_argument("--case", type=int, help="ID test case; mặc định chạy tất cả")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Nhập thông tin sinh và nhận luận giải tử vi tổng quan",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Chạy demo web và API tại http://host:port/",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host để mở demo web")
    parser.add_argument("--port", type=int, default=8000, help="Port cho demo web")
    return parser.parse_args()


def _read_required(label: str) -> str:
    """Read a required interactive value without accepting blank input."""
    while True:
        value = input(label).strip()
        if value:
            return value
        print("Thông tin này không được để trống.")


def _read_birth_profile(person_label: str) -> dict[str, str]:
    """Collect one person's birth profile from the terminal."""
    print(f"\n--- {person_label} ---")
    return {
        "birth_date": _read_required("Ngày sinh (DD/MM/YYYY): "),
        "birth_time": _read_required("Giờ sinh (HH:MM): "),
        "gender": _read_required("Giới tính (nam/nữ): "),
        "birth_place": _read_required("Nơi sinh: "),
    }


def _validate_profile(profile: dict[str, str], calendar_type: str) -> str:
    return _execute_tool(
        "validate_birth_info",
        [
            profile["birth_date"],
            profile["birth_time"],
            profile["gender"],
            profile["birth_place"],
            calendar_type,
        ],
        {},
    )


def run_interactive(provider) -> AgentResult | None:
    """Run a personal reading or a two-person compatibility analysis."""
    print("\n" + "=" * 58)
    print("TRỢ LÝ LUẬN GIẢI TỬ VI")
    print("=" * 58)
    print("1. Luận giải tử vi cá nhân")
    print("2. Tính độ tương thích ghép đôi (lover)")

    while True:
        analysis_type = input("Chọn chức năng (1/2): ").strip()
        if analysis_type in {"1", "2"}:
            break
        print("Vui lòng nhập 1 hoặc 2.")

    if analysis_type == "2":
        first = _read_birth_profile("NGƯỜI THỨ NHẤT")
        second = _read_birth_profile("NGƯỜI THỨ HAI")
        calendar_input = input(
            "Loại lịch của cả hai (solar/lunar, mặc định solar): "
        ).strip()
        calendar_type = calendar_input or "solar"

        for label, profile in (("Người thứ nhất", first), ("Người thứ hai", second)):
            validation = _validate_profile(profile, calendar_type)
            print(f"\nKiểm tra {label.lower()}:\n{validation}")
            if validation.startswith("TOOL_ERROR"):
                print(f"Không thể ghép đôi vì thông tin của {label.lower()} không hợp lệ.")
                return None

        calendar_label = "âm lịch" if calendar_type.lower() == "lunar" else "dương lịch"
        query = (
            f"Phân tích độ tương thích tình cảm. Người 1: {first['gender']}, sinh "
            f"{first['birth_date']} lúc {first['birth_time']} tại {first['birth_place']}; "
            f"Người 2: {second['gender']}, sinh {second['birth_date']} lúc "
            f"{second['birth_time']} tại {second['birth_place']}; cả hai dùng {calendar_label}."
        )
    else:
        profile = _read_birth_profile("THÔNG TIN CỦA BẠN")
        calendar_input = input("Loại lịch (solar/lunar, mặc định solar): ").strip()
        calendar_type = calendar_input or "solar"

        validation = _validate_profile(profile, calendar_type)
        print(f"\nKiểm tra dữ liệu:\n{validation}")
        if validation.startswith("TOOL_ERROR"):
            print("Không thể luận giải cho đến khi thông tin sinh hợp lệ.")
            return None

        query = (
            f"Hãy luận giải tử vi tổng quan cho {profile['gender']} sinh ngày "
            f"{profile['birth_date']} lúc {profile['birth_time']} tại "
            f"{profile['birth_place']}, dùng "
            f"{'dương lịch' if calendar_type.lower() == 'solar' else 'âm lịch'}."
        )
    result = run_react_agent(query, provider)
    print(
        f"Telemetry: iterations={result.iterations}, "
        f"tool_calls={result.tool_calls}, guardrail={result.stopped_by_guardrail}"
    )
    return result


def _run_web_demo_server(provider, host: str, port: int) -> None:
    """Serve the standalone web demo and the model proxy API."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web_path = os.path.join(project_root, "src", "web.html")

    class DemoRequestHandler(BaseHTTPRequestHandler):
        server_version = "AstroAgentDemo/1.0"

        def _set_common_headers(self, content_type: str = "application/json") -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._set_common_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self._set_common_headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError as error:
                raise ValueError(f"Payload JSON không hợp lệ: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError("Payload phải là JSON object.")
            return payload

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self.send_response(HTTPStatus.NO_CONTENT)
            self._set_common_headers()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path in {"/", "/index.html"}:
                try:
                    with open(web_path, encoding="utf-8") as file:
                        self._send_html(file.read())
                except FileNotFoundError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Không tìm thấy {web_path}"})
                return

            if self.path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "provider": provider.__class__.__name__,
                        "model": getattr(provider, "model_name", "unknown"),
                    },
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Không hỗ trợ đường dẫn {self.path}"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/api/generate":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Không hỗ trợ đường dẫn {self.path}"})
                return

            try:
                payload = self._read_json_body()
                system_prompt = str(payload.get("systemPrompt") or payload.get("system_prompt") or "")
                user_content = str(payload.get("userContent") or payload.get("prompt") or payload.get("user_content") or "")
                text = provider.generate(user_content, system_prompt=system_prompt)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "text": str(text or ""),
                        "provider": provider.__class__.__name__,
                        "model": getattr(provider, "model_name", "unknown"),
                    },
                )
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception as error:  # pragma: no cover - defensive server boundary
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Lỗi server: {error}"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), DemoRequestHandler)
    print("=" * 58)
    print("BÀI LAB 3: CHATBOT VS REACT AGENT")
    print("=" * 58)
    print(f"Provider: {provider.__class__.__name__} (Model: {getattr(provider, 'model_name', 'unknown')})")
    print(f"Demo web: http://{host}:{port}/")
    print(f"API: http://{host}:{port}/api/generate")
    print("Nhấn Ctrl+C để dừng server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng demo web.")
    finally:
        server.server_close()


def main() -> int:
    args = _parse_cli_args()
    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    provider = (
        OfflineAstrologyProvider()
        if provider_name == "mock"
        else get_llm_provider(provider_name)
    )
    tests = load_test_cases()
    if args.case is not None:
        tests = [case for case in tests if case.get("id") == args.case]
        if not tests:
            print(f"Không tìm thấy test case có id={args.case}.", file=sys.stderr)
            return 2

    if args.serve:
        _run_web_demo_server(provider, args.host, args.port)
        return 0

    model_name = getattr(provider, "model_name", "Offline Mock Mode")
    print("=" * 58)
    print("BÀI LAB 3: CHATBOT VS REACT AGENT")
    print("=" * 58)
    print(f"Provider: {provider.__class__.__name__} (Model: {model_name})")

    if args.interactive:
        try:
            run_interactive(provider)
        except (EOFError, KeyboardInterrupt):
            print("\nĐã hủy nhập thông tin.")
            return 130
        return 0

    print(f"Đã tải {len(tests)} test case.")

    for case in tests:
        print(f"\n{'=' * 20} TEST CASE {case.get('id', '?')} {'=' * 20}")
        if args.mode in {"baseline", "both"}:
            run_baseline_chatbot(case["question"], provider)
        if args.mode in {"agent", "both"}:
            result = run_react_agent(case["question"], provider)
            print(
                f"Telemetry: iterations={result.iterations}, "
                f"tool_calls={result.tool_calls}, guardrail={result.stopped_by_guardrail}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

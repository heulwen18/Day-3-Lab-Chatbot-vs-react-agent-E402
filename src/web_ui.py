"""Streamlit web UI for the astrology ReAct demo."""

from __future__ import annotations

import os
import sys
from typing import Any

from dotenv import load_dotenv
import streamlit as st


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)


from app import (  # noqa: E402
    DISCLAIMER,
    OfflineAstrologyProvider,
    load_test_cases,
    run_baseline_chatbot,
    run_react_agent,
)
from providers import get_llm_provider  # noqa: E402
from tools import validate_birth_info  # noqa: E402


load_dotenv()

st.set_page_config(
    page_title="Astro React Lab",
    page_icon=":material/nights_stay:",
    layout="wide",
)


ANALYSIS_OPTIONS = [
    "Tổng quan",
    "Học tập & sự nghiệp",
    "Tình cảm",
    "Theo năm",
]
PERSONAL_MODE_OPTIONS = ["Một người", "Ghép đôi"]
PROVIDER_OPTIONS = ["mock", "auto", "gemini", "openai", "anthropic", "openrouter"]


def _shorten(text: str, limit: int = 72) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


@st.cache_resource(show_spinner=False)
def _get_provider(provider_name: str):
    if provider_name == "mock":
        return OfflineAstrologyProvider()
    if provider_name == "auto":
        return get_llm_provider()
    return get_llm_provider(provider_name)


@st.cache_data(show_spinner=False)
def _load_examples() -> list[dict[str, Any]]:
    return load_test_cases()


def _build_personal_query(
    *,
    analysis_type: str,
    birth_date: str,
    birth_time: str,
    gender: str,
    birth_place: str,
    calendar_type: str,
    current_field: str,
    target_year: int,
    extra_question: str,
) -> str:
    calendar_label = "dương lịch" if calendar_type == "solar" else "âm lịch"
    base_prompt = (
        f"Hãy luận giải tử vi {analysis_type.lower()} cho {gender} sinh ngày {birth_date} "
        f"lúc {birth_time} tại {birth_place}, dùng {calendar_label}."
    )

    if analysis_type == "Học tập & sự nghiệp":
        field_text = current_field.strip() or "Người dùng chưa cung cấp ngành học hoặc công việc."
        base_prompt += f" Ngành học hoặc công việc hiện tại: {field_text}."
    elif analysis_type == "Theo năm":
        base_prompt = (
            f"Hãy luận giải xu hướng tham khảo cho năm {target_year} của {gender} sinh ngày "
            f"{birth_date} lúc {birth_time} tại {birth_place}, dùng {calendar_label}."
        )

    if extra_question.strip():
        base_prompt += f" Yêu cầu bổ sung: {extra_question.strip()}"

    return base_prompt


def _build_compatibility_query(
    *,
    first_profile: dict[str, str],
    second_profile: dict[str, str],
    calendar_type: str,
    extra_question: str,
) -> str:
    calendar_label = "dương lịch" if calendar_type == "solar" else "âm lịch"
    query = (
        "Hãy phân tích độ tương hợp tình cảm. "
        f"Người 1: {first_profile['gender']}, sinh {first_profile['birth_date']} lúc {first_profile['birth_time']} tại {first_profile['birth_place']}; "
        f"Người 2: {second_profile['gender']}, sinh {second_profile['birth_date']} lúc {second_profile['birth_time']} tại {second_profile['birth_place']}; "
        f"cả hai dùng {calendar_label}."
    )
    if extra_question.strip():
        query += f" Yêu cầu bổ sung: {extra_question.strip()}"
    return query


def _validate_profile(profile: dict[str, str], calendar_type: str) -> str:
    return validate_birth_info(
        profile["birth_date"],
        profile["birth_time"],
        profile["gender"],
        profile["birth_place"],
        calendar_type,
    )


def _render_result_section(title: str, content: str, *, highlight: bool = False) -> None:
    with st.container(border=True):
        st.subheader(title)
        if highlight:
            st.markdown(f":blue-badge[{DISCLAIMER}]")
        st.write(content)


def _render_metrics(result) -> None:
    cols = st.columns(3)
    cols[0].metric("Iterations", result.iterations)
    cols[1].metric("Tool calls", result.tool_calls)
    cols[2].metric("Guardrail", "Yes" if result.stopped_by_guardrail else "No")


def _render_trace(result) -> None:
    with st.expander("Trace ReAct", icon=":material/query_stats:"):
        if result.trace:
            st.dataframe(result.trace, width="stretch", hide_index=True)
        else:
            st.info("Không có trace để hiển thị.")


def _run_personal_mode(
    provider,
    analysis_type: str,
    birth_date: str,
    birth_time: str,
    gender: str,
    birth_place: str,
    calendar_type: str,
    current_field: str,
    target_year: int,
    extra_question: str,
    compare_with_baseline: bool,
) -> None:
    profile = {
        "birth_date": birth_date.strip(),
        "birth_time": birth_time.strip(),
        "gender": gender.strip(),
        "birth_place": birth_place.strip(),
    }

    validation = _validate_profile(profile, calendar_type)
    if validation.startswith("TOOL_ERROR"):
        st.error(validation.replace("TOOL_ERROR:", "").strip())
        return

    query = _build_personal_query(
        analysis_type=analysis_type,
        birth_date=profile["birth_date"],
        birth_time=profile["birth_time"],
        gender=profile["gender"],
        birth_place=profile["birth_place"],
        calendar_type=calendar_type,
        current_field=current_field,
        target_year=target_year,
        extra_question=extra_question,
    )

    with st.spinner("Đang chạy ReAct agent..."):
        agent_result = run_react_agent(query, provider, verbose=False)
        baseline_answer = None
        if compare_with_baseline:
            baseline_answer = run_baseline_chatbot(query, provider, verbose=False)

    st.session_state.last_run = {
        "mode": "personal",
        "analysis_type": analysis_type,
        "query": query,
        "agent_result": agent_result,
        "baseline_answer": baseline_answer,
        "validation": validation,
    }


def _run_pair_mode(
    provider,
    first_profile: dict[str, str],
    second_profile: dict[str, str],
    calendar_type: str,
    extra_question: str,
    compare_with_baseline: bool,
) -> None:
    first_validation = _validate_profile(first_profile, calendar_type)
    if first_validation.startswith("TOOL_ERROR"):
        st.error("Người thứ nhất: " + first_validation.replace("TOOL_ERROR:", "").strip())
        return

    second_validation = _validate_profile(second_profile, calendar_type)
    if second_validation.startswith("TOOL_ERROR"):
        st.error("Người thứ hai: " + second_validation.replace("TOOL_ERROR:", "").strip())
        return

    query = _build_compatibility_query(
        first_profile=first_profile,
        second_profile=second_profile,
        calendar_type=calendar_type,
        extra_question=extra_question,
    )

    with st.spinner("Đang chạy ReAct agent..."):
        agent_result = run_react_agent(query, provider, verbose=False)
        baseline_answer = None
        if compare_with_baseline:
            baseline_answer = run_baseline_chatbot(query, provider, verbose=False)

    st.session_state.last_run = {
        "mode": "pair",
        "query": query,
        "agent_result": agent_result,
        "baseline_answer": baseline_answer,
        "validation": f"{first_validation}\n\n{second_validation}",
    }


def _render_last_run() -> None:
    last_run = st.session_state.get("last_run")
    if not last_run:
        return

    st.space("medium")
    st.subheader("Kết quả gần nhất")
    st.caption(last_run["query"])

    agent_result = last_run["agent_result"]
    _render_metrics(agent_result)

    if last_run.get("baseline_answer"):
        left, right = st.columns(2, vertical_alignment="top")
        with left:
            _render_result_section("Baseline chatbot", last_run["baseline_answer"])
        with right:
            _render_result_section("ReAct agent", agent_result.answer, highlight=True)
    else:
        _render_result_section("ReAct agent", agent_result.answer, highlight=True)

    _render_trace(agent_result)


def main() -> None:
    st.session_state.setdefault("last_run", None)

    examples = _load_examples()
    provider_name = st.sidebar.segmented_control(
        "LLM provider",
        options=PROVIDER_OPTIONS,
        default="mock",
    )
    compare_with_baseline = st.sidebar.toggle("So sánh với baseline", value=True)
    show_examples = st.sidebar.toggle("Hiển thị câu hỏi mẫu", value=True)

    provider = _get_provider(provider_name)

    st.sidebar.caption(
        f"Provider đang dùng: {provider.__class__.__name__} | Model: {getattr(provider, 'model_name', 'n/a')}"
    )

    st.title("Astro React Lab")
    st.caption(
        "Một giao diện web gọn, có thể nhập lá số, chạy ReAct agent, xem trace từng bước và so sánh với baseline chatbot."
    )

    with st.container(border=True):
        st.markdown(
            """
            :blue-badge[ReAct agent] :orange-badge[Baseline] :green-badge[Tham khảo]
            """
        )
        st.write(
            "UI này tập trung vào trải nghiệm nhập dữ liệu tử vi rõ ràng, phản hồi kết quả có cấu trúc, và trace để nhìn được cách agent ra quyết định."
        )

    if show_examples and examples:
        with st.expander("Câu hỏi mẫu", icon=":material/description:"):
            for case in examples[:5]:
                st.write(f"- #{case.get('id', '?')}: {_shorten(case.get('question', ''))}")

    mode = st.segmented_control(
        "Chế độ xem",
        options=PERSONAL_MODE_OPTIONS,
        default="Một người",
    )

    with st.form("astro_form", border=True):
        if mode == "Một người":
            analysis_type = st.segmented_control(
                "Loại luận giải",
                options=ANALYSIS_OPTIONS,
                default="Tổng quan",
            )

            top_left, top_right = st.columns(2)
            with top_left:
                birth_date = st.text_input("Ngày sinh", placeholder="DD/MM/YYYY")
                birth_time = st.text_input("Giờ sinh", placeholder="HH:MM")
            with top_right:
                gender = st.selectbox("Giới tính", ["nam", "nữ"], index=0)
                birth_place = st.text_input("Nơi sinh", placeholder="Ví dụ: Hà Nội")

            second_row_left, second_row_right = st.columns(2)
            with second_row_left:
                calendar_type = st.segmented_control(
                    "Loại lịch",
                    options=["solar", "lunar"],
                    default="solar",
                )
            with second_row_right:
                if analysis_type == "Theo năm":
                    target_year = int(
                        st.number_input(
                            "Năm cần xem",
                            min_value=1900,
                            max_value=2200,
                            value=2026,
                            step=1,
                        )
                    )
                else:
                    target_year = 2026

            if analysis_type == "Học tập & sự nghiệp":
                current_field = st.text_input("Ngành học hoặc công việc hiện tại", placeholder="Ví dụ: công nghệ thông tin")
            else:
                current_field = ""

            extra_question = st.text_area(
                "Câu hỏi bổ sung",
                placeholder="Ví dụ: Mình nên tập trung vào điểm nào trong giai đoạn này?",
            )

            submitted = st.form_submit_button("Chạy ReAct", icon=":material/play_arrow:", type="primary")

            if submitted:
                _run_personal_mode(
                    provider,
                    analysis_type,
                    birth_date,
                    birth_time,
                    gender,
                    birth_place,
                    calendar_type,
                    current_field,
                    target_year,
                    extra_question,
                    compare_with_baseline,
                )
        else:
            first_left, first_right = st.columns(2)
            with first_left:
                st.markdown("**Người thứ nhất**")
                first_profile = {
                    "birth_date": st.text_input("Ngày sinh 1", placeholder="DD/MM/YYYY"),
                    "birth_time": st.text_input("Giờ sinh 1", placeholder="HH:MM"),
                    "gender": st.selectbox("Giới tính 1", ["nam", "nữ"], key="first_gender"),
                    "birth_place": st.text_input("Nơi sinh 1", placeholder="Ví dụ: Hà Nội"),
                }
            with first_right:
                st.markdown("**Người thứ hai**")
                second_profile = {
                    "birth_date": st.text_input("Ngày sinh 2", placeholder="DD/MM/YYYY"),
                    "birth_time": st.text_input("Giờ sinh 2", placeholder="HH:MM"),
                    "gender": st.selectbox("Giới tính 2", ["nam", "nữ"], key="second_gender"),
                    "birth_place": st.text_input("Nơi sinh 2", placeholder="Ví dụ: Đà Nẵng"),
                }

            calendar_type = st.segmented_control(
                "Loại lịch dùng chung",
                options=["solar", "lunar"],
                default="solar",
            )
            extra_question = st.text_area(
                "Câu hỏi bổ sung",
                placeholder="Ví dụ: Điểm nào hai bên nên lưu ý khi giao tiếp?",
            )

            submitted = st.form_submit_button("Chạy ReAct", icon=":material/play_arrow:", type="primary")

            if submitted:
                _run_pair_mode(
                    provider,
                    first_profile,
                    second_profile,
                    calendar_type,
                    extra_question,
                    compare_with_baseline,
                )

    _render_last_run()


if __name__ == "__main__":
    main()
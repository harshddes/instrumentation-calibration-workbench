from __future__ import annotations

import os
import random
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from ai_variation_engine import generate_chat_reply, run_variation_request
from dashboard_core import (
    build_data_structure,
    build_dataset_context,
    detect_timestamp_column,
    discover_csv_files,
    format_dataframe_console,
    generate_structure_code,
    get_numeric_columns,
    load_measurement_csv,
)


st.set_page_config(
    page_title="3D DAQ Data Playground",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _relative_path_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _make_volatility_challenge(df: pd.DataFrame, numeric_cols: list[str]) -> dict | None:
    if len(numeric_cols) < 2:
        return None
    std_values = df[numeric_cols].std(numeric_only=True).sort_values(ascending=False)
    answer = str(std_values.index[0])
    distractors = [str(col) for col in std_values.index[1:4]]
    options = [answer] + distractors
    random.shuffle(options)
    return {
        "question": "Which channel is most volatile (largest standard deviation)?",
        "options": options,
        "answer": answer,
        "scores": std_values.to_dict(),
    }


def _build_lattice_dataframe(df: pd.DataFrame, channel_cols: list[str], max_rows: int) -> pd.DataFrame:
    clipped = df[channel_cols].head(max_rows).copy()
    clipped = clipped.reset_index(names="row_id")
    lattice = clipped.melt(id_vars="row_id", var_name="channel", value_name="value")
    lattice["channel_id"] = lattice["channel"].astype("category").cat.codes
    return lattice


project_root = Path(__file__).resolve().parents[1]
available_csv = discover_csv_files(project_root)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_variation" not in st.session_state:
    st.session_state.last_variation = None
if "game_score" not in st.session_state:
    st.session_state.game_score = 0
if "game_rounds" not in st.session_state:
    st.session_state.game_rounds = 0
if "game_feedback" not in st.session_state:
    st.session_state.game_feedback = ""

st.title("3D DAQ Data Playground")
st.caption(
    "Interactive lab for CSV data structures: code view, console output, AI variation runner, and 3D visual mapping."
)

with st.sidebar:
    st.header("Data Source")
    source_mode = st.radio("Choose source", ["Repository CSV", "Upload CSV"], horizontal=False)

    selected_path: Path | None = None
    uploaded_file = None

    if source_mode == "Repository CSV":
        if not available_csv:
            st.error("No CSV files found in the repository.")
            st.stop()
        selected_path = st.selectbox(
            "CSV file",
            options=available_csv,
            format_func=lambda p: _relative_path_label(p, project_root),
        )
    else:
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
        if uploaded_file is None:
            st.info("Upload a CSV to start the dashboard.")
            st.stop()

    st.header("AI Setup")
    default_api_key = os.getenv("OPENAI_API_KEY", "")
    api_key = st.text_input(
        "OpenAI API key (optional)",
        type="password",
        value=default_api_key,
        help="If empty, local heuristic mode will be used.",
    ).strip()
    model_name = st.selectbox("Model", options=["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"])


try:
    if source_mode == "Repository CSV":
        assert selected_path is not None
        source_label = str(selected_path)
        df = load_measurement_csv(selected_path)
    else:
        assert uploaded_file is not None
        source_label = uploaded_file.name
        df = load_measurement_csv(uploaded_file)
except Exception as exc:
    st.error(f"Could not load CSV: {exc}")
    st.stop()

if df.empty:
    st.error("CSV is empty.")
    st.stop()

numeric_columns = get_numeric_columns(df)
if not numeric_columns:
    st.error("No numeric columns were detected, so 3D plotting is not possible.")
    st.stop()

data_structure = build_data_structure(df)
timestamp_col = detect_timestamp_column(df.columns) or (
    numeric_columns[0] if numeric_columns else df.columns[0]
)
channel_cols = list(data_structure.channels.keys())
dataset_context = build_dataset_context(df, numeric_columns)

tab_structure, tab_3d, tab_console, tab_chat, tab_game = st.tabs(
    ["Data Structure Code", "3D Visualizer", "Console + Variations", "AI Chatbot", "Mini Game"]
)

with tab_structure:
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Rows", f"{len(df):,}")
    metric_col2.metric("Columns", f"{len(df.columns)}")
    metric_col3.metric("Numeric Columns", f"{len(numeric_columns)}")

    st.subheader("Python code that builds the data structure")
    structure_code = generate_structure_code(source_label, str(timestamp_col), channel_cols)
    st.code(structure_code, language="python")

    st.subheader("Data-structure preview")
    st.json(data_structure.preview(limit=5))

    st.subheader("Raw table preview")
    st.dataframe(df.head(30), width="stretch")

with tab_3d:
    st.subheader("Minimal 3D pictorial representation")
    view_mode = st.radio(
        "Visualization mode",
        options=["Signal space", "Table lattice"],
        horizontal=True,
    )

    if view_mode == "Signal space":
        plot_df = df.copy()
        axis_candidates = list(numeric_columns)
        if len(axis_candidates) == 1:
            plot_df["row_index"] = range(len(plot_df))
            plot_df["zero_axis"] = 0.0
            axis_candidates.extend(["row_index", "zero_axis"])
        elif len(axis_candidates) == 2:
            plot_df["row_index"] = range(len(plot_df))
            axis_candidates.append("row_index")

        x_axis = st.selectbox("X axis", axis_candidates, index=0)
        y_axis = st.selectbox("Y axis", axis_candidates, index=min(1, len(axis_candidates) - 1))
        z_axis = st.selectbox("Z axis", axis_candidates, index=min(2, len(axis_candidates) - 1))
        color_axis = st.selectbox("Color", ["None"] + axis_candidates)
        point_min = 1
        point_max = min(20000, len(plot_df))
        if point_max <= point_min:
            max_points = point_max
        else:
            point_step = max(1, point_max // 60)
            max_points = st.slider(
                "Max plotted points",
                min_value=point_min,
                max_value=point_max,
                value=min(5000, point_max),
                step=point_step,
            )

        step = max(1, len(plot_df) // max_points)
        sampled = plot_df.iloc[::step].copy()

        fig_args: dict = {"x": x_axis, "y": y_axis, "z": z_axis}
        if color_axis != "None":
            fig_args["color"] = color_axis

        fig = px.scatter_3d(sampled, **fig_args, opacity=0.85)
        fig.update_traces(marker={"size": 4, "line": {"width": 0}})
        fig.update_layout(
            template="plotly_white",
            margin={"l": 0, "r": 0, "t": 20, "b": 0},
            scene={
                "xaxis_title": x_axis,
                "yaxis_title": y_axis,
                "zaxis_title": z_axis,
                "bgcolor": "#f9f9f9",
            },
        )
        st.plotly_chart(fig, width="stretch")
    else:
        lattice_min = 1
        lattice_max = min(10000, len(df))
        if lattice_max <= lattice_min:
            lattice_rows = lattice_max
        else:
            lattice_step = max(1, lattice_max // 60)
            lattice_rows = st.slider(
                "Rows to encode in 3D lattice",
                min_value=lattice_min,
                max_value=lattice_max,
                value=min(1500, lattice_max),
                step=lattice_step,
            )
        lattice_df = _build_lattice_dataframe(df, channel_cols, lattice_rows)
        lattice_sample = lattice_df.iloc[:: max(1, len(lattice_df) // 7000)].copy()

        fig = px.scatter_3d(
            lattice_sample,
            x="row_id",
            y="channel_id",
            z="value",
            color="channel",
            opacity=0.8,
        )
        fig.update_traces(marker={"size": 3.5, "line": {"width": 0}})
        fig.update_layout(
            template="plotly_white",
            margin={"l": 0, "r": 0, "t": 20, "b": 0},
            scene={
                "xaxis_title": "Row Index",
                "yaxis_title": "Channel Index",
                "zaxis_title": "Value",
                "bgcolor": "#f9f9f9",
            },
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Each dot represents one table cell mapped into 3D.")

with tab_console:
    st.subheader("Console-style output")
    st.code(format_dataframe_console(df, max_rows=10), language="text")

    st.subheader("Ask for code variations, execute, and inspect output")
    variation_prompt = st.text_area(
        "Variation request",
        value="Add rolling average with window 7 on the main voltage channel.",
        height=110,
    )
    if st.button("Generate + Execute Variation", type="primary"):
        result = run_variation_request(
            prompt=variation_prompt,
            df=df,
            dataset_context=dataset_context,
            api_key=api_key if api_key else None,
            model=model_name,
        )
        st.session_state.last_variation = result

    variation_result = st.session_state.last_variation
    if variation_result is not None:
        st.markdown(
            f"**Engine:** `{variation_result.provider}`  \n"
            f"**Notes:** {variation_result.notes}"
        )
        if variation_result.error:
            st.warning(f"Fallback detail: {variation_result.error}")
        st.code(variation_result.python_code, language="python")
        st.code(variation_result.console_output, language="text")
        st.dataframe(variation_result.transformed_df.head(30), width="stretch")

with tab_chat:
    st.subheader("AI coding coach")
    st.caption("Ask about the current dataset, Python logic, or next transformation ideas.")
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_question = st.chat_input("Ask something about this data/code...")
    if user_question:
        st.session_state.chat_history.append({"role": "user", "content": user_question})
        answer = generate_chat_reply(
            user_message=user_question,
            history=st.session_state.chat_history[:-1],
            dataset_context=dataset_context,
            api_key=api_key if api_key else None,
            model=model_name,
        )
        st.session_state.chat_history.append({"role": "assistant", "content": answer})
        st.rerun()

with tab_game:
    st.subheader("Data Detective Mini-Game")
    st.caption("Train your data intuition: read the table, make a call, check against statistics.")

    challenge = _make_volatility_challenge(df, numeric_columns)
    if challenge is None:
        st.info("Need at least two numeric channels for game mode.")
    else:
        st.markdown(f"**Score:** {st.session_state.game_score} / {st.session_state.game_rounds}")
        choice = st.radio(
            challenge["question"],
            options=challenge["options"],
            index=0,
            key="volatility_choice",
        )
        col_submit, col_explain = st.columns(2)
        with col_submit:
            if st.button("Submit Answer"):
                st.session_state.game_rounds += 1
                if choice == challenge["answer"]:
                    st.session_state.game_score += 1
                    st.session_state.game_feedback = (
                        f"Correct. `{challenge['answer']}` has the highest standard deviation."
                    )
                else:
                    st.session_state.game_feedback = (
                        f"Not this time. Correct answer: `{challenge['answer']}`."
                    )
        with col_explain:
            if st.button("Show volatility table"):
                std_table = (
                    df[numeric_columns]
                    .std(numeric_only=True)
                    .sort_values(ascending=False)
                    .rename("std_dev")
                    .to_frame()
                )
                st.dataframe(std_table, width="stretch")

        if st.session_state.game_feedback:
            st.info(st.session_state.game_feedback)

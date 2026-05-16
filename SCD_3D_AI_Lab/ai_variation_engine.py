from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import re
from typing import Any

import numpy as np
import pandas as pd
import requests


@dataclass
class VariationResult:
    provider: str
    notes: str
    python_code: str
    console_output: str
    transformed_df: pd.DataFrame
    error: str | None = None


SYSTEM_CHAT_PROMPT = """You are a sharp Python data-analysis mentor.
Style rules:
- Explain with Feynman clarity: simple words, precise logic.
- Keep answers practical and beginner-friendly.
- Be concise but concrete.
- If asked for code changes, give exact steps and why they work.
"""


SYSTEM_CODE_PROMPT = """Generate Python code only.
Return one function named transform(df) that:
1) accepts a pandas DataFrame,
2) applies the requested transformation,
3) prints a short console message about what changed,
4) returns the transformed DataFrame.

Constraints:
- Use pandas/numpy only.
- Do not read files.
- Do not use network or OS operations.
- Keep code deterministic.
"""


def extract_python_block(text: str) -> str:
    block = re.search(r"```python\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if block:
        return block.group(1).strip()
    if "def transform" in text:
        return text.strip()
    raise ValueError("No python transform function found in model response.")


def _safe_openai_chat(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    timeout_seconds: int = 45,
) -> str:
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": messages,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    message = data["choices"][0]["message"]["content"]
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in message
        )
    return str(message)


def _run_transform_code(df: pd.DataFrame, python_code: str) -> tuple[pd.DataFrame, str]:
    safe_builtins: dict[str, Any] = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }

    global_scope = {"pd": pd, "np": np, "__builtins__": safe_builtins}
    local_scope: dict[str, Any] = {}
    exec(python_code, global_scope, local_scope)
    transform = local_scope.get("transform") or global_scope.get("transform")
    if transform is None:
        raise ValueError("The generated code does not define transform(df).")

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        transformed = transform(df.copy())
    if not isinstance(transformed, pd.DataFrame):
        raise TypeError("transform(df) must return a pandas DataFrame.")

    console_output = buffer.getvalue().strip()
    if not console_output:
        console_output = "<no console output>"

    footer = f"\n\nResult shape: {transformed.shape}"
    return transformed, console_output + footer


def _extract_window(prompt: str, default: int = 5) -> int:
    matches = re.findall(r"\b(\d{1,3})\b", prompt)
    if not matches:
        return default
    value = int(matches[0])
    return max(2, min(100, value))


def _build_local_code(prompt: str, df: pd.DataFrame) -> tuple[str, str]:
    lower = prompt.lower()
    numeric_cols = [
        col for col in df.columns if pd.api.types.is_numeric_dtype(df[col]) and str(col).lower() != "timestamp"
    ]
    target_col = numeric_cols[0] if numeric_cols else df.columns[0]

    if "rolling" in lower or "moving average" in lower:
        window = _extract_window(prompt, default=5)
        code = f"""
def transform(df):
    df = df.copy()
    source_col = {target_col!r}
    window = {window}
    new_col = f"{{source_col}}_rolling_mean_{{window}}"
    df[new_col] = df[source_col].rolling(window=window, min_periods=1).mean()
    print(f"Added rolling mean column: {{new_col}}")
    return df
""".strip()
        note = f"Local engine: added a rolling mean on `{target_col}` with window={window}."
        return code, note

    if "normalize" in lower or "scale" in lower:
        column_literals = ", ".join(repr(col) for col in numeric_cols) or repr(target_col)
        code = f"""
def transform(df):
    df = df.copy()
    numeric_cols = [{column_literals}]
    for col in numeric_cols:
        col_min = df[col].min()
        col_max = df[col].max()
        if col_max == col_min:
            df[f"{{col}}_norm"] = 0.0
        else:
            df[f"{{col}}_norm"] = (df[col] - col_min) / (col_max - col_min)
    print("Added min-max normalized columns.")
    return df
""".strip()
        note = "Local engine: min-max normalization over numeric columns."
        return code, note

    if "delta" in lower or "difference" in lower or "derivative" in lower:
        code = f"""
def transform(df):
    df = df.copy()
    source_col = {target_col!r}
    new_col = f"{{source_col}}_delta"
    df[new_col] = df[source_col].diff().fillna(0.0)
    print(f"Added first-difference column: {{new_col}}")
    return df
""".strip()
        note = f"Local engine: first difference on `{target_col}`."
        return code, note

    code = f"""
def transform(df):
    df = df.copy()
    source_col = {target_col!r}
    q_low = df[source_col].quantile(0.01)
    q_high = df[source_col].quantile(0.99)
    new_col = f"{{source_col}}_clipped"
    df[new_col] = df[source_col].clip(lower=q_low, upper=q_high)
    print(f"Added clipped column: {{new_col}} using 1%-99% quantiles.")
    return df
""".strip()
    note = f"Local engine: outlier clipping on `{target_col}`."
    return code, note


def run_variation_request(
    prompt: str,
    df: pd.DataFrame,
    dataset_context: str,
    api_key: str | None,
    model: str = "gpt-4.1-mini",
) -> VariationResult:
    prompt = prompt.strip()
    if not prompt:
        return VariationResult(
            provider="local",
            notes="No variation prompt provided.",
            python_code="",
            console_output="",
            transformed_df=df.copy(),
            error="Variation prompt is empty.",
        )

    if api_key:
        try:
            model_text = _safe_openai_chat(
                messages=[
                    {"role": "system", "content": SYSTEM_CODE_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dataset context:\n{dataset_context}\n\n"
                            f"Request:\n{prompt}\n\n"
                            "Return Python code only."
                        ),
                    },
                ],
                api_key=api_key,
                model=model,
            )
            python_code = extract_python_block(model_text)
            transformed, console_output = _run_transform_code(df, python_code)
            return VariationResult(
                provider="openai",
                notes="Generated by OpenAI model and executed locally.",
                python_code=python_code,
                console_output=console_output,
                transformed_df=transformed,
            )
        except Exception as exc:
            local_code, local_note = _build_local_code(prompt, df)
            transformed, console_output = _run_transform_code(df, local_code)
            return VariationResult(
                provider="local_fallback",
                notes=f"OpenAI call failed. {local_note}",
                python_code=local_code,
                console_output=console_output,
                transformed_df=transformed,
                error=str(exc),
            )

    local_code, local_note = _build_local_code(prompt, df)
    transformed, console_output = _run_transform_code(df, local_code)
    return VariationResult(
        provider="local",
        notes=local_note,
        python_code=local_code,
        console_output=console_output,
        transformed_df=transformed,
    )


def generate_chat_reply(
    user_message: str,
    history: list[dict[str, str]],
    dataset_context: str,
    api_key: str | None,
    model: str = "gpt-4.1-mini",
) -> str:
    user_message = user_message.strip()
    if not user_message:
        return "Send a concrete question and I will help."

    if api_key:
        try:
            context_messages = [
                {"role": "system", "content": SYSTEM_CHAT_PROMPT},
                {
                    "role": "system",
                    "content": f"Dataset context:\n{dataset_context}",
                },
            ]
            context_messages.extend(history[-8:])
            context_messages.append({"role": "user", "content": user_message})
            return _safe_openai_chat(
                messages=context_messages,
                api_key=api_key,
                model=model,
            )
        except Exception as exc:
            return (
                "API call failed, so I switched to local coaching mode.\n"
                f"Error: {exc}\n\n"
                "Ask for a concrete transformation, e.g. "
                "'add rolling average with window 7 on V'."
            )

    return (
        "Local coaching mode is active (no API key configured).\n\n"
        "I can still guide you with transformation prompts:\n"
        "- add rolling average window 5\n"
        "- normalize all numeric columns\n"
        "- compute first difference of V\n"
        "- clip outliers in I\n\n"
        "Add `OPENAI_API_KEY` (or paste in sidebar) for full GPT chat."
    )

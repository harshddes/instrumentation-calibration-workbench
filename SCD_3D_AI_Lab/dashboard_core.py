from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass
class DAQDataStructure:
    timestamps: np.ndarray
    channels: dict[str, np.ndarray]
    row_count: int
    source_columns: list[str]

    def preview(self, limit: int = 6) -> dict[str, Any]:
        clipped = max(1, limit)
        channel_preview = {
            name: values[:clipped].tolist() for name, values in self.channels.items()
        }
        return {
            "row_count": self.row_count,
            "source_columns": self.source_columns,
            "timestamps": self.timestamps[:clipped].tolist(),
            "channels": channel_preview,
        }


def discover_csv_files(project_root: Path, limit: int = 300) -> list[Path]:
    ignored_parts = {".git", ".venv", "__pycache__"}
    files: list[Path] = []
    for path in project_root.rglob("*.csv"):
        if any(part in ignored_parts for part in path.parts):
            continue
        files.append(path)

    files.sort(key=lambda file_path: file_path.stat().st_mtime, reverse=True)
    return files[:limit]


def load_measurement_csv(file_or_path: Any) -> pd.DataFrame:
    df = pd.read_csv(file_or_path)
    df.columns = [str(col).strip() for col in df.columns]
    return _coerce_numeric_columns(df)


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    converted = df.copy()
    for column in converted.columns:
        if converted[column].dtype.kind in {"i", "f"}:
            continue
        numeric_candidate = pd.to_numeric(converted[column], errors="coerce")
        valid_ratio = float(numeric_candidate.notna().mean()) if len(df) else 0.0
        if valid_ratio >= 0.8:
            converted[column] = numeric_candidate
    return converted


def get_numeric_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])]


def detect_timestamp_column(columns: Iterable[str]) -> str | None:
    normalized = {col.lower().strip(): col for col in columns}
    for key in ("timestamp", "time", "ts"):
        if key in normalized:
            return normalized[key]
    return None


def build_data_structure(df: pd.DataFrame) -> DAQDataStructure:
    numeric_columns = get_numeric_columns(df)
    timestamp_col = detect_timestamp_column(df.columns)

    if timestamp_col is None and numeric_columns:
        timestamp_col = numeric_columns[0]
    if timestamp_col is None:
        timestamp_col = df.columns[0]

    if pd.api.types.is_numeric_dtype(df[timestamp_col]):
        timestamps = df[timestamp_col].to_numpy(dtype=float)
    else:
        timestamps = np.arange(len(df), dtype=float)

    channel_columns = [col for col in numeric_columns if col != timestamp_col]
    if not channel_columns:
        channel_columns = [col for col in df.columns if col != timestamp_col]

    channels = {column: df[column].to_numpy() for column in channel_columns}

    return DAQDataStructure(
        timestamps=timestamps,
        channels=channels,
        row_count=len(df),
        source_columns=[str(col) for col in df.columns],
    )


def generate_structure_code(source_label: str, timestamp_col: str, channel_cols: list[str]) -> str:
    source_literal = source_label.replace("\\", "\\\\")
    channel_literal = ", ".join(f'"{col}"' for col in channel_cols)

    return "\n".join(
        [
            "import pandas as pd",
            "",
            f'df = pd.read_csv(r"{source_literal}")',
            f'timestamp_col = "{timestamp_col}"',
            f"channel_cols = [{channel_literal}]",
            "",
            "daq_structure = {",
            '    "timestamps": df[timestamp_col].to_numpy(),',
            "    \"channels\": {col: df[col].to_numpy() for col in channel_cols},",
            "    \"row_count\": len(df),",
            "}",
            "",
            "print(daq_structure[\"row_count\"])",
            "print(list(daq_structure[\"channels\"].keys()))",
        ]
    )


def format_dataframe_console(df: pd.DataFrame, max_rows: int = 8) -> str:
    clip = max(3, max_rows)
    preview = df.head(clip)
    null_counts = df.isna().sum().to_dict()

    return "\n".join(
        [
            ">>> df.shape",
            str(df.shape),
            "",
            ">>> df.columns.tolist()",
            str(df.columns.tolist()),
            "",
            ">>> df.dtypes",
            df.dtypes.to_string(),
            "",
            ">>> df.isna().sum().to_dict()",
            str(null_counts),
            "",
            f">>> df.head({clip})",
            preview.to_string(index=False),
        ]
    )


def build_dataset_context(df: pd.DataFrame, numeric_columns: list[str]) -> str:
    lines = [
        f"Rows: {len(df)}",
        f"Columns: {list(df.columns)}",
        f"Numeric columns: {numeric_columns}",
    ]
    if numeric_columns:
        describe = df[numeric_columns].describe().round(6).to_string()
        lines.append("Numeric summary:")
        lines.append(describe)
    return "\n".join(lines)

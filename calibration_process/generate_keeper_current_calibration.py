import csv
import hashlib
import json
import math
import shutil
import stat
from datetime import datetime, timezone
from html import escape
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
EXTERNAL_SOURCE_CSV = PROJECT_ROOT / "examples" / "synthetic_merged_session.csv"
RAW_SOURCE_DIR = PACKAGE_DIR / "raw_sources"
RAW_SOURCE_CSV = RAW_SOURCE_DIR / EXTERNAL_SOURCE_CSV.name
CURATED_SOURCE_DIR = PACKAGE_DIR / "curated_sources"
CURATED_SOURCE_CSV = CURATED_SOURCE_DIR / "synthetic_calibration_source.csv"
ARTIFACT_DIR = PACKAGE_DIR / "artifacts"
PLOT_DIR = ARTIFACT_DIR / "plots"

CALIBRATION_NAME = "demo_keeper_current_linear"
INPUT_COLUMN = "DAQ_KEEPER_I"
REFERENCE_COLUMN = "ps_1_current"
INPUT_UNITS = "A"
REFERENCE_UNITS = "A"
MAX_PLOT_POINTS = 2000
ARTIFACT_PATH = ARTIFACT_DIR / f"{CALIBRATION_NAME}.json"
CLEANED_CSV_PATH = ARTIFACT_DIR / f"{CALIBRATION_NAME}.cleaned.csv"
REJECTED_CSV_PATH = ARTIFACT_DIR / f"{CALIBRATION_NAME}.rejected.csv"
PLOT_PATH = PLOT_DIR / f"{CALIBRATION_NAME}.svg"


def file_sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def make_read_only(path):
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def make_writable(path):
    if path.exists():
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def temp_output_path(path):
    return path.with_name(f"{path.name}.tmp")


def replace_output(temp_path, path):
    make_writable(path)
    try:
        temp_path.replace(path)
    except PermissionError:
        if path.exists():
            path.unlink()
        temp_path.replace(path)


def artifact_source_metadata():
    if not ARTIFACT_PATH.exists():
        return {}

    with ARTIFACT_PATH.open("r", encoding="utf-8") as file:
        artifact = json.load(file)

    return artifact.get("source", {})


def expected_raw_snapshot_sha256():
    source = artifact_source_metadata()
    return source.get("original_raw_snapshot_sha256") or source.get("raw_snapshot_sha256")


def expected_curated_source_sha256():
    source = artifact_source_metadata()
    return source.get("curated_source_sha256")


def ensure_raw_source_snapshot():
    RAW_SOURCE_DIR.mkdir(exist_ok=True)

    if not RAW_SOURCE_CSV.exists():
        shutil.copy2(EXTERNAL_SOURCE_CSV, RAW_SOURCE_CSV)
        make_read_only(RAW_SOURCE_CSV)

    source_hash = file_sha256(RAW_SOURCE_CSV)
    expected_hash = expected_raw_snapshot_sha256()

    if expected_hash is not None and source_hash != expected_hash:
        raise ValueError(
            "raw calibration source checksum changed; create a new calibration "
            "artifact or intentionally refresh the raw snapshot"
        )

    make_read_only(RAW_SOURCE_CSV)
    return RAW_SOURCE_CSV, source_hash


def ensure_curated_source():
    if not CURATED_SOURCE_CSV.exists():
        raise FileNotFoundError(f"Curated calibration source not found: {CURATED_SOURCE_CSV}")

    source_hash = file_sha256(CURATED_SOURCE_CSV)
    expected_hash = expected_curated_source_sha256()

    if expected_hash is not None and source_hash != expected_hash:
        raise ValueError(
            "curated calibration source checksum changed; create a new calibration "
            "artifact or intentionally refresh the curated source"
        )

    make_read_only(CURATED_SOURCE_CSV)
    return CURATED_SOURCE_CSV, source_hash


def parse_finite_float(value):
    text = str(value).strip()
    if text == "":
        raise ValueError("empty numeric field")

    number = float(text)
    if not math.isfinite(number):
        raise ValueError("non-finite numeric field")

    return number


def load_clean_rows(csv_path):
    model_rows = []
    clean_source_rows = []
    rejected_rows = []
    total_rows = 0
    dropped_reasons = {
        "invalid_input_column": 0,
        "invalid_reference_column": 0,
    }

    def record_rejected_row(source_row_number, raw_row, reason, column, error):
        rejected_row = {
            "source_row_number": source_row_number,
            "rejection_reason": reason,
            "rejection_column": column,
            "rejection_detail": str(error),
        }

        for column_name, raw_value in raw_row.items():
            if column_name is not None:
                rejected_row[column_name] = raw_value

        rejected_rows.append(rejected_row)

    with csv_path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        source_fieldnames = reader.fieldnames

        if source_fieldnames is None:
            raise ValueError(f"source CSV is missing a header row: {csv_path}")

        missing_columns = [
            column
            for column in [INPUT_COLUMN, REFERENCE_COLUMN]
            if column not in source_fieldnames
        ]
        if missing_columns:
            raise ValueError(f"source CSV is missing required columns: {missing_columns}")

        for source_row_number, raw_row in enumerate(reader, start=2):
            total_rows += 1

            try:
                input_value = parse_finite_float(raw_row.get(INPUT_COLUMN, ""))
            except ValueError as error:
                dropped_reasons["invalid_input_column"] += 1
                record_rejected_row(
                    source_row_number,
                    raw_row,
                    "invalid_input_column",
                    INPUT_COLUMN,
                    error,
                )
                continue

            try:
                reference_value = parse_finite_float(raw_row.get(REFERENCE_COLUMN, ""))
            except ValueError as error:
                dropped_reasons["invalid_reference_column"] += 1
                record_rejected_row(
                    source_row_number,
                    raw_row,
                    "invalid_reference_column",
                    REFERENCE_COLUMN,
                    error,
                )
                continue

            model_rows.append(
                {
                    "timestamp": raw_row.get("Timestamp", ""),
                    "input": input_value,
                    "reference": reference_value,
                }
            )
            clean_source_rows.append(
                {fieldname: raw_row.get(fieldname, "") for fieldname in source_fieldnames}
            )

    if len(model_rows) < 2:
        raise ValueError("at least two clean calibration rows are required")

    summary = {
        "total_rows": total_rows,
        "used_rows": len(model_rows),
        "dropped_rows": total_rows - len(model_rows),
        "dropped_reasons": dropped_reasons,
        "cleaning_rules": [
            f"Keep rows where {INPUT_COLUMN} is finite and numeric.",
            f"Keep rows where {REFERENCE_COLUMN} is finite and numeric.",
        ],
    }

    return model_rows, clean_source_rows, summary, rejected_rows, source_fieldnames


def fit_linear_model(rows):
    input_values = [row["input"] for row in rows]
    reference_values = [row["reference"] for row in rows]

    input_mean = sum(input_values) / len(input_values)
    reference_mean = sum(reference_values) / len(reference_values)

    ss_xx = sum((value - input_mean) ** 2 for value in input_values)
    if ss_xx == 0:
        raise ValueError("input column has no variation; linear calibration is undefined")

    ss_xy = sum(
        (input_value - input_mean) * (reference_value - reference_mean)
        for input_value, reference_value in zip(input_values, reference_values)
    )

    slope = ss_xy / ss_xx
    intercept = reference_mean - slope * input_mean
    predictions = [slope * value + intercept for value in input_values]
    residuals = [
        reference_value - prediction
        for reference_value, prediction in zip(reference_values, predictions)
    ]

    ss_residual = sum(residual ** 2 for residual in residuals)
    ss_total = sum((value - reference_mean) ** 2 for value in reference_values)
    r_squared = 1 - ss_residual / ss_total if ss_total else 1.0
    rmse = math.sqrt(ss_residual / len(rows))

    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "rmse": rmse,
        "input_min": min(input_values),
        "input_max": max(input_values),
        "reference_min": min(reference_values),
        "reference_max": max(reference_values),
    }


def build_artifact(
    rows,
    clean_summary,
    fit,
    raw_source_path,
    raw_source_hash,
    curated_source_path,
    curated_source_hash,
):
    original_relative_path = EXTERNAL_SOURCE_CSV.relative_to(PROJECT_ROOT).as_posix()
    raw_source_relative_path = raw_source_path.relative_to(PACKAGE_DIR).as_posix()
    curated_source_relative_path = curated_source_path.relative_to(PACKAGE_DIR).as_posix()
    cleaned_relative_path = CLEANED_CSV_PATH.relative_to(PACKAGE_DIR).as_posix()
    rejected_relative_path = REJECTED_CSV_PATH.relative_to(PACKAGE_DIR).as_posix()
    plot_relative_path = PLOT_PATH.relative_to(PACKAGE_DIR).as_posix()

    return {
        "name": CALIBRATION_NAME,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "original_file": original_relative_path,
            "original_raw_snapshot_csv": raw_source_relative_path,
            "original_raw_snapshot_sha256": raw_source_hash,
            "curated_source_csv": curated_source_relative_path,
            "curated_source_sha256": curated_source_hash,
            "input_column": INPUT_COLUMN,
            "input_units": INPUT_UNITS,
            "reference_column": REFERENCE_COLUMN,
            "reference_units": REFERENCE_UNITS,
        },
        "model": {
            "type": "linear",
            "equation": "reference = slope * input + intercept",
            "slope": fit["slope"],
            "intercept": fit["intercept"],
        },
        "quality": {
            "r_squared": fit["r_squared"],
            "rmse": fit["rmse"],
            "sample_count": len(rows),
            "input_valid_min": fit["input_min"],
            "input_valid_max": fit["input_max"],
            "reference_min": fit["reference_min"],
            "reference_max": fit["reference_max"],
        },
        "cleaning": clean_summary,
        "artifacts": {
            "raw_snapshot_csv": raw_source_relative_path,
            "curated_source_csv": curated_source_relative_path,
            "cleaned_csv": cleaned_relative_path,
            "rejected_csv": rejected_relative_path,
            "review_plot": plot_relative_path,
        },
    }


def padded_range(values):
    lower = min(values)
    upper = max(values)

    if lower == upper:
        padding = max(abs(lower), 1.0) * 0.05
    else:
        padding = (upper - lower) * 0.08

    return lower - padding, upper + padding


def tick_values(lower, upper, count):
    if count <= 1:
        return [lower]

    step = (upper - lower) / (count - 1)
    return [lower + step * index for index in range(count)]


def downsample_rows(rows):
    if len(rows) <= MAX_PLOT_POINTS:
        return rows

    step = math.ceil(len(rows) / MAX_PLOT_POINTS)
    return rows[::step]


def write_svg_plot(rows, fit, path):
    width = 960
    height = 640
    left = 86
    right = 34
    top = 54
    bottom = 78

    input_values = [row["input"] for row in rows]
    reference_values = [row["reference"] for row in rows]
    x_lower, x_upper = padded_range(input_values)
    y_lower, y_upper = padded_range(reference_values)
    plot_width = width - left - right
    plot_height = height - top - bottom

    def x_to_px(value):
        return left + (value - x_lower) / (x_upper - x_lower) * plot_width

    def y_to_px(value):
        return top + (y_upper - value) / (y_upper - y_lower) * plot_height

    sampled_rows = downsample_rows(rows)
    x_line_1 = min(input_values)
    x_line_2 = max(input_values)
    y_line_1 = fit["slope"] * x_line_1 + fit["intercept"]
    y_line_2 = fit["slope"] * x_line_2 + fit["intercept"]

    title = f"{REFERENCE_COLUMN} vs {INPUT_COLUMN}"
    equation = (
        f"y = {fit['slope']:+.8f}x {fit['intercept']:+.8f}; "
        f"R^2 = {fit['r_squared']:.6f}"
    )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-family="Arial" font-size="20" fill="black">{escape(title)}</text>',
        f'<text x="{width / 2}" y="50" text-anchor="middle" font-family="Arial" font-size="14" fill="black">{escape(equation)}</text>',
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="black" stroke-width="1.5"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="black" stroke-width="1.5"/>',
    ]

    for value in tick_values(x_lower, x_upper, 6):
        x_position = x_to_px(value)
        lines.extend(
            [
                f'<line x1="{x_position:.2f}" y1="{height - bottom}" x2="{x_position:.2f}" y2="{height - bottom + 6}" stroke="black"/>',
                f'<text x="{x_position:.2f}" y="{height - bottom + 24}" text-anchor="middle" font-family="Arial" font-size="12" fill="black">{value:.4g}</text>',
            ]
        )

    for value in tick_values(y_lower, y_upper, 6):
        y_position = y_to_px(value)
        lines.extend(
            [
                f'<line x1="{left - 6}" y1="{y_position:.2f}" x2="{left}" y2="{y_position:.2f}" stroke="black"/>',
                f'<text x="{left - 10}" y="{y_position + 4:.2f}" text-anchor="end" font-family="Arial" font-size="12" fill="black">{value:.4g}</text>',
            ]
        )

    for row in sampled_rows:
        lines.append(
            f'<circle cx="{x_to_px(row["input"]):.2f}" cy="{y_to_px(row["reference"]):.2f}" r="2.4" fill="#2f6fbb" fill-opacity="0.55"/>'
        )

    lines.extend(
        [
            f'<line x1="{x_to_px(x_line_1):.2f}" y1="{y_to_px(y_line_1):.2f}" x2="{x_to_px(x_line_2):.2f}" y2="{y_to_px(y_line_2):.2f}" stroke="#c73333" stroke-width="3"/>',
            f'<text x="{width / 2}" y="{height - 24}" text-anchor="middle" font-family="Arial" font-size="15" fill="black">{escape(INPUT_COLUMN)} ({INPUT_UNITS})</text>',
            f'<text transform="translate(24 {height / 2}) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="15" fill="black">{escape(REFERENCE_COLUMN)} ({REFERENCE_UNITS})</text>',
            f'<text x="{width - right}" y="{height - 18}" text-anchor="end" font-family="Arial" font-size="12" fill="black">plotted points: {len(sampled_rows)} of {len(rows)}</text>',
            "</svg>",
        ]
    )

    temp_path = temp_output_path(path)
    make_writable(temp_path)
    temp_path.write_text("\n".join(lines), encoding="utf-8")
    replace_output(temp_path, path)


def write_cleaned_csv(rows, fieldnames, path):
    temp_path = temp_output_path(path)
    make_writable(temp_path)

    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    replace_output(temp_path, path)


def write_rejected_csv(rejected_rows, source_fieldnames, path):
    fieldnames = [
        "source_row_number",
        "rejection_reason",
        "rejection_column",
        "rejection_detail",
    ]
    fieldnames.extend(source_fieldnames)

    temp_path = temp_output_path(path)
    make_writable(temp_path)

    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rejected_rows)

    replace_output(temp_path, path)


def write_json_artifact(artifact, path):
    temp_path = temp_output_path(path)
    make_writable(temp_path)
    temp_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    replace_output(temp_path, path)


def main():
    raw_source_path, raw_source_hash = ensure_raw_source_snapshot()
    curated_source_path, curated_source_hash = ensure_curated_source()
    rows, clean_source_rows, clean_summary, rejected_rows, source_fieldnames = load_clean_rows(curated_source_path)
    fit = fit_linear_model(rows)

    ARTIFACT_DIR.mkdir(exist_ok=True)
    PLOT_DIR.mkdir(exist_ok=True)

    artifact = build_artifact(
        rows,
        clean_summary,
        fit,
        raw_source_path,
        raw_source_hash,
        curated_source_path,
        curated_source_hash,
    )

    write_cleaned_csv(clean_source_rows, source_fieldnames, CLEANED_CSV_PATH)
    write_rejected_csv(rejected_rows, source_fieldnames, REJECTED_CSV_PATH)
    write_json_artifact(artifact, ARTIFACT_PATH)
    write_svg_plot(rows, fit, PLOT_PATH)

    print(f"Saved raw source snapshot: {raw_source_path}")
    print(f"Used curated source: {curated_source_path}")
    print(f"Saved cleaned CSV: {CLEANED_CSV_PATH}")
    print(f"Saved rejected-row CSV: {REJECTED_CSV_PATH}")
    print(f"Saved calibration artifact: {ARTIFACT_PATH}")
    print(f"Saved review plot: {PLOT_PATH}")
    print(f"{REFERENCE_COLUMN} = {fit['slope']:.8f} * {INPUT_COLUMN} {fit['intercept']:+.8f}")


if __name__ == "__main__":
    main()

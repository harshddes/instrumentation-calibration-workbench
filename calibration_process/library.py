import json
import shutil
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_ARTIFACT_DIR = PACKAGE_DIR / "artifacts"
KEEPER_CURRENT_PS1 = "demo_keeper_current_linear"


class CalibrationError(Exception):
    pass


def calibration_directory(artifact_dir=None):
    if artifact_dir is None:
        return DEFAULT_ARTIFACT_DIR

    return Path(artifact_dir)


def calibration_path(name, artifact_dir=None):
    directory = calibration_directory(artifact_dir)
    artifact_name = str(name)

    if artifact_name.endswith(".json"):
        return directory / artifact_name

    return directory / f"{artifact_name}.json"


def artifact_base_directory(artifact_dir=None):
    return calibration_directory(artifact_dir).parent


def validate_linear_calibration(calibration, path=None):
    location = f" in {path}" if path else ""

    if calibration.get("model", {}).get("type") != "linear":
        '''
        Two facts about Python dictionaries:

            some_dict["key"] → "give me the value at this key, crash if missing."
            
            some_dict.get("key", fallback) → "give me the value at this key, but if missing, hand me fallback instead, no crash."
        '''
        raise CalibrationError(f"Only linear calibration artifacts are supported{location}.")

    model = calibration["model"]
    if "slope" not in model or "intercept" not in model:
        raise CalibrationError(f"Linear calibration is missing slope or intercept{location}.")



def available_calibrations(artifact_dir=None):
    directory = calibration_directory(artifact_dir)
    if not directory.exists():
        return []

    return sorted(path.stem for path in directory.glob("*.json"))


def load_calibration(name, artifact_dir=None):
    path = calibration_path(name, artifact_dir)
    if not path.exists():
        raise FileNotFoundError(f"Calibration artifact not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        calibration = json.load(file)

    validate_linear_calibration(calibration, path)
    return calibration


def calibration_artifact_file(name, artifact_key, artifact_dir=None):
    calibration = load_calibration(name, artifact_dir)
    relative_path = calibration.get("artifacts", {}).get(artifact_key)

    if relative_path is None:
        raise CalibrationError(f"Calibration artifact is missing '{artifact_key}'.")

    path = Path(relative_path)
    if path.is_absolute():
        return path

    return artifact_base_directory(artifact_dir) / path


def calibration_plot_path(name=KEEPER_CURRENT_PS1, artifact_dir=None):
    path = calibration_artifact_file(name, "review_plot", artifact_dir)

    if not path.exists():
        raise FileNotFoundError(f"Calibration review plot not found: {path}")

    return path


def export_calibration_plot(destination, name=KEEPER_CURRENT_PS1, artifact_dir=None):
    source_path = calibration_plot_path(name, artifact_dir)
    destination_path = Path(destination)

    if destination_path.exists() and destination_path.is_dir():
        destination_path = destination_path / source_path.name

    if destination_path.parent != Path(""):
        destination_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_path, destination_path)
    return destination_path


def apply_calibration(name, value, artifact_dir=None):
    calibration = load_calibration(name, artifact_dir)
    return evaluate_calibration(calibration, value)


def apply_keeper_current_calibration(value, artifact_dir=None):
    return apply_calibration(KEEPER_CURRENT_PS1, value, artifact_dir)


def evaluate_calibration(calibration, value):
    validate_linear_calibration(calibration)

    slope = calibration["model"]["slope"]
    intercept = calibration["model"]["intercept"]

    if isinstance(value, list):
        return [slope * item + intercept for item in value]

    if isinstance(value, tuple):
        return tuple(slope * item + intercept for item in value)

    return slope * value + intercept
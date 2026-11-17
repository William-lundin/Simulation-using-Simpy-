
import json
import math
from pathlib import Path
from datetime import datetime, timezone


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def make_json_serializable(value):
    """Konverterar data till JSON-kompatibla värden."""

    if isinstance(value, dict):
        return {
            str(key): make_json_serializable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            make_json_serializable(item)
            for item in value
        ]

    if hasattr(value, "tolist"):
        return make_json_serializable(value.tolist())

    if hasattr(value, "item"):
        return make_json_serializable(value.item())

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, float) and not math.isfinite(value):
        return None

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def save_json(data, filepath):
    """Sparar JSON med UTF-8 och läsbar formatering."""

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with filepath.open("w", encoding="utf-8") as file:
        json.dump(
            make_json_serializable(data),
            file,
            indent=4,
            ensure_ascii=False,
            allow_nan=False
        )


def export_simulation_data(config, simulation_result):
    """
    Exporterar fullständiga simuleringsresultat
    och ett separat analysunderlag för LLM.

    Funktionen analyserar inte eller rekommenderar
    några förändringar.
    """

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()

    metadata = {
        "project": "ClinicFlow",
        "export_timestamp_utc": timestamp,
        "purpose": "Neutral clinic simulation data",
        "recommendations_generated": False
    }

    # Fullständig export
    raw_data = {
        "metadata": metadata,
        "configuration": config,
        "simulation_results": simulation_result
    }

    raw_filepath = OUTPUT_DIR / "simulation_raw_data.json"
    save_json(raw_data, raw_filepath)

    # Separat underlag för språkmodellen
    llm_context = {
        "metadata": metadata,

        "instructions": [
            "Analyze the supplied simulation data independently.",
            "Do not assume predefined bottlenecks.",
            "Distinguish observed patterns from causal explanations.",
            "Consider uncertainty and variation between replications.",
            "Treat all results as simulated data, not clinical evidence.",
            "Propose hypotheses that can be tested in further simulations.",
            "Do not claim that a proposed change works before testing it."
        ],

        "configuration": config,

        "simulation_summary": simulation_result.get("summary", {}),

        "station_statistics": simulation_result.get("stations", {}),

        "replication_data": simulation_result.get(
            "raw_replications", []
        ),

        "simulation_metadata": {
            "replications": simulation_result.get("replications"),
            "days_per_replication": simulation_result.get(
                "days_per_replication"
            )
        },

        "raw_data_file": str(raw_filepath)
    }

    context_filepath = OUTPUT_DIR / "llm_context.json"
    save_json(llm_context, context_filepath)

    return [raw_filepath, context_filepath]

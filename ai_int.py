import contextlib
import io
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import clinicflow


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemma-4-26b-a4b-it:free"


def run_simulation() -> str:
    """Run clinicflow and return its capacity report as text."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        resources = clinicflow.run_clinic_day()
        clinicflow.print_capacity_report(resources)
    return output.getvalue()


def generate_report(simulation_output: str) -> str:
    """Ask OpenRouter for a short Swedish summary of the simulation."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Set it before running ai_int.py."
        )

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Skriv korta och sakliga rapporter på svenska. "
                    "Använd endast uppgifter som finns i simuleringsresultatet."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Skriv en kort, enkel textrapport på svenska utan markdown "
                    "om klinikens patientflöde, "
                    "resursutnyttjande, väntetider och eventuell övertid. "
                    "Nämn viktiga resultat och undvik att hitta på orsaker.\n\n"
                    f"Simuleringsresultat:\n{simulation_output}"
                ),
            },
        ],
    }
    request = Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenRouter request failed with HTTP {error.code}: {body}"
        ) from error


    try:
        report = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            "OpenRouter returned a response without report text."
        ) from error

    if not isinstance(report, str) or not report.strip():
        raise RuntimeError("OpenRouter returned an empty report.")

    return report.strip()


def main() -> None:
    simulation_output = run_simulation()
    report = generate_report(simulation_output)
    print(report)


if __name__ == "__main__":
    main()

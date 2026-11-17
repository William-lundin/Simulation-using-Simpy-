import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def load_json(filename):
    path = CONFIG_DIR / filename

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_config():
    clinic = load_json("clinic.json")
    scheduling = load_json("scheduling.json")
    objectives = load_json("objectives.json")

    validate_config(clinic, scheduling, objectives)

    return {
        "clinic": clinic,
        "scheduling": scheduling,
        "objectives": objectives
    }


def validate_config(clinic, scheduling, objectives):

    clinic_data = clinic["clinic"]
    stations = clinic["stations"]
    patient_types = clinic["patient_types"]
    simulation = clinic["simulation"]

    if clinic_data["closing_hour"] <= clinic_data["opening_hour"]:
        raise ValueError("Stängningstiden måste vara efter öppningstiden.")

    if simulation["days"] < 1 or simulation["replications"] < 1:
        raise ValueError("Antal dagar och simuleringar måste vara minst 1.")

    if not 0 <= simulation["no_show_probability"] <= 1:
        raise ValueError("No-show-sannolikheten måste ligga mellan 0 och 1.")

    for station_name, station in stations.items():

        if station["staff"] < 1 or station["rooms"] < 1:
            raise ValueError(
                f"{station_name}: personal och rum måste vara minst 1."
            )

        if station["service_time_mean"] <= 0:
            raise ValueError(
                f"{station_name}: behandlingstiden måste vara positiv."
            )

        if station["service_time_std"] < 0:
            raise ValueError(
                f"{station_name}: standardavvikelsen får inte vara negativ."
            )

    for patient_name, patient in patient_types.items():

        if patient_name not in scheduling["booking_rules"]:
            raise ValueError(
                f"Bokningsregel saknas för {patient_name}."
            )

        if not patient["route"]:
            raise ValueError(
                f"{patient_name}: patientvägen får inte vara tom."
            )

        for station in patient["route"]:
            if station not in stations:
                raise ValueError(
                    f"{patient_name}: stationen {station} finns inte."
                )

    weights = objectives["weights"]

    if any(value < 0 for value in weights.values()):
        raise ValueError("Målfunktionens vikter får inte vara negativa.")

    if sum(weights.values()) <= 0:
        raise ValueError("Minst en vikt måste vara större än noll.")

    for name, rule in scheduling["booking_rules"].items():

        if name not in patient_types:
            raise ValueError(f"Okänd patienttyp i bokningsreglerna: {name}")

        if rule["interval_minutes"] <= 0:
            raise ValueError(
                f"{name}: bokningsintervallet måste vara positivt."
            )

        if rule["first_offset_minutes"] < 0:
            raise ValueError(
                f"{name}: startförskjutningen får inte vara negativ."
            )
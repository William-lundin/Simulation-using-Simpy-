
from clinicflow.config_loader import load_config
from clinicflow.analytics.metrics import run_experiment
from clinicflow.llm.exporter import export_simulation_data


def main():

    print("=" * 45)
    print("              CLINICFLOW")
    print("=" * 45)

    print("\nLäser in klinikens konfiguration...")

    loaded = load_config()

    # Hantera både separata konfigurationsfiler
    # och en samlad konfiguration.
    if isinstance(loaded, tuple):

        config = None
        booking_rules = None

        for item in loaded:
            if not isinstance(item, dict):
                continue

            if "clinic" in item and "stations" in item:
                config = item

            if "booking_rules" in item:
                booking_rules = item["booking_rules"]

        if config is None:
            raise ValueError(
                "Klinikkonfiguration saknas i load_config()."
            )

        if booking_rules is None:
            booking_rules = config.get("booking_rules")

    else:
        config = loaded
        booking_rules = config.get("booking_rules")

        if booking_rules is None and "scheduling" in config:
            booking_rules = config["scheduling"].get(
                "booking_rules"
            )

    if booking_rules is None:
        raise ValueError(
            "Kunde inte hitta bokningsreglerna."
        )

    clinic_name = config.get("clinic", {}).get(
        "name", "Okänd klinik"
    )

    print(f"Klinik: {clinic_name}")

    print("\nStartar simuleringen...")
    print("Ingen automatisk optimering utförs.")

    # Kör den ursprungliga simulerings- och analysmotorn.
    result = run_experiment(
        config,
        booking_rules,
        seed_offset=0
    )

    print("\nSimuleringen är klar.")

    # Visa endast befintliga mätvärden.
    summary = result.get("summary", {})

    print("\n" + "=" * 45)
    print("          SIMULERINGSRESULTAT")
    print("=" * 45)

    if summary:
        for key, value in summary.items():
            print(f"{key}: {value}")
    else:
        print("Ingen sammanfattande statistik returnerades.")

    # Exportera data utan att tolka eller rangordna resultat.
    print("\nExporterar simuleringsdata...")

    files = export_simulation_data(
        config=config,
        simulation_result=result
    )

    print("\n" + "=" * 45)
    print("                 KLART")
    print("=" * 45)

    for filepath in files:
        print(f"Skapad fil: {filepath}")

    print("\nPython har inte genererat några rekommendationer.")
    print("Simuleringsdata är redo för extern LLM-analys.")


if __name__ == "__main__":
    main()

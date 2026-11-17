import statistics
import numpy as np
from clinicflow.simulation.engine import simulate_day


def percentile(values, percent):
    if not values:
        return 0.0

    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    rank = (len(ordered) - 1) * (percent / 100.0)
    lower_index = int(math.floor(rank))
    upper_index = int(math.ceil(rank))

    if lower_index == upper_index:
        return float(ordered[lower_index])

    fraction = rank - lower_index
    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    return float(lower_value + (upper_value - lower_value) * fraction)


def safe_mean(values):
    return statistics.mean(values) if values else 0.0


def confidence_interval(values):

    if len(values) < 2:
        value = safe_mean(values)
        return {
            "lower": value,
            "upper": value
        }

    mean = statistics.mean(values)
    standard_deviation = statistics.stdev(values)

    margin = 1.96 * standard_deviation / (len(values) ** 0.5)

    return {
        "lower": mean - margin,
        "upper": mean + margin
    }


def run_replication(config, booking_rules, seed):

    days = config["clinic"]["simulation"]["days"]

    patients_booked = 0
    patients_arrived = 0
    patients_completed = 0
    patients_unfinished = 0
    patients_no_show = 0

    patient_waits = []
    total_times = []

    station_data = {}

    for station_name in config["clinic"]["stations"]:

        station_data[station_name] = {
            "busy_minutes": 0.0,
            "capacity_minutes": 0.0,
            "overtime_minutes": 0.0,
            "waits": [],
            "max_queue": 0
        }

    for day in range(days):

        day_result = simulate_day(
            config["clinic"],
            booking_rules,
            seed + day
        )

        patients_booked += day_result["metadata"]["patients_booked"]

        for patient in day_result["patients"]:

            if patient["status"] == "no_show":
                patients_no_show += 1
                continue

            patients_arrived += 1

            if patient["status"] == "completed":
                patients_completed += 1

                total_times.append(
                    patient["total_time_in_clinic"]
                )

            elif patient["status"] == "unfinished":
                patients_unfinished += 1

            patient_waits.append(
                sum(patient["waits"].values())
            )

        for station_name, data in day_result["stations"].items():

            target = station_data[station_name]

            target["busy_minutes"] += data["busy_minutes"]
            target["capacity_minutes"] += data["capacity_minutes"]
            target["overtime_minutes"] += data["overtime_minutes"]
            target["waits"].extend(data["waits"])

            target["max_queue"] = max(
                target["max_queue"],
                data["max_queue"]
            )

    station_summary = {}

    utilizations = []

    for station_name, data in station_data.items():

        utilization = (
            data["busy_minutes"] / data["capacity_minutes"]
            if data["capacity_minutes"] > 0
            else 0.0
        )

        utilizations.append(utilization)

        station_summary[station_name] = {
            "average_wait_minutes": safe_mean(data["waits"]),
            "utilization": utilization,
            "busy_minutes": data["busy_minutes"],
            "capacity_minutes": data["capacity_minutes"],
            "idle_minutes": max(
                0.0,
                data["capacity_minutes"] - data["busy_minutes"]
            ),
            "overtime_minutes": data["overtime_minutes"],
            "max_queue": data["max_queue"]
        }

    unfinished_rate = (
        patients_unfinished / patients_arrived
        if patients_arrived > 0 else 0.0
    )

    total_capacity = sum(
        data["capacity_minutes"]
        for data in station_data.values()
    )

    total_idle = sum(
        max(
            0.0,
            data["capacity_minutes"] - data["busy_minutes"]
        )
        for data in station_data.values()
    )

    staff_idle_rate = (
        total_idle / total_capacity
        if total_capacity > 0 else 0.0
    )

    resource_imbalance = (
        max(utilizations) - min(utilizations)
        if utilizations else 0.0
    )

    return {
        "patients_booked": patients_booked,
        "patients_arrived": patients_arrived,
        "patients_completed": patients_completed,
        "patients_unfinished": patients_unfinished,
        "patients_no_show": patients_no_show,

        "average_wait_minutes": safe_mean(patient_waits),
        "average_time_in_clinic": safe_mean(total_times),

        "unfinished_patient_rate": unfinished_rate,
        "overtime_minutes": sum(
            data["overtime_minutes"]
            for data in station_data.values()
        ),

        "staff_idle_rate": staff_idle_rate,
        "resource_imbalance": resource_imbalance,

        "stations": station_summary
    }


def run_experiment(config, booking_rules, seed_offset=0):

    repetitions = config["clinic"]["simulation"]["replications"]
    base_seed = config["clinic"]["simulation"]["random_seed"]

    runs = []

    for repetition in range(repetitions):

        seed = base_seed + seed_offset + repetition * 10000

        result = run_replication(
            config,
            booking_rules,
            seed
        )

        runs.append(result)

    metric_names = [
        "patients_booked",
        "patients_arrived",
        "patients_completed",
        "patients_unfinished",
        "patients_no_show",
        "average_wait_minutes",
        "average_time_in_clinic",
        "unfinished_patient_rate",
        "overtime_minutes",
        "staff_idle_rate",
        "resource_imbalance"
    ]

    summary = {}

    for metric in metric_names:

        values = [run[metric] for run in runs]

        summary[metric] = {
            "mean": safe_mean(values),
            "median": statistics.median(values),
            "p90": float(np.percentile(values, 90)),
            "confidence_interval_95": confidence_interval(values)
        }

    station_summary = {}

    for station_name in runs[0]["stations"]:

        station_summary[station_name] = {}

        for metric in [
            "average_wait_minutes",
            "utilization",
            "busy_minutes",
            "idle_minutes",
            "overtime_minutes",
            "max_queue"
        ]:

            values = [
                run["stations"][station_name][metric]
                for run in runs
            ]

            station_summary[station_name][metric] = {
                "mean": safe_mean(values),
                "p90": float(np.percentile(values, 90))
            }

    return {
        "replications": len(runs),
        "days_per_replication": config["clinic"]["simulation"]["days"],
        "summary": summary,
        "stations": station_summary,
        "raw_replications": runs
    }
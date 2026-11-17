
import random
import simpy


def generate_appointments(config, booking_rules):
    opening = config["clinic"]["opening_hour"] * 60
    closing = config["clinic"]["closing_hour"] * 60

    appointments = []
    patient_id = 0

    for patient_type, rule in booking_rules.items():
        interval = rule["interval_minutes"]
        first_offset = rule["first_offset_minutes"]

        appointment_time = opening + first_offset

        while appointment_time < closing:
            patient_id += 1

            appointments.append({
                "patient_id": patient_id,
                "patient_type": patient_type,
                "appointment_time": appointment_time
            })

            appointment_time += interval

    return sorted(appointments, key=lambda x: x["appointment_time"])


def create_station_resources(env, config):
    resources = {}
    capacities = {}

    for station_name, station_config in config["stations"].items():
        capacity = min(
            station_config["staff"],
            station_config["rooms"]
        )

        resources[station_name] = simpy.Resource(
            env,
            capacity=capacity
        )

        capacities[station_name] = capacity

    return resources, capacities


def patient_process(
    env,
    patient,
    config,
    resources,
    results,
    rng
):
    simulation_config = config["simulation"]

    opening = config["clinic"]["opening_hour"] * 60
    closing = config["clinic"]["closing_hour"] * 60

    arrival_std = simulation_config["arrival_deviation_std"]
    no_show_probability = simulation_config["no_show_probability"]

    appointment_time = patient["appointment_time"]

    actual_arrival = rng.gauss(
        appointment_time,
        arrival_std
    )

    actual_arrival = max(opening, actual_arrival)

    yield env.timeout(max(0, actual_arrival - opening))

    patient_record = {
        "patient_id": patient["patient_id"],
        "patient_type": patient["patient_type"],
        "appointment_time": appointment_time,
        "arrival_time": actual_arrival,
        "start_times": {},
        "waits": {},
        "finish_times": {},
        "status": "pending"
    }

    if rng.random() < no_show_probability:
        patient_record["status"] = "no_show"
        results["patients"].append(patient_record)
        return

    patient_record["status"] = "arrived"

    route = config["patient_types"][
        patient["patient_type"]
    ]["route"]

    for station_name in route:

        if env.now + opening >= closing:
            patient_record["status"] = "unfinished"
            results["patients"].append(patient_record)
            return

        station_config = config["stations"][station_name]
        resource = resources[station_name]

        queue_start = env.now

        with resource.request() as request:
            yield request

            wait_time = env.now - queue_start

            if env.now + opening >= closing:
                patient_record["waits"][station_name] = wait_time
                patient_record["status"] = "unfinished"

                results["stations"][station_name]["waits"].append(
                    wait_time
                )

                results["stations"][station_name]["max_queue"] = max(
                    results["stations"][station_name]["max_queue"],
                    len(resource.queue)
                )

                results["patients"].append(patient_record)
                return

            service_start = env.now

            mean = station_config["service_time_mean"]
            std = station_config["service_time_std"]

            service_time = max(
                1.0,
                rng.gauss(mean, std)
            )

            patient_record["start_times"][station_name] = (
                service_start + opening
            )

            patient_record["waits"][station_name] = wait_time

            station_results = results["stations"][station_name]

            station_results["waits"].append(wait_time)

            yield env.timeout(service_time)

            service_end = env.now

            patient_record["finish_times"][station_name] = (
                service_end + opening
            )

            station_results["busy_minutes"] += service_time

            # Behandlingstid som sker inom klinikens öppettider.
            opening_duration = closing - opening

            busy_during_opening = max(
                0.0,
                min(service_end, opening_duration) - service_start
            )

            station_results["busy_minutes_open"] += (
                busy_during_opening
            )

            # Behandlingstid efter klinikens stängning.
            overtime_start = closing - opening

            overtime = max(
                0.0,
                service_end - max(service_start, overtime_start)
            )

            station_results["overtime_minutes"] += overtime

    patient_record["status"] = "completed"
    patient_record["finish_time"] = env.now + opening

    patient_record["total_time_in_clinic"] = (
        patient_record["finish_time"] - actual_arrival
    )

    results["patients"].append(patient_record)


def simulate_day(config, booking_rules, seed):
    rng = random.Random(seed)
    env = simpy.Environment()

    resources, capacities = create_station_resources(
        env,
        config
    )

    results = {
        "patients": [],
        "stations": {}
    }

    opening = config["clinic"]["opening_hour"] * 60
    closing = config["clinic"]["closing_hour"] * 60
    opening_duration = closing - opening

    for station_name in config["stations"]:
        results["stations"][station_name] = {
            "busy_minutes": 0.0,
            "busy_minutes_open": 0.0,
            "overtime_minutes": 0.0,
            "max_queue": 0,
            "waits": []
        }

    appointments = generate_appointments(
        config,
        booking_rules
    )

    for patient in appointments:
        env.process(
            patient_process(
                env,
                patient,
                config,
                resources,
                results,
                rng
            )
        )

    env.run()

    for station_name, station_result in results["stations"].items():
        available_minutes = (
            capacities[station_name] * opening_duration
        )

        station_result["capacity_minutes"] = available_minutes

        station_result["utilization"] = (
            station_result["busy_minutes_open"] / available_minutes
            if available_minutes > 0 else 0.0
        )

        station_result["idle_minutes"] = max(
            0.0,
            available_minutes - station_result["busy_minutes_open"]
        )

    results["metadata"] = {
        "patients_booked": len(appointments),
        "opening_duration_minutes": opening_duration,
        "opening_time_minutes": opening
    }

    return results

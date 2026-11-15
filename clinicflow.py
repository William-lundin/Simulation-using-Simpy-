import simpy
import random
import statistics
from collections import defaultdict


# ============================================================
# 1. KLINIKENS INSTÄLLNINGAR
# ============================================================
# Alla värden som senare ska kunna ändras av en LLM ligger här.
# Simuleringslogiken längre ned ska inte behöva skrivas om.

CONFIG = {

    # Öppettider
    "opening_hour": 8,
    "closing_hour": 16,

    # Hur ofta kliniken bokar in en patient.
    # 12 minuter innebär ungefär 40 bokningar under 8 timmar.
    "appointment_interval": 5,

    # Variation i när patienterna faktiskt anländer.
    # Exempelvis kan en patient komma några minuter tidigt eller sent.
    "arrival_std": 3,

    # Andel patienter som inte dyker upp.
    "no_show_probability": 0.05,

    # Personal per station
    "nurses": 3,
    "doctors": 4,
    "lab_staff": 2,

    # Rum per station
    "nurse_rooms": 3,
    "doctor_rooms": 2,
    "lab_rooms": 2,

    # Behandlingstider i minuter.
    # Värdena används som typvärden i en triangulär fördelning.
    "nurse_time": 12,
    "doctor_time": 20,
    "lab_time": 10,

    # Variation i behandlingstider.
    # 0.2 betyder att tiden kan variera ungefär +/- 20 %.
    "service_time_variation": 0.2,

    # Fördelning av olika patienttyper.
    # Värdena behöver inte summera till 100, eftersom de normaliseras.
    "patient_types": {
        "nurse_only": 30,
        "doctor_only": 35,
        "nurse_doctor": 20,
        "nurse_lab": 15
    },

    # Reproducerbara resultat.
    "random_seed": 42
}


# ============================================================
# 2. GRUNDLÄGGANDE TID OCH KAPACITET
# ============================================================

OPENING_MINUTE = CONFIG["opening_hour"] * 60
CLOSING_MINUTE = CONFIG["closing_hour"] * 60

OPENING_DURATION = CLOSING_MINUTE - OPENING_MINUTE

# Antalet bokningar skapas utifrån öppettiden och intervallet.
# Den sista bokningen måste ligga före stängning.
APPOINTMENT_INTERVAL = CONFIG["appointment_interval"]

NUMBER_OF_APPOINTMENTS = (
    OPENING_DURATION + APPOINTMENT_INTERVAL - 1
) // APPOINTMENT_INTERVAL


# ============================================================
# 3. PATIENTTYPER OCH BEHANDLINGSFLÖDEN
# ============================================================

# Varje patienttyp har en bestämd vårdväg.
# Ordningen är viktig: en patient som behöver sjuksköterska
# och läkare måste först genomföra sjuksköterskebesöket.

PATIENT_ROUTES = {

    "nurse_only": ["nurse"],

    "doctor_only": ["doctor"],

    "nurse_doctor": ["nurse", "doctor"],

    "nurse_lab": ["nurse", "lab"]
}


# Behandlingstidernas grundvärden.
SERVICE_TIMES = {
    "nurse": CONFIG["nurse_time"],
    "doctor": CONFIG["doctor_time"],
    "lab": CONFIG["lab_time"]
}


# ============================================================
# 4. SIMULERINGENS RESULTATDATA
# ============================================================

# All statistik samlas här.
# defaultdict gör att nya stationer kan läggas till utan
# att vi först behöver skapa en separat variabel för dem.

stats = {

    "patients_booked": 0,
    "patients_arrived": 0,
    "patients_no_show": 0,
    "patients_after_closing": 0,

    "patients_completed": 0,
    "patients_incomplete": 0,

    "station_treatments": defaultdict(int),

    "station_waits": defaultdict(list),

    "station_busy_open": defaultdict(float),

    "station_busy_total": defaultdict(float),

    "station_max_queue": defaultdict(int),

    "patient_total_times": [],

    "patient_types": defaultdict(int),

    "patients_not_started": defaultdict(int)
}


# ============================================================
# 5. SKAPA KLINIKENS RESURSER
# ============================================================

# En behandlingsplats kräver både personal och ett rum.
#
# Exempel:
# 3 sjuksköterskor + 2 sjuksköterskerum = 2 samtidiga platser.
#
# Vi modellerar dessa som kombinerade behandlingsplatser.
# Det betyder att en plats alltid har både personal och rum.

def create_resources(env):

    resources = {}

    station_settings = {

        "nurse": (
            CONFIG["nurses"],
            CONFIG["nurse_rooms"]
        ),

        "doctor": (
            CONFIG["doctors"],
            CONFIG["doctor_rooms"]
        ),

        "lab": (
            CONFIG["lab_staff"],
            CONFIG["lab_rooms"]
        )
    }

    for station, (staff, rooms) in station_settings.items():

        capacity = min(staff, rooms)

        resources[station] = simpy.Resource(
            env,
            capacity=capacity
        )

    return resources


# ============================================================
# 6. SLUMPA PATIENTTYP
# ============================================================

def choose_patient_type():

    patient_types = CONFIG["patient_types"]

    names = list(patient_types.keys())

    weights = list(patient_types.values())

    return random.choices(
        names,
        weights=weights,
        k=1
    )[0]


# ============================================================
# 7. SKAPA EN BOKNINGSLISTA FÖR EN DAG
# ============================================================

def create_schedule():

    schedule = []

    for i in range(NUMBER_OF_APPOINTMENTS):

        appointment_time = (
            OPENING_MINUTE
            + i * APPOINTMENT_INTERVAL
        )

        # Säkerställ att inga bokningar sker efter stängning.
        if appointment_time >= CLOSING_MINUTE:
            break

        patient_type = choose_patient_type()

        # Patientens faktiska ankomst kan avvika från bokningen.
        arrival_delay = random.gauss(
            0,
            CONFIG["arrival_std"]
        )

        # No-show bestäms redan när schemat skapas.
        no_show = (
            random.random()
            < CONFIG["no_show_probability"]
        )

        schedule.append({

            "patient_id": i + 1,

            "appointment_time": appointment_time,

            "patient_type": patient_type,

            "arrival_delay": arrival_delay,

            "no_show": no_show
        })

    return schedule


# ============================================================
# 8. HJÄLPFUNKTIONER FÖR TID
# ============================================================

def format_time(minutes):

    hours = int(minutes // 60)

    mins = int(minutes % 60)

    return f"{hours:02d}:{mins:02d}"


def open_hours_overlap(start, end):

    """
    Räknar ut hur många minuter av en behandling som
    ligger inom klinikens ordinarie öppettider.

    Exempel:
    Behandling 15:50–16:10 ger:
    10 minuter inom öppettid.
    10 minuter övertid.
    """

    overlap_start = max(start, OPENING_MINUTE)

    overlap_end = min(end, CLOSING_MINUTE)

    return max(0, overlap_end - overlap_start)


# ============================================================
# 9. GENOMFÖR EN BEHANDLING
# ============================================================

def treatment(env, patient_id, station, resources):

    resource = resources[station]

    # Tidpunkten då patienten börjar vänta.
    queue_start = env.now

    # Patienten begär en behandlingsplats.
    with resource.request() as request:

        # Om alla platser är upptagna hamnar patienten i kö.
        # SimPy släpper fram patienten när en plats blir ledig.
        if resource.count >= resource.capacity:

            current_queue = len(resource.queue) + 1

            stats["station_max_queue"][station] = max(
                stats["station_max_queue"][station],
                current_queue
            )

        yield request

        # Beräkna väntetid.
        wait_time = env.now - queue_start

        stats["station_waits"][station].append(
            wait_time
        )

        # Viktigt:
        # En patient får inte påbörja en ny behandling
        # efter att kliniken har stängt.
        if env.now >= CLOSING_MINUTE:

            stats["patients_not_started"][station] += 1

            return False

        # Slumpa behandlingstid.
        average_time = SERVICE_TIMES[station]

        variation = CONFIG["service_time_variation"]

        service_time = random.triangular(

            average_time * (1 - variation),

            average_time * (1 + variation),

            average_time
        )

        service_start = env.now

        service_end = service_start + service_time

        # Mät hur mycket av behandlingstiden som ligger
        # inom ordinarie öppettid.
        busy_during_opening = open_hours_overlap(
            service_start,
            service_end
        )

        stats["station_busy_open"][station] += (
            busy_during_opening
        )

        stats["station_busy_total"][station] += (
            service_time
        )

        stats["station_treatments"][station] += 1

        # Behandlingen får slutföras även om den passerar
        # klinikens stängningstid.
        yield env.timeout(service_time)

        return True


# ============================================================
# 10. PATIENTENS VÅRDFÖRLOPP
# ============================================================

def patient_process(env, patient, resources):

    patient_id = patient["patient_id"]

    appointment_time = patient["appointment_time"]

    patient_type = patient["patient_type"]

    arrival_delay = patient["arrival_delay"]

    no_show = patient["no_show"]

    stats["patients_booked"] += 1

    stats["patient_types"][patient_type] += 1

    # Beräkna faktisk ankomsttid.
    actual_arrival = max(
        OPENING_MINUTE,
        appointment_time + arrival_delay
    )

    # Simuleringens klocka börjar vid minut 0.
    # Vänta tills patienten faktiskt anländer.
    yield env.timeout(
        max(0, actual_arrival - env.now)
    )

    # Patient som inte dyker upp.
    if no_show:

        stats["patients_no_show"] += 1

        return

    stats["patients_arrived"] += 1

    # Patient som anländer efter stängning kan inte tas emot.
    if env.now >= CLOSING_MINUTE:

        stats["patients_after_closing"] += 1

        stats["patients_incomplete"] += 1

        return

    patient_start = env.now

    route = PATIENT_ROUTES[patient_type]

    completed_stations = 0

    # Patienten går igenom sin vårdväg i rätt ordning.
    for station in route:

        completed = yield from treatment(
            env,
            patient_id,
            station,
            resources
        )

        # Om behandlingen inte kan påbörjas före stängning
        # avbryts patientens återstående vårdväg.
        if not completed:

            stats["patients_incomplete"] += 1

            return

        completed_stations += 1

    # Patienten har genomfört hela sin vårdväg.
    stats["patients_completed"] += 1

    total_time = env.now - patient_start

    stats["patient_total_times"].append(
        total_time
    )


# ============================================================
# 11. KÖR EN KLINIKDAG
# ============================================================

def run_clinic_day():

    # Samma slumpfrö ger reproducerbara resultat.
    random.seed(CONFIG["random_seed"])

    env = simpy.Environment()

    resources = create_resources(env)

    schedule = create_schedule()

    # Lägg in alla patientprocesser.
    # Varje patient hanteras självständigt.
    for patient in schedule:

        env.process(
            patient_process(
                env,
                patient,
                resources
            )
        )

    # Kör tills samtliga patienter har avslutat
    # eller avbrutit sitt vårdförlopp.
    env.run()

    return resources


# ============================================================
# 12. KAPACITETSRAPPORT
# ============================================================

def print_capacity_report(resources):

    print("\n")
    print("=" * 65)
    print("                 CLINICFLOW – KAPACITETSRAPPORT")
    print("=" * 65)

    print(
        f"\nKlinikens öppettider: "
        f"{format_time(OPENING_MINUTE)}–"
        f"{format_time(CLOSING_MINUTE)}"
    )

    print(
        f"Öppettid: {OPENING_DURATION} minuter"
    )

    print(
        f"Bokningsintervall: "
        f"{APPOINTMENT_INTERVAL} minuter"
    )

    print(
        f"Planerade bokningar: "
        f"{NUMBER_OF_APPOINTMENTS}"
    )

    # --------------------------------------------------------
    # PATIENTFLÖDE
    # --------------------------------------------------------

    print("\n" + "-" * 65)
    print("1. PATIENTFLÖDE")
    print("-" * 65)

    print(
        f"Bokade patienter: "
        f"{stats['patients_booked']}"
    )

    print(
        f"Anlända patienter: "
        f"{stats['patients_arrived']}"
    )

    print(
        f"Uteblivna patienter: "
        f"{stats['patients_no_show']}"
    )

    print(
        f"Patienter efter stängning: "
        f"{stats['patients_after_closing']}"
    )

    print(
        f"Fullständigt genomförda vårdförlopp: "
        f"{stats['patients_completed']}"
    )

    print(
        f"Ofullständiga vårdförlopp: "
        f"{stats['patients_incomplete']}"
    )

    # --------------------------------------------------------
    # PATIENTTYPER
    # --------------------------------------------------------

    print("\n" + "-" * 65)
    print("2. FÖRDELNING AV PATIENTTYPER")
    print("-" * 65)

    for patient_type, count in stats["patient_types"].items():

        print(
            f"{patient_type:20s}: {count:3d} patienter"
        )

    # --------------------------------------------------------
    # STATIONERNAS KAPACITET
    # --------------------------------------------------------

    print("\n" + "-" * 65)
    print("3. RESURSUTNYTTJANDE PER STATION")
    print("-" * 65)

    station_settings = {

        "nurse": (
            CONFIG["nurses"],
            CONFIG["nurse_rooms"]
        ),

        "doctor": (
            CONFIG["doctors"],
            CONFIG["doctor_rooms"]
        ),

        "lab": (
            CONFIG["lab_staff"],
            CONFIG["lab_rooms"]
        )
    }

    for station, (staff, rooms) in station_settings.items():

        capacity = resources[station].capacity

        busy_minutes = stats["station_busy_open"][station]

        # Total möjlig kapacitet under öppettiden.
        total_available_minutes = (
            capacity * OPENING_DURATION
        )

        if total_available_minutes > 0:

            utilization = (
                busy_minutes / total_available_minutes
            ) * 100

        else:

            utilization = 0

        waits = stats["station_waits"][station]

        average_wait = (
            statistics.mean(waits)
            if waits else 0
        )

        max_wait = (
            max(waits)
            if waits else 0
        )

        print(f"\n{station.upper()}")

        print(
            f"  Personal: {staff}"
        )

        print(
            f"  Rum: {rooms}"
        )

        print(
            f"  Samtidiga behandlingsplatser: {capacity}"
        )

        print(
            f"  Genomförda behandlingar: "
            f"{stats['station_treatments'][station]}"
        )

        print(
            f"  Utnyttjande under öppettid: "
            f"{utilization:.1f}%"
        )

        print(
            f"  Genomsnittlig väntetid: "
            f"{average_wait:.1f} minuter"
        )

        print(
            f"  Längsta registrerade väntetid: "
            f"{max_wait:.1f} minuter"
        )

        print(
            f"  Största observerade kö: "
            f"{stats['station_max_queue'][station]} patienter"
        )

        print(
            f"  Behandlingar som inte hann starta: "
            f"{stats['patients_not_started'][station]}"
        )

    # --------------------------------------------------------
    # TOTAL VÄNTETID OCH PATIENTTID
    # --------------------------------------------------------

    print("\n" + "-" * 65)
    print("4. PATIENTUPPLEVELSE")
    print("-" * 65)

    all_waits = []

    for waits in stats["station_waits"].values():

        all_waits.extend(waits)

    if all_waits:

        print(
            f"Genomsnittlig väntetid per behandling: "
            f"{statistics.mean(all_waits):.1f} minuter"
        )

        print(
            f"Total registrerad väntetid: "
            f"{sum(all_waits):.1f} minuter"
        )

    if stats["patient_total_times"]:

        print(
            f"Genomsnittlig tid på kliniken för "
            f"färdigbehandlade patienter: "
            f"{statistics.mean(stats['patient_total_times']):.1f} minuter"
        )

    # --------------------------------------------------------
    # ÖVERTID
    # --------------------------------------------------------

    print("\n" + "-" * 65)
    print("5. ÖVERTID")
    print("-" * 65)

    for station in station_settings:

        total_busy = stats["station_busy_total"][station]

        busy_open = stats["station_busy_open"][station]

        overtime = max(0, total_busy - busy_open)

        print(
            f"{station.upper():10s}: "
            f"{overtime:.1f} minuter behandlingstid efter stängning"
        )

    print("\n" + "=" * 65)
    print("                  SIMULERING AVSLUTAD")
    print("=" * 65)


# ============================================================
# 13. STARTA PROGRAMMET
# ============================================================

if __name__ == "__main__":

    resources = run_clinic_day()

    print_capacity_report(resources)
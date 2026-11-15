
import simpy
import random
import statistics
from collections import defaultdict


# ============================================================
# 1. KLINIKENS KONFIGURATION
# ============================================================

CONFIG = {

    "opening_hour": 8,
    "closing_hour": 16,

    "arrival_std": 3,
    "no_show_probability": 0.05,
    "service_time_variation": 0.20,

    "service_times": {
        "nurse": 12,
        "doctor": 20,
        "lab": 10
    },

    "rooms": {
        "nurse": 3,
        "doctor": 2,
        "lab": 2
    },

    # Separata bokningsscheman per patienttyp.
    # Tider anges i minuter efter öppning.

    "appointment_schedules": {

        "nurse_only": {
            "interval": 20,
            "first": 0
        },

        "doctor_only": {
            "interval": 20,
            "first": 5
        },

        "nurse_doctor": {
            "interval": 30,
            "first": 10
        },

        "nurse_lab": {
            "interval": 30,
            "first": 15
        }
    },

    # Individuella arbetsscheman.
    # Tider anges i minuter efter öppning.

    "staff_schedules": {

        "nurse": [

            {
                "name": "Nurse_1",
                "start": 0,
                "end": 480,
                "lunch_start": 240,
                "lunch_duration": 30
            },

            {
                "name": "Nurse_2",
                "start": 0,
                "end": 480,
                "lunch_start": 270,
                "lunch_duration": 30
            },

            {
                "name": "Nurse_3",
                "start": 0,
                "end": 480,
                "lunch_start": 300,
                "lunch_duration": 30
            }
        ],

        "doctor": [

            {
                "name": "Doctor_1",
                "start": 0,
                "end": 480,
                "lunch_start": 240,
                "lunch_duration": 30
            },

            {
                "name": "Doctor_2",
                "start": 0,
                "end": 480,
                "lunch_start": 270,
                "lunch_duration": 30
            },

            {
                "name": "Doctor_3",
                "start": 0,
                "end": 480,
                "lunch_start": 300,
                "lunch_duration": 30
            },

            {
                "name": "Doctor_4",
                "start": 0,
                "end": 480,
                "lunch_start": 330,
                "lunch_duration": 30
            }
        ],

        "lab": [

            {
                "name": "Lab_1",
                "start": 0,
                "end": 480,
                "lunch_start": 240,
                "lunch_duration": 30
            },

            {
                "name": "Lab_2",
                "start": 0,
                "end": 480,
                "lunch_start": 270,
                "lunch_duration": 30
            }
        ]
    },

    "random_seed": 42
}


# ============================================================
# 2. TIDSHANTERING
# ============================================================

OPENING_MINUTE = CONFIG["opening_hour"] * 60
CLOSING_MINUTE = CONFIG["closing_hour"] * 60

OPENING_DURATION = CLOSING_MINUTE - OPENING_MINUTE


def format_time(minutes):

    hours = int(minutes // 60)
    mins = int(minutes % 60)

    return f"{hours:02d}:{mins:02d}"


def interval_overlap(start1, end1, start2, end2):

    return max(
        0,
        min(end1, end2) - max(start1, start2)
    )


# ============================================================
# 3. PATIENTTYPER OCH VÅRDFLÖDEN
# ============================================================

PATIENT_ROUTES = {

    "nurse_only": ["nurse"],

    "doctor_only": ["doctor"],

    "nurse_doctor": ["nurse", "doctor"],

    "nurse_lab": ["nurse", "lab"]
}


# ============================================================
# 4. STATISTIK
# ============================================================

def create_stats():

    return {

        "patients_booked": 0,
        "patients_arrived": 0,
        "patients_no_show": 0,
        "patients_after_closing": 0,

        "patients_completed": 0,
        "patients_incomplete": 0,

        "patient_types": defaultdict(int),

        "station_treatments": defaultdict(int),

        "station_waits": defaultdict(list),

        "station_max_queue": defaultdict(int),

        "station_not_started": defaultdict(int),

        "staff_busy_open": defaultdict(float),
        "staff_busy_total": defaultdict(float),

        "room_busy_open": defaultdict(float),
        "room_busy_total": defaultdict(float),

        "patient_total_times": []
    }


stats = create_stats()


# ============================================================
# 5. STATION MANAGER
# ============================================================

class StationManager:

    """
    Hanterar personal, rum och kö separat.

    En behandling kan endast starta när både
    personal och behandlingsrum är tillgängliga.

    En central köhanterare tilldelar båda resurserna
    samtidigt. Ingen resurs reserveras i förväg.
    """

    def __init__(self, env, station, staff, room_count):

        self.env = env
        self.station = station

        self.staff = staff

        self.rooms = [
            f"{station}_room_{i + 1}"
            for i in range(room_count)
        ]

        self.busy_staff = set()
        self.busy_rooms = set()

        self.queue = []

        self.changed = env.event()

        self.create_schedule_events()

        env.process(self.dispatcher())

    # --------------------------------------------------------
    # KONTROLLERA PERSONALENS TILLGÄNGLIGHET
    # --------------------------------------------------------

    def staff_is_available(self, employee):

        now = self.env.now

        if not (
            employee["start"] <= now < employee["end"]
        ):
            return False

        lunch_start = employee["lunch_start"]

        lunch_end = (
            lunch_start + employee["lunch_duration"]
        )

        if lunch_start <= now < lunch_end:
            return False

        if employee["name"] in self.busy_staff:
            return False

        return True

    # --------------------------------------------------------
    # VÄCK KÖHANTERAREN
    # --------------------------------------------------------

    def notify(self):

        old_event = self.changed

        self.changed = self.env.event()

        if not old_event.triggered:
            old_event.succeed()

    # --------------------------------------------------------
    # SCHEMALAGDA HÄNDELSER
    # --------------------------------------------------------

    def create_schedule_events(self):

        boundaries = {CLOSING_MINUTE}

        for employee in self.staff:

            boundaries.add(employee["start"])
            boundaries.add(employee["end"])

            lunch_start = employee["lunch_start"]

            lunch_end = (
                lunch_start + employee["lunch_duration"]
            )

            boundaries.add(lunch_start)
            boundaries.add(lunch_end)

        for boundary in sorted(boundaries):

            if 0 < boundary <= CLOSING_MINUTE:

                self.env.process(
                    self.wake_at(boundary)
                )

    def wake_at(self, target_time):

        yield self.env.timeout(
            target_time - self.env.now
        )

        self.notify()

    # --------------------------------------------------------
    # PATIENTEN BEGÄR EN BEHANDLINGSPLATS
    # --------------------------------------------------------

    def request(self, patient_id):

        if self.env.now >= CLOSING_MINUTE:
            return None

        job = {

            "patient_id": patient_id,

            "request_time": self.env.now,

            "event": self.env.event()
        }

        self.queue.append(job)

        stats["station_max_queue"][self.station] = max(
            stats["station_max_queue"][self.station],
            len(self.queue)
        )

        self.notify()

        assignment = yield job["event"]

        return assignment

    # --------------------------------------------------------
    # CENTRAL KÖHANTERARE
    # --------------------------------------------------------

    def dispatcher(self):

        while True:

            # Stängning: inga nya behandlingar får starta.
            if self.env.now >= CLOSING_MINUTE:

                while self.queue:

                    job = self.queue.pop(0)

                    if not job["event"].triggered:
                        job["event"].succeed(None)

                yield self.changed

                continue

            assignment_made = False

            while self.queue:

                available_staff = [

                    employee
                    for employee in self.staff

                    if self.staff_is_available(employee)
                ]

                available_rooms = [

                    room
                    for room in self.rooms

                    if room not in self.busy_rooms
                ]

                if not available_staff or not available_rooms:
                    break

                # FIFO: äldsta patientförfrågan först.
                job = self.queue.pop(0)

                employee = available_staff[0]

                room = available_rooms[0]

                # Reservera personal och rum samtidigt.
                self.busy_staff.add(employee["name"])

                self.busy_rooms.add(room)

                waiting_time = (
                    self.env.now - job["request_time"]
                )

                assignment = {

                    "staff": employee,

                    "room": room,

                    "waiting_time": waiting_time
                }

                if not job["event"].triggered:
                    job["event"].succeed(assignment)

                assignment_made = True

            if not assignment_made:

                # Vänta tills någon resurs förändras,
                # en lunch börjar/slutar eller kliniken stänger.
                yield self.changed

    # --------------------------------------------------------
    # FRIGÖR RESURSER
    # --------------------------------------------------------

    def release(self, employee, room):

        self.busy_staff.remove(employee["name"])

        self.busy_rooms.remove(room)

        self.notify()


# ============================================================
# 6. SKAPA RESURSER OCH KONVERTERA TIDER
# ============================================================

def create_resources(env):

    managers = {}

    for station, staff_config in CONFIG["staff_schedules"].items():

        staff = []

        for employee in staff_config:

            # Kopiera originalet så att CONFIG inte ändras.
            employee_copy = employee.copy()

            # VIKTIG KORRIGERING:
            # Konvertera från minuter efter öppning
            # till minuter efter midnatt.
            employee_copy["start"] += OPENING_MINUTE

            employee_copy["end"] += OPENING_MINUTE

            employee_copy["lunch_start"] += OPENING_MINUTE

            staff.append(employee_copy)

        room_count = CONFIG["rooms"][station]

        managers[station] = StationManager(

            env=env,

            station=station,

            staff=staff,

            room_count=room_count
        )

    return managers


# ============================================================
# 7. SKAPA BOKNINGSSCHEMA
# ============================================================

def create_schedule():

    schedule = []

    patient_id = 1

    for patient_type, settings in (
        CONFIG["appointment_schedules"].items()
    ):

        interval = settings["interval"]

        first_appointment = settings["first"]

        if interval <= 0:
            raise ValueError(
                f"Ogiltigt bokningsintervall: {patient_type}"
            )

        if patient_type not in PATIENT_ROUTES:
            raise ValueError(
                f"Okänd patienttyp: {patient_type}"
            )

        appointment_time = first_appointment

        while appointment_time < OPENING_DURATION:

            arrival_delay = random.gauss(
                0,
                CONFIG["arrival_std"]
            )

            no_show = (
                random.random()
                < CONFIG["no_show_probability"]
            )

            schedule.append({

                "patient_id": patient_id,

                "appointment_time": (
                    OPENING_MINUTE + appointment_time
                ),

                "patient_type": patient_type,

                "arrival_delay": arrival_delay,

                "no_show": no_show
            })

            patient_id += 1

            appointment_time += interval

    schedule.sort(
        key=lambda patient: patient["appointment_time"]
    )

    return schedule


# ============================================================
# 8. GENOMFÖR BEHANDLING
# ============================================================

def treatment(env, patient_id, station, managers):

    manager = managers[station]

    queue_start = env.now

    assignment = yield from manager.request(patient_id)

    # Patienten fick ingen behandlingsplats före stängning.
    if assignment is None:

        waiting_time = env.now - queue_start

        stats["station_waits"][station].append(
            waiting_time
        )

        stats["station_not_started"][station] += 1

        return False

    employee = assignment["staff"]

    room = assignment["room"]

    waiting_time = assignment["waiting_time"]

    stats["station_waits"][station].append(
        waiting_time
    )

    treatment_start = env.now

    average_time = CONFIG["service_times"][station]

    variation = CONFIG["service_time_variation"]

    service_time = random.triangular(

        average_time * (1 - variation),

        average_time * (1 + variation),

        average_time
    )

    treatment_end = treatment_start + service_time

    # Mät behandlingstid inom klinikens öppettid.
    busy_open = interval_overlap(

        treatment_start,

        treatment_end,

        OPENING_MINUTE,

        CLOSING_MINUTE
    )

    # Personalens beläggning.
    stats["staff_busy_open"][employee["name"]] += busy_open

    stats["staff_busy_total"][employee["name"]] += service_time

    # Rummets beläggning.
    stats["room_busy_open"][room] += busy_open

    stats["room_busy_total"][room] += service_time

    stats["station_treatments"][station] += 1

    # Behandlingen får slutföras efter stängning.
    yield env.timeout(service_time)

    manager.release(employee, room)

    return True


# ============================================================
# 9. PATIENTENS VÅRDFÖRLOPP
# ============================================================

def patient_process(env, patient, managers):

    patient_id = patient["patient_id"]

    patient_type = patient["patient_type"]

    appointment_time = patient["appointment_time"]

    stats["patients_booked"] += 1

    stats["patient_types"][patient_type] += 1

    actual_arrival = max(

        OPENING_MINUTE,

        appointment_time + patient["arrival_delay"]
    )

    # Vänta till faktisk ankomst.
    yield env.timeout(
        max(0, actual_arrival - env.now)
    )

    if patient["no_show"]:

        stats["patients_no_show"] += 1

        return

    stats["patients_arrived"] += 1

    # Ingen patient får påbörja vård efter stängning.
    if env.now >= CLOSING_MINUTE:

        stats["patients_after_closing"] += 1

        stats["patients_incomplete"] += 1

        return

    patient_start = env.now

    route = PATIENT_ROUTES[patient_type]

    for station in route:

        completed = yield from treatment(

            env,

            patient_id,

            station,

            managers
        )

        if not completed:

            stats["patients_incomplete"] += 1

            return

    stats["patients_completed"] += 1

    total_time = env.now - patient_start

    stats["patient_total_times"].append(total_time)


# ============================================================
# 10. KÖR EN KLINIKDAG
# ============================================================

def run_clinic_day():

    global stats

    random.seed(CONFIG["random_seed"])

    stats = create_stats()

    env = simpy.Environment()

    managers = create_resources(env)

    schedule = create_schedule()

    for patient in schedule:

        env.process(
            patient_process(
                env,
                patient,
                managers
            )
        )

    # Alla patienter som har påbörjat behandling
    # får avsluta sina processer.
    env.run()

    return managers


# ============================================================
# 11. BERÄKNA PERSONALENS TILLGÄNGLIGA ARBETSTID
# ============================================================

def calculate_staff_availability(employee):

    shift_minutes = interval_overlap(

        employee["start"],

        employee["end"],

        OPENING_MINUTE,

        CLOSING_MINUTE
    )

    lunch_end = (
        employee["lunch_start"]
        + employee["lunch_duration"]
    )

    lunch_minutes = interval_overlap(

        employee["lunch_start"],

        lunch_end,

        OPENING_MINUTE,

        CLOSING_MINUTE
    )

    return max(0, shift_minutes - lunch_minutes)


# ============================================================
# 12. KAPACITETSRAPPORT
# ============================================================

def print_capacity_report(managers):

    print("\n")
    print("=" * 70)
    print("             CLINICFLOW – KAPACITETSRAPPORT")
    print("=" * 70)

    print(
        f"\nÖppettider: "
        f"{format_time(OPENING_MINUTE)}–"
        f"{format_time(CLOSING_MINUTE)}"
    )

    print(
        f"Öppettid: {OPENING_DURATION} minuter"
    )

    # --------------------------------------------------------
    # PATIENTFLÖDE
    # --------------------------------------------------------

    print("\n" + "-" * 70)
    print("1. PATIENTFLÖDE")
    print("-" * 70)

    print(f"Bokade patienter: {stats['patients_booked']}")

    print(f"Anlända patienter: {stats['patients_arrived']}")

    print(f"Uteblivna patienter: {stats['patients_no_show']}")

    print(
        f"Patienter efter stängning: "
        f"{stats['patients_after_closing']}"
    )

    print(
        f"Genomförda vårdförlopp: "
        f"{stats['patients_completed']}"
    )

    print(
        f"Ofullständiga vårdförlopp: "
        f"{stats['patients_incomplete']}"
    )

    print("\nPatienttyper:")

    for patient_type, count in stats["patient_types"].items():

        print(f"  {patient_type:20s}: {count}")

    # --------------------------------------------------------
    # STATIONERNAS KAPACITET
    # --------------------------------------------------------

    print("\n" + "-" * 70)
    print("2. STATIONERNAS RESURSUTNYTTJANDE")
    print("-" * 70)

    for station, manager in managers.items():

        staff_available = sum(

            calculate_staff_availability(employee)

            for employee in manager.staff
        )

        staff_busy = sum(

            stats["staff_busy_open"][employee["name"]]

            for employee in manager.staff
        )

        room_available = (
            len(manager.rooms) * OPENING_DURATION
        )

        room_busy = sum(

            stats["room_busy_open"][room]

            for room in manager.rooms
        )

        staff_utilization = (

            staff_busy / staff_available * 100

            if staff_available > 0 else 0
        )

        room_utilization = (

            room_busy / room_available * 100

            if room_available > 0 else 0
        )

        waits = stats["station_waits"][station]

        average_wait = (

            statistics.mean(waits)

            if waits else 0
        )

        maximum_wait = (

            max(waits)

            if waits else 0
        )

        # Teoretisk kapacitet baserat på tillgänglig
        # personal och rum.
        average_service_time = CONFIG["service_times"][station]

        staff_capacity = (
            staff_available / average_service_time
        )

        room_capacity = (
            room_available / average_service_time
        )

        theoretical_capacity = min(
            staff_capacity,
            room_capacity
        )

        print(f"\n{station.upper()}")

        print(
            f"  Personal: {len(manager.staff)}"
        )

        print(
            f"  Rum: {len(manager.rooms)}"
        )

        print(
            f"  Teoretisk kapacitet: "
            f"{theoretical_capacity:.1f} behandlingar"
        )

        print(
            f"  Genomförda behandlingar: "
            f"{stats['station_treatments'][station]}"
        )

        print(
            f"  Personalbeläggning: "
            f"{staff_utilization:.1f}%"
        )

        print(
            f"  Rumsbeläggning: "
            f"{room_utilization:.1f}%"
        )

        print(
            f"  Genomsnittlig väntetid: "
            f"{average_wait:.1f} minuter"
        )

        print(
            f"  Längsta väntetid: "
            f"{maximum_wait:.1f} minuter"
        )

        print(
            f"  Största observerade kö: "
            f"{stats['station_max_queue'][station]}"
        )

        print(
            f"  Behandlingar som inte hann starta: "
            f"{stats['station_not_started'][station]}"
        )

    # --------------------------------------------------------
    # INDIVIDUELL PERSONALBELÄGGNING
    # --------------------------------------------------------

    print("\n" + "-" * 70)
    print("3. BELÄGGNING PER ANSTÄLLD")
    print("-" * 70)

    for station, employees in CONFIG["staff_schedules"].items():

        print(f"\n{station.upper()}")

        for original_employee in employees:

            name = original_employee["name"]

            # Tillgänglighet räknas i minuter efter öppning.
            # Här behöver vi inte konvertera eftersom
            # öppettiden också räknas i samma referens.
            employee = original_employee.copy()

            employee["start"] += OPENING_MINUTE
            employee["end"] += OPENING_MINUTE
            employee["lunch_start"] += OPENING_MINUTE

            available = calculate_staff_availability(
                employee
            )

            busy = stats["staff_busy_open"][name]

            utilization = (

                busy / available * 100

                if available > 0 else 0
            )

            print(
                f"  {name:15s} "
                f"Beläggning: {utilization:5.1f}% "
                f"Tillgänglig: {available:5.0f} min "
                f"Behandling: {busy:5.1f} min"
            )

    # --------------------------------------------------------
    # INDIVIDUELL RUMSBELÄGGNING
    # --------------------------------------------------------

    print("\n" + "-" * 70)
    print("4. BELÄGGNING PER BEHANDLINGSRUM")
    print("-" * 70)

    for station, manager in managers.items():

        print(f"\n{station.upper()}")

        for room in manager.rooms:

            busy = stats["room_busy_open"][room]

            available = OPENING_DURATION

            utilization = (

                busy / available * 100

                if available > 0 else 0
            )

            print(
                f"  {room:20s} "
                f"Beläggning: {utilization:5.1f}% "
                f"Behandling: {busy:5.1f} min"
            )

    # --------------------------------------------------------
    # PATIENTUPPLEVELSE
    # --------------------------------------------------------

    print("\n" + "-" * 70)
    print("5. PATIENTUPPLEVELSE")
    print("-" * 70)

    all_waits = []

    for station_waits in stats["station_waits"].values():

        all_waits.extend(station_waits)

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

    print("\n" + "-" * 70)
    print("6. BEHANDLINGSTID EFTER STÄNGNING")
    print("-" * 70)

    for station, manager in managers.items():

        staff_overtime = sum(

            stats["staff_busy_total"][employee["name"]]
            - stats["staff_busy_open"][employee["name"]]

            for employee in manager.staff
        )

        room_overtime = sum(

            stats["room_busy_total"][room]
            - stats["room_busy_open"][room]

            for room in manager.rooms
        )

        print(
            f"{station.upper():10s} "
            f"Personal: {staff_overtime:.1f} min "
            f"Rum: {room_overtime:.1f} min"
        )

    print("\n" + "=" * 70)
    print("                 SIMULERING AVSLUTAD")
    print("=" * 70)


# ============================================================
# 13. STARTA CLINICFLOW
# ============================================================

if __name__ == "__main__":

    managers = run_clinic_day()

    print_capacity_report(managers)

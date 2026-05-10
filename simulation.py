import random
from dataclasses import dataclass, field
from typing import List, Optional

DRIVERS_DATA = [
    ("Max Verstappen",    "Red Bull",    "#3671C6", 89.20),
    ("Sergio Perez",      "Red Bull",    "#3671C6", 89.68),
    ("Lewis Hamilton",    "Ferrari",     "#E8002D", 89.38),
    ("Charles Leclerc",   "Ferrari",     "#E8002D", 89.42),
    ("Lando Norris",      "McLaren",     "#FF8000", 89.45),
    ("Oscar Piastri",     "McLaren",     "#FF8000", 89.52),
    ("George Russell",    "Mercedes",    "#27F4D2", 89.60),
    ("Kimi Antonelli",    "Mercedes",    "#27F4D2", 89.75),
    ("Carlos Sainz",      "Williams",    "#64C4FF", 89.70),
    ("Alex Albon",        "Williams",    "#64C4FF", 89.90),
    ("Fernando Alonso",   "Aston Martin","#229971", 89.80),
    ("Lance Stroll",      "Aston Martin","#229971", 90.05),
    ("Nico Hulkenberg",   "Sauber",      "#52E252", 90.10),
    ("Gabriel Bortoleto", "Sauber",      "#52E252", 90.20),
    ("Pierre Gasly",      "Alpine",      "#FF87BC", 90.00),
    ("Jack Doohan",       "Alpine",      "#FF87BC", 90.15),
    ("Yuki Tsunoda",      "RB",          "#6692FF", 89.95),
    ("Isack Hadjar",      "RB",          "#6692FF", 90.08),
    ("Oliver Bearman",    "Haas",        "#B6BABD", 90.12),
    ("Esteban Ocon",      "Haas",        "#B6BABD", 90.18),
]

TIRE_DATA = {
    "Soft":   {"symbol": "S", "pace_delta": 0.0,  "deg_per_lap": 0.048, "cliff_start": 20},
    "Medium": {"symbol": "M", "pace_delta": 0.42, "deg_per_lap": 0.026, "cliff_start": 32},
    "Hard":   {"symbol": "H", "pace_delta": 0.85, "deg_per_lap": 0.013, "cliff_start": 50},
}

PIT_LOSS = 21.5


@dataclass
class Tire:
    compound: str
    age: int = 0

    @property
    def deg(self) -> float:
        d = TIRE_DATA[self.compound]
        base = d["deg_per_lap"] * self.age
        cliff = max(0, self.age - d["cliff_start"]) * d["deg_per_lap"] * 1.8
        return base + cliff

    @property
    def pace_penalty(self) -> float:
        return TIRE_DATA[self.compound]["pace_delta"] + self.deg

    @property
    def wear_pct(self) -> int:
        cliff = TIRE_DATA[self.compound]["cliff_start"]
        return min(100, int((self.age / (cliff * 1.4)) * 100))

    @property
    def symbol(self) -> str:
        return TIRE_DATA[self.compound]["symbol"]


@dataclass
class LapData:
    lap: int
    lap_time: float
    s1: float
    s2: float
    s3: float
    position: int
    gap_ahead: Optional[float]
    gap_behind: Optional[float]
    tire_compound: str
    tire_age: int
    is_pit_lap: bool = False
    has_mistake: bool = False
    safety_car: bool = False
    gap_to_leader: Optional[float] = None


@dataclass
class Driver:
    name: str
    team: str
    color: str
    base_pace: float
    grid_pos: int
    is_player: bool = False

    position: int = 0
    total_time: float = 0.0
    tire: Tire = field(default_factory=lambda: Tire("Medium"))
    lap_history: List[LapData] = field(default_factory=list)
    pit_laps: List[int] = field(default_factory=list)
    pitted_laps: List[int] = field(default_factory=list)

    def __post_init__(self):
        self.position = self.grid_pos

    @property
    def last_lap(self) -> Optional[LapData]:
        return self.lap_history[-1] if self.lap_history else None

    @property
    def best_lap(self) -> Optional[float]:
        clean = [l.lap_time for l in self.lap_history if not l.is_pit_lap and not l.safety_car]
        return min(clean) if clean else None

    def simulate_lap(
        self,
        lap_num: int,
        safety_car: bool = False,
        push_level: int = 1,   # 0=Koruma 1=Normal 2=Push
        tactic: str = "Normal", # "Normal" | "Atak" | "Savun"
    ) -> LapData:
        self.tire.age += 1
        is_pit = lap_num in self.pit_laps

        # Push modu etkileri
        PUSH_PACE    = {0: +0.35, 1: 0.0, 2: -0.40}
        PUSH_DEG     = {0: 0.65,  1: 1.0, 2: 1.55}
        PUSH_MISTAKE = {0: 0.60,  1: 1.0, 2: 1.30}

        # Tactic effects
        TACTIC_PACE    = {"Normal": 0.0,  "Push": -0.15, "Defend": +0.08}
        TACTIC_DEG     = {"Normal": 1.0,  "Push": 1.20,  "Defend": 0.90}
        TACTIC_MISTAKE = {"Normal": 1.0,  "Push": 1.20,  "Defend": 0.70}

        pace_bonus  = PUSH_PACE[push_level]    + (TACTIC_PACE[tactic]    if self.is_player else 0.0)
        deg_mult    = PUSH_DEG[push_level]     * (TACTIC_DEG[tactic]     if self.is_player else 1.0)
        err_mult    = PUSH_MISTAKE[push_level] * (TACTIC_MISTAKE[tactic] if self.is_player else 1.0)

        # Lastik yıpranmasını bu turda anlık uygula (deg_mult)
        extra_tire_stress = (deg_mult - 1.0) * TIRE_DATA[self.tire.compound]["deg_per_lap"]

        # Yakıt/track evolution: her tur arabalar hafifçe hızlanır (max 0.7s)
        fuel_saving = min((lap_num - 1) * 0.013, 0.70)

        base = self.base_pace + self.tire.pace_penalty + extra_tire_stress * 15 - fuel_saving
        if self.is_player:
            base += pace_bonus

        noise = random.gauss(0, 0.14)

        mistake = 0.0
        has_mistake = False
        base_err = 0.045 * (err_mult if self.is_player else 1.0)
        if not safety_car and random.random() < base_err:
            mistake = random.uniform(0.5, 2.8)
            has_mistake = True

        sc_penalty = random.uniform(28, 38) if safety_car else 0.0
        lap_time = base + noise + mistake + sc_penalty

        s1_frac = random.gauss(0.28, 0.004)
        s2_frac = random.gauss(0.42, 0.004)
        s1 = round(lap_time * s1_frac, 3)
        s2 = round(lap_time * s2_frac, 3)
        s3 = round(lap_time - s1 - s2, 3)

        self.total_time += lap_time

        # Scale real tire aging by deg_mult for player only
        if self.is_player and deg_mult != 1.0:
            self.tire.age += int(deg_mult - 1.0)

        if is_pit:
            self.total_time += PIT_LOSS + random.gauss(0, 0.35)
            pit_number = len(self.pitted_laps)  # 0 = first pit, 1 = second pit
            self.pitted_laps.append(lap_num)
            if pit_number == 0:
                next_compound = {"Soft": "Medium", "Medium": "Hard", "Hard": "Medium"}
            else:
                # Second pit: fresh Soft for FL attack
                next_compound = {"Soft": "Soft", "Medium": "Soft", "Hard": "Soft"}
            self.tire = Tire(next_compound[self.tire.compound])

        data = LapData(
            lap=lap_num,
            lap_time=lap_time,
            s1=s1, s2=s2, s3=s3,
            position=self.position,
            gap_ahead=None,
            gap_behind=None,
            tire_compound=self.tire.compound,
            tire_age=self.tire.age,
            is_pit_lap=is_pit,
            has_mistake=has_mistake,
            safety_car=safety_car,
        )
        self.lap_history.append(data)
        return data


@dataclass
class Race:
    drivers: List[Driver]
    total_laps: int
    current_lap: int = 0
    safety_car_laps: List[int] = field(default_factory=list)
    player_idx: int = 0
    finished: bool = False
    sc_reason: str = ""

    @property
    def player(self) -> Driver:
        return self.drivers[self.player_idx]

    @property
    def standings(self) -> List[Driver]:
        if self.current_lap == 0:
            return sorted(self.drivers, key=lambda d: d.grid_pos)
        return sorted(self.drivers, key=lambda d: d.total_time)

    def simulate_lap(self, push_level: int = 1, tactic: str = "Normal"):
        if self.finished:
            return
        self.current_lap += 1
        sc = self.current_lap in self.safety_car_laps

        for driver in self.drivers:
            if driver.is_player:
                driver.simulate_lap(self.current_lap, sc, push_level=push_level, tactic=tactic)
            else:
                driver.simulate_lap(self.current_lap, sc)

        # Lap 1: traffic/clean-air penalty by grid position
        # P1 grid = 0s, each position adds +0.28s (P20 = +5.32s)
        if self.current_lap == 1:
            for driver in self.drivers:
                driver.total_time += (driver.grid_pos - 1) * 0.28

        ordered = self.standings
        leader_time = ordered[0].total_time
        for pos, driver in enumerate(ordered, 1):
            driver.position = pos
            if driver.last_lap:
                driver.last_lap.position = pos
                driver.last_lap.gap_ahead = (
                    None if pos == 1
                    else round(driver.total_time - ordered[pos - 2].total_time, 3)
                )
                driver.last_lap.gap_behind = (
                    None if pos == len(ordered)
                    else round(ordered[pos].total_time - driver.total_time, 3)
                )
                driver.last_lap.gap_to_leader = (
                    0.0 if pos == 1
                    else round(driver.total_time - leader_time, 3)
                )

        if self.current_lap >= self.total_laps:
            self.finished = True


DRIVER_NAMES = [d[0] for d in DRIVERS_DATA]


def create_race(
    total_laps: int = 57,
    player_driver_name: Optional[str] = None,
    player_grid_pos: Optional[int] = None,
) -> Race:
    pool = DRIVERS_DATA.copy()

    # Pick player driver
    if player_driver_name and player_driver_name in DRIVER_NAMES:
        chosen = next(d for d in pool if d[0] == player_driver_name)
    else:
        chosen = random.choice(pool)

    pool = [d for d in pool if d[0] != chosen[0]]
    random.shuffle(pool)

    p_name, p_team, p_color_orig, p_pace = chosen
    player_grid = player_grid_pos if player_grid_pos else random.randint(1, 20)

    # Remaining 19 drivers get random grid slots
    other_grids = [g for g in range(1, 21) if g != player_grid]
    random.shuffle(other_grids)

    if total_laps >= 45:
        pit_lap_fn = lambda: random.randint(int(total_laps * 0.33), int(total_laps * 0.52))
    else:
        pit_lap_fn = lambda: random.randint(int(total_laps * 0.38), int(total_laps * 0.55))

    drivers = []

    # Player driver
    start_tire = random.choices(["Soft", "Medium"], weights=[65, 35])[0]
    drivers.append(Driver(
        name=p_name,
        team=p_team,
        color="#00FF41",
        base_pace=p_pace + random.gauss(0, 0.06),
        grid_pos=player_grid,
        is_player=True,
        tire=Tire(start_tire),
        pit_laps=[pit_lap_fn()],
    ))

    # Other 19 drivers
    for i, (name, team, color, pace) in enumerate(pool):
        start_tire = random.choices(["Soft", "Medium"], weights=[65, 35])[0]
        first_pit = pit_lap_fn()
        npc_pits = [first_pit]
        # 35% chance of a second pit (FL attack on fresh Soft in the final laps)
        if total_laps >= 40 and random.random() < 0.35:
            second_pit = random.randint(int(total_laps * 0.78), total_laps - 3)
            if second_pit > first_pit + 5:
                npc_pits.append(second_pit)
        drivers.append(Driver(
            name=name,
            team=team,
            color=color,
            base_pace=pace + random.gauss(0, 0.06),
            grid_pos=other_grids[i],
            is_player=False,
            tire=Tire(start_tire),
            pit_laps=npc_pits,
        ))

    sc_laps: List[int] = []
    sc_reason: str = ""
    if total_laps >= 22 and random.random() < 0.55:
        sc_start = random.randint(8, total_laps - 12)
        sc_laps = [sc_start, sc_start + 1]
        victim = random.choice([d for d in drivers if not d.is_player])
        incident = random.choice([
            "heavy crash", "went off track", "hit the wall",
            "mechanical failure", "multi-car incident",
        ])
        sc_reason = f"{victim.name} — {incident} (Lap {sc_start})"

    return Race(
        drivers=drivers,
        total_laps=total_laps,
        safety_car_laps=sc_laps,
        sc_reason=sc_reason,
        player_idx=next(i for i, d in enumerate(drivers) if d.is_player),
    )

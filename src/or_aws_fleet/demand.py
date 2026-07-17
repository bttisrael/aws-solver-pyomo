from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date


DC_LATITUDE = -23.5505
DC_LONGITUDE = -46.6333

CITY_PROFILES = [
    ("Sao Paulo", "SP", -23.5505, -46.6333, 0.28),
    ("Guarulhos", "SP", -23.4543, -46.5337, 0.12),
    ("Campinas", "SP", -22.9056, -47.0608, 0.10),
    ("Santos", "SP", -23.9608, -46.3336, 0.08),
    ("Sorocaba", "SP", -23.5015, -47.4526, 0.08),
    ("Sao Jose dos Campos", "SP", -23.2237, -45.9009, 0.08),
    ("Ribeirao Preto", "SP", -21.1775, -47.8103, 0.07),
    ("Bauru", "SP", -22.3145, -49.0587, 0.06),
    ("Rio de Janeiro", "RJ", -22.9068, -43.1729, 0.07),
    ("Belo Horizonte", "MG", -19.9167, -43.9345, 0.06),
]


@dataclass(frozen=True)
class DemandPoint:
    customer_id: str
    city: str
    state: str
    latitude: float
    longitude: float
    demand_units: int
    service_minutes: int
    distance_km: float


def _seed_for_date(run_date: date, random_seed: int | None = None) -> int:
    if random_seed is not None:
        return random_seed
    return int(run_date.strftime("%Y%m%d"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def generate_daily_demand(
    run_date: date,
    demand_points: int = 120,
    random_seed: int | None = None,
) -> list[DemandPoint]:
    rng = random.Random(_seed_for_date(run_date, random_seed))
    weights = [profile[4] for profile in CITY_PROFILES]
    cities = rng.choices(CITY_PROFILES, weights=weights, k=demand_points)

    demand: list[DemandPoint] = []
    for idx, (city, state, base_lat, base_lon, _) in enumerate(cities, start=1):
        lat = base_lat + rng.uniform(-0.09, 0.09)
        lon = base_lon + rng.uniform(-0.09, 0.09)
        distance_km = haversine_km(DC_LATITUDE, DC_LONGITUDE, lat, lon)

        # Log-normal demand creates realistic heavy-tail delivery days.
        raw_units = rng.lognormvariate(mu=3.15, sigma=0.55)
        demand_units = max(1, min(95, int(round(raw_units))))
        service_minutes = 8 + int(round(demand_units * 0.45)) + rng.randint(0, 8)

        demand.append(
            DemandPoint(
                customer_id=f"C{run_date:%Y%m%d}-{idx:04d}",
                city=city,
                state=state,
                latitude=round(lat, 6),
                longitude=round(lon, 6),
                demand_units=demand_units,
                service_minutes=service_minutes,
                distance_km=round(distance_km, 2),
            )
        )

    return demand

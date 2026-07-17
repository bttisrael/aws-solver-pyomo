"""Create and populate logistics.route with the synthetic beverage network."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from upload_sheets_to_dsql import aws_region, connect_dsql, load_dotenv, resolve_dsql_endpoint


LOCATIONS = {
    "FAC_01_SERRA_NORTE": (-5.3686, -49.1178),
    "FAC_02_VALE_LESTE": (-19.4683, -42.5367),
    "FAC_03_PLANALTO_CENTRAL": (-15.7939, -47.8828),
    "FAC_04_COSTA_SUL": (-27.5954, -48.5480),
    "FAC_05_RIO_OESTE": (-22.9068, -43.1729),
    "DC_01_NORTE": (-3.1190, -60.0217),
    "DC_02_NORDESTE": (-8.0476, -34.8770),
    "DC_03_CENTRO_OESTE": (-15.6014, -56.0979),
    "DC_04_SUDESTE_1": (-23.5505, -46.6333),
    "DC_05_SUDESTE_2": (-19.9167, -43.9345),
    "DC_06_SUL_1": (-25.4284, -49.2733),
    "DC_07_SUL_2": (-30.0346, -51.2177),
    "DC_08_INTERIOR_1": (-22.9056, -47.0608),
    "DC_09_INTERIOR_2": (-21.1704, -47.8103),
}

ORIGINS = [name for name in LOCATIONS if name.startswith("FAC_")]
DESTINATIONS = [name for name in LOCATIONS if name.startswith("DC_")]


def haversine_km(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat_a, lon_a = map(radians, point_a)
    lat_b, lon_b = map(radians, point_b)
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a
    value = sin(dlat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))


def route_rows() -> list[tuple[object, ...]]:
    rows = []
    for origin in ORIGINS:
        for destiny in DESTINATIONS:
            origin_coordinates = LOCATIONS[origin]
            destiny_coordinates = LOCATIONS[destiny]
            # Synthetic road-distance estimate: great-circle distance plus 20%.
            distance_km = round(haversine_km(origin_coordinates, destiny_coordinates) * 1.20, 2)
            rows.append((origin, destiny, *origin_coordinates, *destiny_coordinates, distance_km))
    return rows


def main() -> None:
    load_dotenv()
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)
    rows = route_rows()

    with connect_dsql(endpoint, region) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS logistics.route (
                    origin VARCHAR(40) NOT NULL,
                    destiny VARCHAR(40) NOT NULL,
                    origin_latitude NUMERIC(9, 6) NOT NULL,
                    origin_longitude NUMERIC(9, 6) NOT NULL,
                    destiny_latitude NUMERIC(9, 6) NOT NULL,
                    destiny_longitude NUMERIC(9, 6) NOT NULL,
                    distance_km NUMERIC(10, 2) NOT NULL,
                    google_driving_distance_km NUMERIC(10, 2),
                    PRIMARY KEY (origin, destiny)
                )
                """
            )
            cur.execute("DELETE FROM logistics.route")
            cur.executemany(
                """
                INSERT INTO logistics.route (
                    origin, destiny, origin_latitude, origin_longitude,
                    destiny_latitude, destiny_longitude, distance_km
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
            cur.execute("SELECT COUNT(*), MIN(distance_km), MAX(distance_km) FROM logistics.route")
            count, minimum, maximum = cur.fetchone()

    if count != len(rows):
        raise RuntimeError(f"Expected {len(rows)} routes, found {count}")
    print(f"route table updated: rows={count}, min_km={minimum}, max_km={maximum}")


if __name__ == "__main__":
    main()

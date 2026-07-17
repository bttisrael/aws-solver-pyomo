"""Populate Google driving distances for logistics.route using Routes API."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

from upload_sheets_to_dsql import aws_region, connect_dsql, load_dotenv, resolve_dsql_endpoint


ROUTES_API_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"


def waypoint(latitude: float, longitude: float) -> dict[str, object]:
    return {
        "waypoint": {
            "location": {"latLng": {"latitude": latitude, "longitude": longitude}}
        }
    }


def fetch_routes(
    api_key: str,
    origins: list[tuple[str, float, float]],
    destinations: list[tuple[str, float, float]],
) -> list[dict[str, object]]:
    payload = {
        "origins": [waypoint(latitude, longitude) for _, latitude, longitude in origins],
        "destinations": [waypoint(latitude, longitude) for _, latitude, longitude in destinations],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
    }
    request = urllib.request.Request(
        ROUTES_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,status,condition",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Routes API returned HTTP {exc.code}: {details}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key and not args.schema_only:
        raise RuntimeError("Set GOOGLE_MAPS_API_KEY in .env; never commit the key.")

    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)
    with connect_dsql(endpoint, region) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE logistics.route
                ADD COLUMN IF NOT EXISTS google_driving_distance_km NUMERIC(10, 2)
                """
            )
            if args.schema_only:
                print("google_driving_distance_km column is ready")
                return
            cur.execute(
                """
                SELECT DISTINCT origin, origin_latitude, origin_longitude
                FROM logistics.route ORDER BY origin
                """
            )
            origins = [(row[0], float(row[1]), float(row[2])) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT DISTINCT destiny, destiny_latitude, destiny_longitude
                FROM logistics.route ORDER BY destiny
                """
            )
            destinations = [(row[0], float(row[1]), float(row[2])) for row in cur.fetchall()]

            elements = fetch_routes(api_key, origins, destinations)
            updates = []
            for element in elements:
                if element.get("condition") != "ROUTE_EXISTS" or "distanceMeters" not in element:
                    continue
                origin = origins[int(element["originIndex"])][0]
                destiny = destinations[int(element["destinationIndex"])][0]
                distance_km = Decimal(int(element["distanceMeters"])) / Decimal(1000)
                updates.append((distance_km.quantize(Decimal("0.01")), origin, destiny))

            if len(updates) != len(origins) * len(destinations):
                raise RuntimeError(
                    f"Google returned {len(updates)} valid routes; expected {len(origins) * len(destinations)}."
                )
            cur.executemany(
                """
                UPDATE logistics.route
                SET google_driving_distance_km = %s
                WHERE origin = %s AND destiny = %s
                """,
                updates,
            )
            cur.execute(
                """
                SELECT COUNT(*), MIN(google_driving_distance_km), MAX(google_driving_distance_km)
                FROM logistics.route WHERE google_driving_distance_km IS NOT NULL
                """
            )
            count, minimum, maximum = cur.fetchone()

    print(f"Google driving distances updated: rows={count}, min_km={minimum}, max_km={maximum}")


if __name__ == "__main__":
    main()

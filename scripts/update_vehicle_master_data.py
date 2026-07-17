"""Replace the logistics vehicle master with the approved seven-vehicle fleet."""

from decimal import Decimal

from upload_sheets_to_dsql import aws_region, connect_dsql, load_dotenv, resolve_dsql_endpoint


VEHICLES = [
    ("Cargo van", Decimal("8.00"), 1200, Decimal("4.20"), 5),
    ("Hatchback / sedan car", Decimal("0.80"), 250, Decimal("1.80"), 1),
    ("Light cargo van (Fiorino/Kangoo)", Decimal("3.20"), 650, Decimal("2.80"), 2),
    ("Medium box truck", Decimal("45.00"), 10000, Decimal("12.00"), 30),
    ("Semi-trailer / tractor-trailer", Decimal("90.00"), 25000, Decimal("18.00"), 60),
    ("Single-unit box truck", Decimal("32.00"), 6000, Decimal("9.00"), 21),
    ("Urban delivery truck (3/4 ton)", Decimal("18.00"), 3000, Decimal("6.50"), 12),
]


def main() -> None:
    load_dotenv()
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)

    with connect_dsql(endpoint, region) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE logistics.vehicle_master_data
                ADD COLUMN IF NOT EXISTS vehicle_capacity_pallets INTEGER
                """
            )
            cur.execute("DELETE FROM logistics.vehicle_master_data")
            cur.executemany(
                """
                INSERT INTO logistics.vehicle_master_data (
                    vehicle_type,
                    vehicle_capacity_m3,
                    vehicle_capacity_kg,
                    freight_cost_per_km,
                    vehicle_capacity_pallets
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                VEHICLES,
            )
            cur.execute(
                """
                SELECT vehicle_type, vehicle_capacity_m3, vehicle_capacity_kg,
                       freight_cost_per_km, vehicle_capacity_pallets
                FROM logistics.vehicle_master_data
                ORDER BY vehicle_type
                """
            )
            rows = cur.fetchall()

    if len(rows) != len(VEHICLES):
        raise RuntimeError(f"Expected {len(VEHICLES)} vehicles, found {len(rows)}")

    print("vehicle master data updated and verified")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()

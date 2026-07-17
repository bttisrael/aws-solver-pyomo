"""Add and backfill Google driving distance on logistics.daily_programming."""

from upload_sheets_to_dsql import aws_region, connect_dsql, load_dotenv, resolve_dsql_endpoint


def main() -> None:
    load_dotenv()
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)

    with connect_dsql(endpoint, region) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE logistics.daily_programming
                ADD COLUMN IF NOT EXISTS google_driving_distance_km NUMERIC(10, 2)
                """
            )
            cur.execute("SELECT DISTINCT date FROM logistics.daily_programming ORDER BY date")
            programming_dates = [row[0] for row in cur.fetchall()]
            updated = 0
            for programming_date in programming_dates:
                cur.execute(
                    """
                    UPDATE logistics.daily_programming AS programming
                    SET google_driving_distance_km = route.google_driving_distance_km
                    FROM logistics.route AS route
                    WHERE route.origin = programming.origin
                      AND route.destiny = programming.destiny
                      AND programming.date = %s
                    """,
                    (programming_date,),
                )
                updated += cur.rowcount
            cur.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE google_driving_distance_km IS NULL),
                       MIN(google_driving_distance_km),
                       MAX(google_driving_distance_km)
                FROM logistics.daily_programming
                """
            )
            total, missing, minimum, maximum = cur.fetchone()

    if missing:
        raise RuntimeError(f"{missing} of {total} programming rows have no Google distance")
    print(
        "daily_programming distance backfill complete: "
        f"updated={updated}, rows={total}, min_km={minimum}, max_km={maximum}"
    )


if __name__ == "__main__":
    main()

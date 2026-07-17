"""Add and backfill cubic volume on logistics.daily_programming."""

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
                ADD COLUMN IF NOT EXISTS total_volume_m3 NUMERIC(16, 6)
                """
            )
            cur.execute("SELECT DISTINCT date FROM logistics.daily_programming ORDER BY date")
            programming_dates = [row[0] for row in cur.fetchall()]
            updated = 0
            for programming_date in programming_dates:
                cur.execute(
                    """
                    UPDATE logistics.daily_programming AS programming
                    SET total_volume_m3 = ROUND(programming.total_boxes * master.box_volume, 6)
                    FROM logistics.master_data AS master
                    WHERE master.cod_material = programming.cod_material
                      AND programming.date = %s
                    """,
                    (programming_date,),
                )
                updated += cur.rowcount
            cur.execute(
                """
                SELECT COUNT(*), COUNT(*) FILTER (WHERE total_volume_m3 IS NULL),
                       MIN(total_volume_m3), MAX(total_volume_m3)
                FROM logistics.daily_programming
                """
            )
            total, missing, minimum, maximum = cur.fetchone()
    if missing:
        raise RuntimeError(f"{missing} of {total} programming rows have no cubic volume")
    print(
        f"daily_programming volume backfill complete: updated={updated}, rows={total}, "
        f"min_m3={minimum}, max_m3={maximum}"
    )


if __name__ == "__main__":
    main()

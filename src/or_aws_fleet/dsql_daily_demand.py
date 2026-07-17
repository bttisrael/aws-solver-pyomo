from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import boto3
import psycopg


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEZONE = "America/Sao_Paulo"


@dataclass(frozen=True)
class DcConfig:
    table: str
    code: str
    customer_code: str
    region: str
    origin: str
    min_orders: int
    max_orders: int
    freight_base: Decimal
    freight_zone: Decimal
    customer_pool: int
    cities: tuple[tuple[str, Decimal, Decimal, Decimal], ...]


DC_CONFIGS = (
    DcConfig(
        table="logistics.dc_1",
        code="DC1",
        customer_code="S",
        region="South",
        origin="DC_1 South - Curitiba, PR",
        min_orders=77,
        max_orders=137,
        freight_base=Decimal("9.50"),
        freight_zone=Decimal("1.00"),
        customer_pool=28_000,
        cities=(
            ("Curitiba, PR", Decimal("-25.4284"), Decimal("-49.2733"), Decimal("1.00")),
            ("Porto Alegre, RS", Decimal("-30.0346"), Decimal("-51.2177"), Decimal("1.20")),
            ("Florianopolis, SC", Decimal("-27.5949"), Decimal("-48.5482"), Decimal("1.10")),
            ("Joinville, SC", Decimal("-26.3044"), Decimal("-48.8487"), Decimal("0.95")),
            ("Londrina, PR", Decimal("-23.3045"), Decimal("-51.1696"), Decimal("1.05")),
            ("Maringa, PR", Decimal("-23.4205"), Decimal("-51.9331"), Decimal("1.08")),
            ("Blumenau, SC", Decimal("-26.9188"), Decimal("-49.0661"), Decimal("1.02")),
            ("Caxias do Sul, RS", Decimal("-29.1678"), Decimal("-51.1794"), Decimal("1.18")),
            ("Cascavel, PR", Decimal("-24.9555"), Decimal("-53.4552"), Decimal("1.16")),
            ("Pelotas, RS", Decimal("-31.7654"), Decimal("-52.3376"), Decimal("1.28")),
        ),
    ),
    DcConfig(
        table="logistics.dc_2",
        code="DC2",
        customer_code="N",
        region="North",
        origin="DC_2 North - Manaus, AM",
        min_orders=68,
        max_orders=113,
        freight_base=Decimal("16.00"),
        freight_zone=Decimal("1.50"),
        customer_pool=22_000,
        cities=(
            ("Manaus, AM", Decimal("-3.1190"), Decimal("-60.0217"), Decimal("1.00")),
            ("Belem, PA", Decimal("-1.4558"), Decimal("-48.4902"), Decimal("1.45")),
            ("Macapa, AP", Decimal("0.0349"), Decimal("-51.0694"), Decimal("1.55")),
            ("Boa Vista, RR", Decimal("2.8235"), Decimal("-60.6758"), Decimal("1.42")),
            ("Porto Velho, RO", Decimal("-8.7612"), Decimal("-63.9004"), Decimal("1.50")),
            ("Rio Branco, AC", Decimal("-9.9750"), Decimal("-67.8243"), Decimal("1.65")),
            ("Palmas, TO", Decimal("-10.1840"), Decimal("-48.3336"), Decimal("1.35")),
            ("Santarem, PA", Decimal("-2.4431"), Decimal("-54.7083"), Decimal("1.48")),
            ("Maraba, PA", Decimal("-5.3686"), Decimal("-49.1178"), Decimal("1.40")),
            ("Parauapebas, PA", Decimal("-6.0675"), Decimal("-49.9022"), Decimal("1.46")),
        ),
    ),
    DcConfig(
        table="logistics.dc_3",
        code="DC3",
        customer_code="C",
        region="Central",
        origin="DC_3 Central - Brasilia, DF",
        min_orders=72,
        max_orders=126,
        freight_base=Decimal("11.50"),
        freight_zone=Decimal("1.15"),
        customer_pool=25_000,
        cities=(
            ("Brasilia, DF", Decimal("-15.7939"), Decimal("-47.8828"), Decimal("1.00")),
            ("Goiania, GO", Decimal("-16.6869"), Decimal("-49.2648"), Decimal("1.05")),
            ("Cuiaba, MT", Decimal("-15.6014"), Decimal("-56.0979"), Decimal("1.32")),
            ("Campo Grande, MS", Decimal("-20.4697"), Decimal("-54.6201"), Decimal("1.25")),
            ("Anapolis, GO", Decimal("-16.3281"), Decimal("-48.9530"), Decimal("1.02")),
            ("Rondonopolis, MT", Decimal("-16.4673"), Decimal("-54.6372"), Decimal("1.28")),
            ("Dourados, MS", Decimal("-22.2231"), Decimal("-54.8120"), Decimal("1.34")),
            ("Sinop, MT", Decimal("-11.8604"), Decimal("-55.5091"), Decimal("1.38")),
            ("Aparecida de Goiania, GO", Decimal("-16.8233"), Decimal("-49.2439"), Decimal("1.04")),
            ("Luziania, GO", Decimal("-16.2525"), Decimal("-47.9502"), Decimal("1.00")),
        ),
    ),
)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    lat NUMERIC(10, 6) NOT NULL,
    long NUMERIC(10, 6) NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    freight NUMERIC(12, 2) NOT NULL,
    date DATE NOT NULL
)
""".strip()

INSERT_SQL = """
INSERT INTO {table} (
    order_id,
    customer_id,
    origin,
    destination,
    sku,
    quantity,
    lat,
    long,
    price,
    freight,
    date
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id,
    origin = EXCLUDED.origin,
    destination = EXCLUDED.destination,
    sku = EXCLUDED.sku,
    quantity = EXCLUDED.quantity,
    lat = EXCLUDED.lat,
    long = EXCLUDED.long,
    price = EXCLUDED.price,
    freight = EXCLUDED.freight,
    date = EXCLUDED.date
""".strip()


SkuRow = tuple[str, str, int, Decimal]
DemandRow = tuple[str, str, str, str, str, int, Decimal, Decimal, Decimal, Decimal, date]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def aws_region() -> str:
    return (
        os.getenv("DSQL_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or "us-east-2"
    )


def resolve_dsql_endpoint(region: str) -> str:
    endpoint = os.getenv("DSQL_CLUSTER_ENDPOINT")
    if endpoint:
        return endpoint

    identifier = os.getenv("DSQL_CLUSTER_IDENTIFIER") or os.getenv("DSQL_CLUSTER_ID")
    if not identifier:
        raise RuntimeError("Set DSQL_CLUSTER_ENDPOINT or DSQL_CLUSTER_IDENTIFIER.")

    client = boto3.client("dsql", region_name=region)
    return client.get_cluster(identifier=identifier)["endpoint"]


def connect_dsql(endpoint: str, region: str) -> psycopg.Connection:
    token = boto3.client("dsql", region_name=region).generate_db_connect_admin_auth_token(
        endpoint, region
    )
    return psycopg.connect(
        host=endpoint,
        dbname=os.getenv("DSQL_DATABASE", "postgres"),
        user=os.getenv("DSQL_DB_USER", "admin"),
        password=token,
        sslmode="require",
        connect_timeout=30,
    )


def parse_run_date(value: str | None, timezone_name: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(ZoneInfo(timezone_name)).date()


def stable_seed(run_date: date, dc_code: str) -> int:
    seed_text = f"{run_date.isoformat()}:{dc_code}"
    return sum((index + 1) * ord(char) for index, char in enumerate(seed_text))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def coord(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def category_price_range(category: str) -> tuple[Decimal, Decimal, Decimal]:
    text = category.lower()
    if any(word in text for word in ("electronics", "computer", "appliance")):
        return Decimal("45.00"), Decimal("1450.00"), Decimal("220.00")
    if any(word in text for word in ("automotive", "tools")):
        return Decimal("25.00"), Decimal("900.00"), Decimal("140.00")
    if any(word in text for word in ("kitchen", "home", "decor", "bed", "furniture")):
        return Decimal("18.00"), Decimal("780.00"), Decimal("95.00")
    if any(word in text for word in ("beauty", "hair", "personal")):
        return Decimal("10.00"), Decimal("260.00"), Decimal("48.00")
    if "pet" in text:
        return Decimal("12.00"), Decimal("320.00"), Decimal("58.00")
    if any(word in text for word in ("fitness", "sports")):
        return Decimal("18.00"), Decimal("680.00"), Decimal("110.00")
    if any(word in text for word in ("office", "stationery")):
        return Decimal("8.00"), Decimal("380.00"), Decimal("45.00")
    if any(word in text for word in ("baby", "kids", "toy")):
        return Decimal("12.00"), Decimal("420.00"), Decimal("60.00")
    if "garden" in text:
        return Decimal("20.00"), Decimal("650.00"), Decimal("90.00")
    return Decimal("15.00"), Decimal("550.00"), Decimal("75.00")


def sku_unit_price(sku: str, category: str) -> Decimal:
    low, high, mode = category_price_range(category)
    seed = sum((index + 1) * ord(char) for index, char in enumerate(sku)) + 20260705
    rng = random.Random(seed)
    return money(Decimal(str(rng.triangular(float(low), float(high), float(mode)))))


def choose_quantity(rng: random.Random) -> int:
    roll = rng.random()
    if roll < 0.64:
        return 1
    if roll < 0.86:
        return 2
    if roll < 0.95:
        return 3
    if roll < 0.985:
        return 4
    return rng.choice([5, 6])


def load_available_skus(conn: psycopg.Connection) -> list[SkuRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sku, category, sku_weight, sku_m3
            FROM logistics.master_data_sku_random
            WHERE sku IS NOT NULL AND sku <> ''
            ORDER BY sku
            """
        )
        rows = [
            (str(sku), str(category), int(weight), Decimal(str(sku_m3)))
            for sku, category, weight, sku_m3 in cur.fetchall()
        ]
    if not rows:
        raise RuntimeError("No available SKUs found in logistics.master_data_sku_random.")
    return rows


def generate_rows_for_dc(config: DcConfig, run_date: date, skus: list[SkuRow]) -> list[DemandRow]:
    rng = random.Random(stable_seed(run_date, config.code))
    order_count = rng.randint(config.min_orders, config.max_orders)
    rows: list[DemandRow] = []

    for sequence in range(1, order_count + 1):
        destination, lat, lng, city_factor = rng.choice(config.cities)
        sku, category, weight_g, sku_m3 = rng.choice(skus)
        quantity = choose_quantity(rng)
        unit_price = sku_unit_price(sku, category)
        price_noise = Decimal(str(rng.uniform(0.94, 1.06)))
        price = money(unit_price * quantity * price_noise)

        weight_kg = Decimal(weight_g) / Decimal("1000")
        freight = money(
            config.freight_base
            + Decimal("7.50") * config.freight_zone * city_factor
            + Decimal("1.35") * weight_kg * quantity
            + Decimal("95.00") * sku_m3 * quantity
            + Decimal(str(rng.uniform(0.0, 9.0)))
        )

        rows.append(
            (
                f"ORD-{config.code}-{run_date:%Y%m%d}-{sequence:06d}",
                f"CUST-{config.customer_code}-{rng.randint(1, config.customer_pool):06d}",
                config.origin,
                destination,
                sku,
                quantity,
                coord(lat + Decimal(str(rng.uniform(-0.12, 0.12)))),
                coord(lng + Decimal(str(rng.uniform(-0.12, 0.12)))),
                price,
                freight,
                run_date,
            )
        )
    return rows


def ensure_tables(conn: psycopg.Connection) -> None:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS logistics")
        for config in DC_CONFIGS:
            cur.execute(CREATE_TABLE_SQL.format(table=config.table))
    conn.autocommit = False


def replace_daily_rows(conn: psycopg.Connection, config: DcConfig, run_date: date, rows: list[DemandRow]) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {config.table} WHERE date = %s", (run_date,))
            cur.executemany(INSERT_SQL.format(table=config.table), rows)


def run_daily_insert(run_date: date, dry_run: bool = False) -> dict[str, Any]:
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)
    print(f"DSQL region: {region}")
    print(f"DSQL endpoint: {endpoint}")
    print(f"Run date: {run_date.isoformat()}")

    summary: dict[str, Any] = {"run_date": run_date.isoformat(), "tables": {}}
    with connect_dsql(endpoint, region) as conn:
        ensure_tables(conn)
        skus = load_available_skus(conn)
        print(f"Available SKUs loaded from master data: {len(skus)}")

        for config in DC_CONFIGS:
            rows = generate_rows_for_dc(config, run_date, skus)
            summary["tables"][config.table] = len(rows)
            if dry_run:
                print(f"dry-run {config.table}: would insert {len(rows)} rows")
                continue
            replace_daily_rows(conn, config, run_date, rows)
            print(f"inserted {config.table}: {len(rows)} rows for {run_date.isoformat()}")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Insert daily generated demand into Aurora DSQL.")
    parser.add_argument("--run-date", help="Date to insert in YYYY-MM-DD format. Defaults to today in the configured timezone.")
    parser.add_argument("--timezone", default=os.getenv("LOCAL_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    run_date = parse_run_date(args.run_date or os.getenv("RUN_DATE"), args.timezone)
    summary = run_daily_insert(run_date, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


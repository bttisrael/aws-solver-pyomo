# OR Production AWS

Production-style operational research pipeline for daily fleet sizing on AWS.

Every day at 17:00, the pipeline generates synthetic delivery demand and solves a
Pyomo optimization model that minimizes the number of vehicles needed to deliver
that demand. Outputs are written locally during development and to S3 when the
pipeline runs in AWS.

## Architecture

```text
Amazon EventBridge Scheduler
  +-- daily 17:00 America/Sao_Paulo
      |
      +-- Amazon ECS Fargate task
          |
          +-- generate daily demand
          +-- solve Pyomo fleet-sizing model
          +-- write demand, assignments, and summary
          +-- upload outputs to Amazon S3

Amazon CloudWatch Logs
  +-- container logs and run summary

Amazon S3
  +-- demand/dt=YYYY-MM-DD/demand.csv
  +-- solutions/dt=YYYY-MM-DD/vehicle_assignments.csv
  +-- summaries/dt=YYYY-MM-DD/summary.json
```

## Optimization Problem

The model is a daily fleet-sizing assignment problem.

Decision variables:

- `x[c, v]`: 1 if customer demand point `c` is assigned to vehicle `v`.
- `y[v]`: 1 if vehicle `v` is used.

Objective:

- Minimize `sum(y[v])`, the number of vehicles used.

Constraints:

- Every demand point is assigned to exactly one vehicle.
- Total demand assigned to a vehicle cannot exceed vehicle capacity.
- A demand point can only be assigned to an active vehicle.
- Vehicle activation is ordered to reduce symmetry and speed up the solve.

The Docker image installs `highspy`, so Pyomo uses HiGHS in the AWS task. If no
MILP solver is available in a local environment, the application falls back to a
first-fit decreasing heuristic and marks the solution as heuristic.

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt

python -m or_aws_fleet.main --run-date 2026-07-04
```

Outputs are written to `data/runs/<date>/`.

Useful environment variables:

```env
RUN_DATE=2026-07-04
DEMAND_POINTS=120
VEHICLE_CAPACITY=100
MAX_VEHICLES=200
OUTPUT_DIR=data/runs
S3_BUCKET=
```

## Tests

```powershell
pytest
```

## Deploy With AWS CDK

Install CDK dependencies:

```powershell
cd infra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cdk bootstrap
cdk deploy
```

The CDK stack creates:

- S3 bucket for run artifacts.
- ECS cluster and Fargate task definition.
- Docker image asset for the optimizer application.
- EventBridge Scheduler schedule at `17:00 America/Sao_Paulo`.
- IAM roles needed for ECS task execution and S3 writes.

## Cost Notes

The task is short-lived and runs once per day. This keeps cost low while still
showing a realistic cloud-native OR pipeline: scheduled generation, containerized
optimization, durable outputs, logs, and infrastructure as code.

## Upload Google Sheets data to Aurora DSQL

The uploader in `scripts/upload_sheets_to_dsql.py` reads these Google Sheets tabs and creates/loads two Aurora DSQL tables in schema `logistics`:

- `MASTER DATA - SKU RANDOM` -> `logistics.master_data_sku_random`
- `VEHICLE MASTER DATA` -> `logistics.vehicle_master_data`

Setup:

```powershell
copy dsql.env.example .env
python -m pip install -r requirements.txt
```

Fill `.env` with AWS credentials and your DSQL cluster endpoint. The Google service account path is already pointed at `C:\Users\israb\Documents\ML-production\service-account.json`.

Dry-run first:

```powershell
python scripts\upload_sheets_to_dsql.py --dry-run
```

Upload/replace both tables:

```powershell
python scripts\upload_sheets_to_dsql.py --replace
```

Then in the Aurora DSQL query editor:

```sql
SELECT * FROM logistics.master_data_sku_random LIMIT 20;
SELECT * FROM logistics.vehicle_master_data LIMIT 20;
```

## Daily Aurora DSQL demand insertion

The deployed AWS schedule `daily-dsql-demand-0000` runs every day at `00:00 America/Sao_Paulo` and invokes a Lambda function that inserts new demand rows into:

```sql
logistics.dc_1
logistics.dc_2
logistics.dc_3
```

The Lambda reads available SKUs directly from `logistics.master_data_sku_random` on every run, so generated orders only use SKUs that exist in the current master data. Each run replaces rows for its own run date before inserting, which keeps reruns idempotent.

Daily order ranges:

```text
DC_1: 77-137 orders/day
DC_2: 68-113 orders/day
DC_3: 72-126 orders/day
```

Local dry-run:

```powershell
cd C:\Users\israb\Documents\OR-production-AWS\lambda\dsql_daily_demand
python -c "import handler; print(handler.lambda_handler({'dry_run': True, 'run_date': '2026-07-06'}, None))"
```

Package Lambda dependencies and deploy/update the Lambda schedule:

```powershell
cd C:\Users\israb\Documents\OR-production-AWS
python -m pip install -r lambda\dsql_daily_demand\requirements.txt -t lambda\dsql_daily_demand --upgrade
cd infra
npx aws-cdk@2.176.0 deploy DsqlDailyDemandStack --app "python dsql_demand_app.py" --require-approval never
```


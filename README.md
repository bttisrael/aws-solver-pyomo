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

## FastAPI + Pyomo fleet optimizer

The optimizer reads one date from `logistics.daily_programming`, separates the
lines by origin/destination route, and solves a two-dimensional bin-packing
model for every route. The primary objective is to minimize active vehicles.

Constraints:

- Every programming line is assigned to exactly one vehicle.
- Vehicle weight is limited to 25,000 kg by default.
- Vehicle capacity is limited to 60 pallet positions by default.
- Vehicle activation is ordered to reduce model symmetry.
- A deterministic first-fit-decreasing solution is used as the upper bound and
  as a fallback when a MILP solver is unavailable.

Local API:

```powershell
$env:PYTHONPATH = "src"
uvicorn or_aws_fleet.api:app --host 0.0.0.0 --port 8080
```

Health check:

```powershell
Invoke-RestMethod http://localhost:8080/health
```

Solve and persist a plan:

```powershell
$body = @{
  programming_date = "2026-07-16"
  max_weight_kg = 25000
  max_pallets = 60
  time_limit_seconds = 60
  persist = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8080/solve `
  -ContentType application/json `
  -Body $body
```

Results are stored in:

```text
logistics.optimization_runs
logistics.optimization_vehicle_assignments
logistics.optimization_line_assignments
```

AWS architecture:

```text
Internal Application Load Balancer
  -> FastAPI on ECS/Fargate
      -> Aurora DSQL daily_programming input
      -> Pyomo + HiGHS route optimization
      -> Aurora DSQL optimization result tables
      -> CloudWatch Logs

DockerImageAsset -> CDK bootstrap ECR repository -> Fargate task definition
```

The load balancer is internal and only accepts traffic from inside its VPC. Add
an authenticated API Gateway, VPN, or corporate network route before exposing
the service to external callers.

### Remote image build with CodeBuild

Production images are built in AWS rather than on a developer workstation:

1. CDK packages the application source as an S3 asset. Credentials, local data,
   caches, infrastructure output, and `.env` files are excluded.
2. CodeBuild downloads the source into an isolated Linux build environment.
3. CodeBuild installs test dependencies and runs `pytest`.
4. A successful test phase runs `docker build` using the project Dockerfile.
5. The image is tagged with an immutable source-content hash.
6. CodeBuild pushes the image to the private `or-fleet-optimizer` ECR repository.
7. ECR scans the image on push and retains the newest 20 images.
8. The Fargate task definition references the exact immutable image tag.
9. ECS starts the FastAPI service and sends container output to CloudWatch Logs.

Deploy/update the build resources, execute the remote build, then deploy the
service stack:

```powershell
cd infra
npx aws-cdk@2.176.0 deploy OptimizerBuildStack --app "python app.py" --require-approval never
cd ..
python scripts\run_optimizer_codebuild.py
cd infra
npx aws-cdk@2.176.0 deploy OrFleetOptimizationStack --app "python app.py" --require-approval never
```

## Production CI/CD

The repository uses two GitHub Actions workflows:

- `.github/workflows/ci.yml` runs on pull requests and `main`. It installs from a
  clean Python 3.11 environment, runs Ruff and pytest, validates the Docker
  build, and synthesizes the CDK stacks. It has read-only repository access and
  no AWS credentials.
- `.github/workflows/deploy-production.yml` runs only after the `main` CI
  workflow succeeds, or through a manual dispatch. The job enters the protected `production` environment,
  obtains short-lived AWS credentials through GitHub OIDC, launches CodeBuild,
  deploys the immutable ECR image with CDK, and waits for ECS and target-group
  health.

No long-lived AWS access key is stored in GitHub. ECS uses a deployment circuit
breaker with rollback enabled. Deployments are serialized, so two production
releases cannot modify the service concurrently.

```text
Pull request -> lint + tests + Docker validation + CDK synth
      |
      +-- merge to main
              |
              +-- protected production environment approval
                      |
                      +-- GitHub OIDC -> short-lived AWS role
                              |
                              +-- CodeBuild -> pytest -> Docker -> ECR
                                      |
                                      +-- CDK -> ECS/Fargate
                                              |
                                              +-- service stable + ALB health
                                                      |
                                                      +-- success or ECS rollback
```

### One-time AWS and GitHub setup

Bootstrap CDK and create the narrowly trusted GitHub deployment role using an
administrator's local AWS session:

```powershell
cd infra
npx aws-cdk@2.176.0 bootstrap aws://922981236785/us-east-2

aws cloudformation deploy `
  --stack-name AwsSolverPyomoGitHubOidc `
  --template-file github_oidc.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --region us-east-2
```

An AWS account can have only one GitHub Actions OIDC provider. If it already
exists, pass its ARN to avoid creating a duplicate:

```powershell
aws cloudformation deploy `
  --stack-name AwsSolverPyomoGitHubOidc `
  --template-file github_oidc.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides ExistingGitHubOidcProviderArn=arn:aws:iam::922981236785:oidc-provider/token.actions.githubusercontent.com `
  --region us-east-2
```

In GitHub, create an environment named `production`, add the required reviewers,
restrict its deployment branch to `main`, and create these repository variables:

| Variable | Value |
|---|---|
| `AWS_ACCOUNT_ID` | `922981236785` |
| `AWS_REGION` | `us-east-2` |
| `AWS_DEPLOY_ROLE_ARN` | The `DeployRoleArn` output from `AwsSolverPyomoGitHubOidc` |

Protect `main`: require pull requests, require the `test` and `synthesize`
checks, require the branch to be current before merging, block force pushes, and
block deletion. Dependabot opens weekly Python, CDK, and GitHub Actions update
pull requests.

### English optimization load-plan table

`logistics.optimization_load_plan` contains the presentation-ready assignment:

```text
run_id, vehicle_id, position_number, load_level, load_item_label,
origin, destination, material_code, boxes, units_by_material,
total_units, weight_kg, pallet_volume, demand_id
```

`BASE` and `TOP` rows share a position number. Items are ordered by descending
weight so each base item is at least as heavy as its paired top item. A row is an
optimized programming-line load item; fractional pallet demand is deliberately
not represented as a complete physical pallet.

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


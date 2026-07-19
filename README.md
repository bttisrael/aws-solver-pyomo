# OR Production AWS

Production-style demand forecasting and operational research pipeline for fleet
sizing on AWS. It combines a rolling 21-day probabilistic forecast with Pyomo
vehicle minimization and a public, continuously available Streamlit portfolio dashboard.

## Architecture

```text
Amazon EventBridge Scheduler
  +-- daily 00:00 America/Sao_Paulo: synthetic demand and daily programming
  +-- daily 00:15 America/Sao_Paulo: 21-day forecast and optimization
      |
      +-- Amazon ECS Fargate task
          |
          +-- persisted AutoML champion forecast (P10/P50/P90)
          +-- solve P50 and P90 Pyomo fleet-sizing plans for D+1..D+21
          +-- persist forecasts, vehicle assignments, and load plans in Aurora DSQL

Amazon CloudWatch Logs
  +-- container logs and run summary

Amazon Aurora DSQL
  +-- daily_programming and optimization results
  +-- demand_forecast and forecast optimization results
```

## Forecasting and model governance

The production forecast is refreshed every day but the model is not retrained
every day. On the first run, the training pipeline evaluates histogram gradient
boosting, random forest, and extremely randomized trees on a recursive 21-day
time holdout. The lowest-WAPE candidate is refit on the full one-year history,
versioned in `forecast_model_registry`, and persisted in the private artifacts
S3 bucket. Daily runs load that champion to predict the next 21 business totals.
The same-weekday route/SKU profile disaggregates those ML totals to active demand
series, while validation residuals produce calibrated P10/P50/P90 scenarios.
P50 drives the expected plan; P90 provides a conservative capacity plan.

Demand generation uses persistent intermittent route-SKU series rather than a
new random product mix every day. Stable base velocities are combined with
calendar seasonality, annual growth, correlated market cycles, route and SKU
cycles, multi-day promotions, occasional stockouts, heavy-tailed order noise,
and rare market-wide shocks. This creates learnable structure while retaining
realistic residual uncertainty for AutoML challenger evaluation.

AutoML retraining is triggered only after three consecutive failed monitoring
evaluations and at least seven days since the previous training job. The gates
are WAPE above 20%, MASE above 1.0, absolute bias above 7%, or prediction interval
coverage outside 70%-90%. The persisted champion remains available between
training jobs, so daily forecast generation does not incur daily training cost.
The seasonal weekday forecast remains the validation benchmark and supplies the
route/SKU allocation profile; the persisted AutoML champion produces the daily
business totals used by the production forecast.

Forecast tables:

```text
logistics.forecast_runs
logistics.demand_forecast
logistics.forecast_optimization_runs
logistics.forecast_vehicle_assignments
logistics.forecast_load_plan
logistics.forecast_model_registry
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
lines by origin/destination route, and solves a multi-fleet bin-packing model
for every route. The weighted objective minimizes active vehicles plus freight
cost (`Google driving distance x vehicle freight cost/km`).

Constraints:

- Every programming line is assigned to exactly one vehicle.
- Weight, pallet, and cubic-volume limits are enforced for each enabled vehicle type.
- Vehicle types and capacities come from `logistics.vehicle_master_data`.
- Route distance comes from the Google Routes API value in `logistics.route`.
- Vehicle-count and freight-cost weights are editable for scenario analysis.
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
  vehicle_count_weight = 1.0
  freight_cost_weight = 0.001
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
Internet-facing Application Load Balancer
  -> Streamlit operations dashboard on ECS/Fargate
      -> editable solver configuration and simulation
      -> macro and detailed optimization results
      -> daily_programming browser and CSV export
      -> CrewAI data analyst grounded in curated Aurora DSQL queries
          -> Claude 3 Haiku through Amazon Bedrock
          -> $0.50 server-side daily usage budget
  -> FastAPI + Pyomo in the same ECS/Fargate task
      -> Aurora DSQL daily_programming input
      -> Pyomo + HiGHS route optimization
      -> Aurora DSQL optimization result tables
      -> CloudWatch Logs

DockerImageAsset -> CDK bootstrap ECR repository -> Fargate task definition
```

The public portfolio dashboard runs continuously with one ECS/Fargate task. CDK
enforces both desired count and minimum capacity at one, so ECS automatically
replaces an unhealthy task and production deployments wait for load-balancer
health before succeeding. Only Streamlit is registered with the public load
balancer; FastAPI and Aurora DSQL are not directly exposed. Use only
synthetic/non-confidential portfolio data. The container pins Streamlit to a
tested version, and ALB cookie stickiness keeps each browser's HTML, lazy
JavaScript modules, and WebSocket on the same ECS task during rolling deployments.

Availability configuration:

```text
Public app: http://OrFlee-Optim-5Z8Y1eU8XJzT-747546465.us-east-2.elb.amazonaws.com/
ECS desired count: 1
ECS minimum capacity: 1
Maximum running tasks: 1
```

The portfolio **Live app** button can link directly to the public app URL; no
GitHub or AWS authentication step is required for visitors.

```html
<a href="http://OrFlee-Optim-5Z8Y1eU8XJzT-747546465.us-east-2.elb.amazonaws.com/">
  <button>Live app</button>
</a>
```

### Streamlit operations dashboard

The interface uses a dark logistics control-center theme across every screen,
with cyan operational KPIs, compact fleet panels, dark data grids, and a
color-coded route map optimized for desktop demonstrations.

The dashboard provides six operator screens:

1. **Solver Configuration** selects the programming date and displays every
   vehicle type in an editable table. Users can enable vehicle types, change
   weight, pallet, volume, and cost/km parameters, tune the objective weights,
   and execute the Pyomo scenario without changing code.
2. **Actual Optimization** selects one of the five latest persisted runs, shows macro vehicle,
   route, box, weight, and occupancy KPIs, presents the vehicle summary, and
   provides the detailed BASE/TOP operational load plan with CSV export.
3. **Forecast Optimization** shows the 21-day P50/P90 vehicle curve, forecast and
   governance KPIs, and date/scenario-level operational loads with CSV export.
4. **Route Network** maps every factory-to-distribution-center connection using
   the route master coordinates and Google driving distances. Filters, map
   tooltips, route KPIs, and a detailed table combine network data with vehicle,
   load, occupancy, and freight metrics from the latest actual optimization.
5. **Daily Programming** displays the selected input date with origin and
   destination filters, totals, and CSV export.
6. **AI Data Analyst** provides a conversational CrewAI agent backed by Claude
   3 Haiku on Amazon Bedrock. The agent can inspect curated, row-limited demand,
   route, vehicle, forecast, and optimization summaries from Aurora DSQL. It
   cannot execute arbitrary SQL or write to the database. A server-side daily
   usage ledger reserves budget before each request and enforces a $0.50 cap.

Local execution requires the DSQL variables in the current PowerShell session:

```powershell
$env:PYTHONPATH = "src"
streamlit run src\or_aws_fleet\streamlit_app.py
```

In AWS, the same immutable image runs as two containers in one Fargate task:
FastAPI listens on port 8080 and Streamlit listens on port 8501. Both use the
task IAM role for short-lived Aurora DSQL authentication; the dashboard also
uses that role for Bedrock inference, so no long-lived LLM API key is stored.
The public load
balancer routes users to Streamlit and checks `/_stcore/health`.

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

The uploader in `scripts/upload_sheets_to_dsql.py` reads the `VEHICLE MASTER DATA`
Google Sheets tab and creates/loads the retained Aurora DSQL table:

- `VEHICLE MASTER DATA` -> `logistics.vehicle_master_data`

The optimizer uses `logistics.daily_programming` as its demand input and joins
the retained fleet and route master tables:

```text
logistics.vehicle_master_data
logistics.route
logistics.daily_programming
```

`logistics.route.google_driving_distance_km` contains Google Routes API driving
distance. `daily_programming` carries that distance and `total_volume_m3` into
the solver input.

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

Upload/replace the vehicle table:

```powershell
python scripts\upload_sheets_to_dsql.py --replace
```

Then in the Aurora DSQL query editor:

```sql
SELECT * FROM logistics.vehicle_master_data LIMIT 20;
```

The one-time DSQL cleanup migration is available at:

```powershell
python scripts\drop_legacy_dsql_tables.py
```

Package Lambda dependencies and deploy/update the Lambda schedule:

```powershell
cd C:\Users\israb\Documents\OR-production-AWS
python -m pip install -r lambda\dsql_daily_demand\requirements.txt -t lambda\dsql_daily_demand --upgrade
cd infra
npx aws-cdk@2.176.0 deploy DsqlDailyDemandStack --app "python dsql_demand_app.py" --require-approval never
```

Demand volume is calibrated with `DEMAND_UNITS_MULTIPLIER=4`. The versioned
historical migration is idempotent and records each completed table/date step:

```powershell
$env:PYTHONPATH = "src"
python scripts\scale_beverage_demand.py --through-date 2026-07-17
```


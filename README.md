# Beverage Forecasting & Fleet Optimization

A public, production-style MLOps and operations-research platform for synthetic
beverage logistics. The system generates daily demand, forecasts the next 21
days with a governed AutoML champion, and uses Pyomo + HiGHS to select a
cost-aware mixed fleet for 45 factory-to-distribution-center routes.

[Open the live platform](http://orflee-optim-5z8y1eu8xjzt-747546465.us-east-2.elb.amazonaws.com/)

> Portfolio note: all demand, products, locations, and operating results are
> synthetic. No commercial beverage brands or confidential operational data are
> used.

## What the platform demonstrates

- A continuously available Streamlit control center on Amazon ECS/Fargate.
- Five synthetic factories, nine distribution centers, 45 routes, and seven
  configurable vehicle types.
- Daily seasonal demand generation and an enriched `daily_programming` input.
- A recursive 21-day machine-learning forecast with P10/P50/P90 scenarios.
- AutoML challenger selection, model artifacts in S3, registry metadata in
  Aurora DSQL, validation gates, and performance-triggered retraining.
- Multi-vehicle Pyomo optimization across weight, volume, pallet capacity,
  driving distance, and freight cost.
- A read-only CrewAI data analyst using Amazon Nova Lite through Amazon Bedrock,
  including validated charts and a server-side USD 0.50 daily budget.
- Infrastructure as code, remote container builds, short-lived GitHub OIDC
  credentials, automated tests, deployment health checks, and rollback.

## Application screens

| Screen | Purpose |
|---|---|
| **Solver Configuration** | Edit all vehicle capacities and costs, choose the programming date, tune objective weights, and run both actual and forecast optimization. |
| **Actual Optimization** | Inspect one of the five latest runs, macro KPIs, route/vehicle assignments, and the BASE/TOP operational load plan. |
| **Forecast Optimization** | Compare the AutoML P50 forecast with an eight-week same-weekday baseline, review holdout metrics, and inspect P50/P90 optimized plans for D+1 through D+21. |
| **Route Network** | Explore all routes on a map with Google driving distance, utilization, freight cost, projected 21-day cost, and theoretical capacity opportunity. |
| **Daily Programming** | Filter the selected demand snapshot by factory and distribution center and export it as CSV. |
| **AI Data Analyst** | Ask grounded questions about curated demand, routes, vehicles, forecasts, and optimization results, and request safe bar, line, or scatter charts. |

## AWS architecture

```text
EventBridge Scheduler
  00:00 America/Sao_Paulo
    -> Lambda demand generator
       -> logistics.daily_demand
       -> logistics.daily_programming

  00:15 America/Sao_Paulo
    -> one-off ECS/Fargate forecast task
       -> load or train AutoML champion
       -> persist model artifact in private S3
       -> generate P10/P50/P90 for D+1..D+21
       -> optimize P50 and P90 plans with Pyomo + HiGHS
       -> persist forecasts and load plans in Aurora DSQL

Internet -> public Application Load Balancer
  -> continuously running ECS/Fargate service
     -> Streamlit dashboard (public target, port 8501)
     -> FastAPI + Pyomo container (not exposed by the ALB, port 8080)
     -> Aurora DSQL using task-role authentication
     -> Bedrock Nova Lite using task-role authentication

GitHub pull request -> Ruff + pytest + Docker build + CDK synth
  -> merge to main -> protected production environment
  -> GitHub OIDC -> CodeBuild -> private ECR
  -> CDK deploy -> ECS health check or automatic rollback

CloudWatch Logs <- Lambda, CodeBuild, forecast task, API, and dashboard
```

The public service keeps exactly one Fargate task online. ECS replaces an
unhealthy task automatically, and Application Load Balancer cookie stickiness
keeps Streamlit WebSocket and lazy-loaded assets on the same task during rolling
deployments.

## Data model

Core inputs:

```text
logistics.vehicle_master_data
logistics.route
logistics.daily_demand
logistics.daily_programming
```

Actual optimization outputs:

```text
logistics.optimization_runs
logistics.optimization_vehicle_assignments
logistics.optimization_line_assignments
logistics.optimization_load_plan
```

Forecasting and forecast-optimization outputs:

```text
logistics.forecast_runs
logistics.demand_forecast
logistics.forecast_optimization_runs
logistics.forecast_vehicle_assignments
logistics.forecast_load_plan
logistics.forecast_model_registry
```

Agent governance:

```text
logistics.agent_daily_usage
```

`daily_programming` enriches demand with product weight, boxes, pallets, cubic
volume, and Google driving distance. A pallet requirement is calculated as:

```text
units / (qty_by_box * qty_by_pallet)
```

## Forecasting and MLOps

The first training run evaluates histogram gradient boosting, random forest,
and extremely randomized trees using leakage-free lag, rolling, calendar, and
seasonality features. Candidate models are compared on a recursive 21-day time
holdout. The lowest-WAPE model is refit on all available history and persisted
as the champion.

Daily inference loads the champion rather than retraining it. The model predicts
daily business totals, while the recent same-weekday route/SKU profile allocates
those totals to active logistics series. Validation residuals calibrate the
P10/P50/P90 uncertainty scenarios.

Monitoring evaluates WAPE, MASE, bias, and interval coverage. Retraining is
eligible only after three consecutive failed evaluations and a seven-day
cooldown. Current gates are:

| Metric | Retraining gate |
|---|---|
| WAPE | greater than 20% |
| MASE | greater than 1.0 |
| Absolute bias | greater than 7% |
| P10-P90 coverage | outside 70%-90% |

The dashboard reports model-selection metrics from the recursive holdout; it
does not present unknown future 21-day actuals as realized accuracy.

## Optimization model

Demand is partitioned by origin/destination route. For every route, Pyomo
assigns each programming line to one enabled vehicle candidate and activates
only the vehicles required to carry it.

The weighted objective is:

```text
minimize(
    vehicle_count_weight * active_vehicle_count
    + freight_cost_weight
      * sum(route_distance_km * vehicle_freight_cost_per_km)
)
```

Constraints enforce:

- exactly one vehicle assignment per demand line;
- weight, pallet, and cubic-volume capacity for each vehicle type;
- assignment only to an activated vehicle;
- ordered activation to reduce symmetry.

HiGHS is the production MILP solver. A deterministic first-fit-decreasing plan
provides an upper bound and a fallback if a solver is unavailable.

## AI Data Analyst safety and cost controls

The CrewAI agent is grounded through predefined, row-limited analytical tools.
It cannot execute arbitrary SQL, mutate Aurora DSQL, run generated code, or
render arbitrary HTML. Chart requests are restricted to validated bar, line,
and scatter specifications rendered by trusted Streamlit/Altair components.

Amazon Bedrock is called with the ECS task role, so the deployment stores no
long-lived LLM API key. A transactionally reserved daily ledger enforces the
USD 0.50 server-side budget before inference.

## Local development

Requirements: Python 3.11+, Node.js 22 for CDK synthesis, and Docker for local
container validation.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
$env:PYTHONPATH = "src"
```

Run the dashboard:

```powershell
streamlit run src\or_aws_fleet\streamlit_app.py
```

Run the private API locally:

```powershell
uvicorn or_aws_fleet.api:app --host 0.0.0.0 --port 8080
```

Run validation:

```powershell
ruff check src tests infra scripts
pytest -q
docker build --tag or-fleet-optimizer:local .
cd infra
cdk synth --quiet
```

Runtime database access uses short-lived Aurora DSQL authentication. Keep local
configuration in `.env`; that file, credentials, generated data, and deployment
outputs are excluded from version control and container build context.

## CI/CD and production deployment

`Continuous integration` runs for pull requests and pushes to `main`:

1. install a clean Python 3.11 environment;
2. run Ruff and pytest;
3. build the Docker image;
4. synthesize the CDK stacks.

After successful `main` CI, `Deploy production` enters the protected GitHub
`production` environment, assumes a narrowly trusted AWS role through OIDC,
starts CodeBuild, publishes an immutable image to ECR, deploys with CDK, and
waits for both ECS stability and healthy load-balancer targets. The workflow is
also manually dispatchable.

No long-lived AWS key is stored in GitHub. Production deployments are serialized,
ECR scans images on push, and the ECS deployment circuit breaker rolls back an
unhealthy release.

Infrastructure stacks:

```text
OptimizerBuildStack          CodeBuild and private ECR image pipeline
OrFleetOptimizationStack     ALB, ECS service, forecast task, S3, IAM, schedules
DsqlDailyDemandStack         Lambda demand generator and 00:00 schedule
AwsSolverPyomoGitHubOidc     GitHub Actions OIDC deployment role
```

Manual production deployment should normally use the existing GitHub Actions
workflow. For infrastructure development, CDK can be synthesized locally from
`infra/`; do not commit `.env`, AWS credentials, generated CDK output, or data
exports.

## Technology stack

Python, Pandas, scikit-learn, joblib, Pyomo, HiGHS, FastAPI, Streamlit, Altair,
CrewAI, Amazon Bedrock Nova Lite, Aurora DSQL, Amazon S3, Lambda, EventBridge
Scheduler, CodeBuild, ECR, ECS/Fargate, Application Load Balancer, CloudWatch,
AWS CDK, Docker, and GitHub Actions.

## License and portfolio use

This repository is a portfolio demonstration. Review AWS costs, IAM policies,
networking, data classification, and operational controls before adapting it to
a commercial workload.

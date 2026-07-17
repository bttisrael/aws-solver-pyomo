from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack, aws_iam as iam, aws_lambda as lambda_, aws_logs as logs, aws_scheduler as scheduler
from constructs import Construct


class DsqlDailyDemandStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        project_root = Path(__file__).resolve().parents[1]
        dsql_region = self.node.try_get_context("dsql_region") or "us-east-2"
        dsql_cluster_identifier = (
            self.node.try_get_context("dsql_cluster_identifier")
            or "tjt42epnmb2gto7zpkpsmuqvnq"
        )
        dsql_database = self.node.try_get_context("dsql_database") or "postgres"
        dsql_db_user = self.node.try_get_context("dsql_db_user") or "admin"

        demand_function = lambda_.Function(
            self,
            "DailyDsqlDemandFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(str(project_root / "lambda" / "dsql_daily_demand")),
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "DSQL_REGION": dsql_region,
                "DSQL_CLUSTER_IDENTIFIER": dsql_cluster_identifier,
                "DSQL_DATABASE": dsql_database,
                "DSQL_DB_USER": dsql_db_user,
                "LOCAL_TIMEZONE": "America/Sao_Paulo",
                "BASELINE_DEMAND_ROWS": "1000",
                "DEMAND_SEED": "271828",
            },
        )
        demand_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dsql:GetCluster", "dsql:DbConnectAdmin"],
                resources=["*"],
            )
        )

        scheduler_role = iam.Role(
            self,
            "DailyDsqlDemandSchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[demand_function.function_arn],
            )
        )

        scheduler.CfnSchedule(
            self,
            "DailyDsqlDemandSchedule",
            name="daily-dsql-demand-0000",
            description="Generate seasonal beverage demand in Aurora DSQL at 00:00 Sao Paulo time.",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(0 0 * * ? *)",
            schedule_expression_timezone="America/Sao_Paulo",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=demand_function.function_arn,
                role_arn=scheduler_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_event_age_in_seconds=3600,
                    maximum_retry_attempts=2,
                ),
            ),
        )

        CfnOutput(self, "DailyDsqlDemandFunctionName", value=demand_function.function_name)
        CfnOutput(self, "DailyDsqlDemandScheduleName", value="daily-dsql-demand-0000")


from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
)
from constructs import Construct


class OrFleetStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        project_root = Path(__file__).resolve().parents[1]

        bucket = s3.Bucket(
            self,
            "RunArtifactsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireRunArtifactsAfterOneYear",
                    expiration=Duration.days(365),
                )
            ],
        )

        vpc = ec2.Vpc(
            self,
            "OptimizerVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                )
            ],
        )

        cluster = ecs.Cluster(self, "OptimizerCluster", vpc=vpc)

        task_definition = ecs.FargateTaskDefinition(
            self,
            "OptimizerTaskDefinition",
            cpu=512,
            memory_limit_mib=1024,
        )
        bucket.grant_read_write(task_definition.task_role)

        image_asset = ecr_assets.DockerImageAsset(
            self,
            "OptimizerImage",
            directory=str(project_root),
            exclude=[
                "cdk.out",
                "infra/cdk.out",
                ".venv",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                "data",
            ],
        )

        log_group = logs.LogGroup(
            self,
            "OptimizerLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        task_definition.add_container(
            "optimizer",
            image=ecs.ContainerImage.from_docker_image_asset(image_asset),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="or-fleet",
                log_group=log_group,
            ),
            environment={
                "S3_BUCKET": bucket.bucket_name,
                "S3_PREFIX": "daily-fleet-sizing",
                "DEMAND_POINTS": "120",
                "VEHICLE_CAPACITY": "100",
                "MAX_VEHICLES": "200",
                "OUTPUT_DIR": "/tmp/or-fleet-runs",
            },
        )

        security_group = ec2.SecurityGroup(
            self,
            "OptimizerTaskSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )

        scheduler_role = iam.Role(
            self,
            "SchedulerRunTaskRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_definition.task_definition_arn],
                conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
            )
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    task_definition.task_role.role_arn,
                    task_definition.execution_role.role_arn,
                ],
            )
        )

        subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC).subnet_ids

        scheduler.CfnSchedule(
            self,
            "DailyFleetOptimizationSchedule",
            name="daily-fleet-optimization-1700",
            description="Run daily demand generation and Pyomo fleet sizing at 17:00 Sao Paulo time.",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(0 17 * * ? *)",
            schedule_expression_timezone="America/Sao_Paulo",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=scheduler_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task_definition.task_definition_arn,
                    launch_type="FARGATE",
                    task_count=1,
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            assign_public_ip="ENABLED",
                            security_groups=[security_group.security_group_id],
                            subnets=subnets,
                        )
                    ),
                ),
            ),
        )

        CfnOutput(self, "ArtifactsBucketName", value=bucket.bucket_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn)

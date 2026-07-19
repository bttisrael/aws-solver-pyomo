from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
)
from constructs import Construct


class OrFleetStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        image_repository: ecr.IRepository,
        image_tag: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        dsql_region = self.node.try_get_context("dsql_region") or "us-east-2"
        dsql_cluster_identifier = self.node.try_get_context("dsql_cluster_identifier") or "tjt42epnmb2gto7zpkpsmuqvnq"

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
            cpu=2048,
            memory_limit_mib=4096,
        )
        bucket.grant_read_write(task_definition.task_role)

        log_group = logs.LogGroup(
            self,
            "OptimizerLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        container = task_definition.add_container(
            "optimizer",
            image=ecs.ContainerImage.from_ecr_repository(image_repository, image_tag),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="or-fleet",
                log_group=log_group,
            ),
            environment={
                "DSQL_REGION": dsql_region,
                "DSQL_CLUSTER_IDENTIFIER": dsql_cluster_identifier,
                "DSQL_DATABASE": "postgres",
                "DSQL_DB_USER": "admin",
                "MODEL_BUCKET": bucket.bucket_name,
            },
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))
        dashboard = task_definition.add_container(
            "dashboard",
            image=ecs.ContainerImage.from_ecr_repository(image_repository, image_tag),
            command=[
                "streamlit",
                "run",
                "src/or_aws_fleet/streamlit_app.py",
                "--server.address=0.0.0.0",
                "--server.port=8501",
                "--server.headless=true",
            ],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="or-fleet-dashboard",
                log_group=log_group,
            ),
            environment={
                "DSQL_REGION": dsql_region,
                "DSQL_CLUSTER_IDENTIFIER": dsql_cluster_identifier,
                "DSQL_DATABASE": "postgres",
                "DSQL_DB_USER": "admin",
                "MODEL_BUCKET": bucket.bucket_name,
                "AGENT_BEDROCK_REGION": "us-east-1",
                "AGENT_BEDROCK_MODEL_ID": "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
                "AGENT_DAILY_BUDGET_USD": "0.50",
                "CREWAI_DISABLE_TELEMETRY": "true",
            },
        )
        dashboard.add_port_mappings(ecs.PortMapping(container_port=8501))
        task_definition.task_role.add_to_policy(
            iam.PolicyStatement(actions=["dsql:GetCluster", "dsql:DbConnectAdmin"], resources=["*"])
        )
        task_definition.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:us-east-1::foundation-model/"
                    "anthropic.claude-3-haiku-20240307-v1:0"
                ],
            )
        )

        forecast_task = ecs.FargateTaskDefinition(
            self, "DailyForecastTaskDefinition", cpu=2048, memory_limit_mib=4096
        )
        bucket.grant_read_write(forecast_task.task_role)
        forecast_task.add_container(
            "forecast",
            image=ecs.ContainerImage.from_ecr_repository(image_repository, image_tag),
            command=["python", "-m", "or_aws_fleet.dsql_forecast"],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="forecast", log_group=log_group),
            environment={
                "DSQL_REGION": dsql_region,
                "DSQL_CLUSTER_IDENTIFIER": dsql_cluster_identifier,
                "DSQL_DATABASE": "postgres",
                "DSQL_DB_USER": "admin",
                "MODEL_BUCKET": bucket.bucket_name,
                # Forecasting runs daily; training runs initially and then only
                # after three consecutive monitoring failures and a seven-day cooldown.
                "ENABLE_AUTOML_RETRAINING": "true",
            },
        )
        forecast_task.task_role.add_to_policy(
            iam.PolicyStatement(actions=["dsql:GetCluster", "dsql:DbConnectAdmin"], resources=["*"])
        )

        security_group = ec2.SecurityGroup(
            self,
            "OptimizerTaskSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )
        load_balancer_security_group = ec2.SecurityGroup(
            self,
            "OptimizerLoadBalancerSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )
        load_balancer_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow public HTTP access to the portfolio dashboard",
        )
        security_group.add_ingress_rule(
            load_balancer_security_group,
            ec2.Port.tcp(8501),
            "Allow Streamlit only from the internal load balancer",
        )

        service = ecs.FargateService(
            self,
            "OptimizerApiService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=True,
            security_groups=[security_group],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
            max_healthy_percent=200,
        )
        scaling = service.auto_scale_task_count(min_capacity=1, max_capacity=1)
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=65,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(1),
        )

        load_balancer = elbv2.ApplicationLoadBalancer(
            self,
            "OptimizerApiLoadBalancer",
            vpc=vpc,
            internet_facing=True,
            security_group=load_balancer_security_group,
        )
        listener = load_balancer.add_listener("HttpListener", port=80, open=False)
        # Use a new construct ID when migrating the original internal ALB to
        # the public portfolio ALB. This forces CloudFormation to create a
        # fresh target group instead of briefly attaching the old target group
        # to two load balancers during replacement.
        target_group = listener.add_targets(
            "OptimizerDashboardTargetsV2",
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.load_balancer_target(container_name="dashboard", container_port=8501)],
            health_check=elbv2.HealthCheck(path="/_stcore/health", healthy_http_codes="200"),
            deregistration_delay=Duration.seconds(30),
        )
        # Streamlit keeps browser state over a WebSocket and lazy-loads hashed
        # frontend chunks. During a rolling deployment, keep one browser on one
        # task so its HTML, JavaScript modules, and WebSocket use the same image.
        target_group.set_attribute("stickiness.enabled", "true")
        target_group.set_attribute("stickiness.type", "lb_cookie")
        target_group.set_attribute("stickiness.lb_cookie.duration_seconds", "7200")

        forecast_scheduler_role = iam.Role(
            self,
            "DailyForecastSchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        forecast_scheduler_role.add_to_policy(
            iam.PolicyStatement(actions=["ecs:RunTask"], resources=[forecast_task.task_definition_arn])
        )
        forecast_scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[forecast_task.task_role.role_arn, forecast_task.execution_role.role_arn],
            )
        )
        scheduler.CfnSchedule(
            self,
            "DailyForecastSchedule",
            name="or-fleet-daily-forecast-0015",
            description="Refresh the 21-day forecast and optimized P50/P90 plans after daily demand generation.",
            schedule_expression="cron(15 0 * * ? *)",
            schedule_expression_timezone="America/Sao_Paulo",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=forecast_scheduler_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_event_age_in_seconds=3600, maximum_retry_attempts=2
                ),
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=forecast_task.task_definition_arn,
                    launch_type="FARGATE",
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            assign_public_ip="ENABLED",
                            subnets=[subnet.subnet_id for subnet in vpc.public_subnets],
                            security_groups=[security_group.security_group_id],
                        )
                    ),
                ),
            ),
        )

        CfnOutput(self, "ArtifactsBucketName", value=bucket.bucket_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "TargetGroupArn", value=target_group.target_group_arn)
        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn)
        CfnOutput(self, "OptimizerPublicDashboardUrl", value=f"http://{load_balancer.load_balancer_dns_name}")
        CfnOutput(self, "DailyForecastScheduleName", value="or-fleet-daily-forecast-0015")

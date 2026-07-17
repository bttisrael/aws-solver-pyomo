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
    aws_lambda as lambda_,
    aws_s3 as s3,
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
            },
        )
        dashboard.add_port_mappings(ecs.PortMapping(container_port=8501))
        task_definition.task_role.add_to_policy(
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
            "Allow public HTTP access to the time-limited portfolio dashboard",
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
            desired_count=0,
            assign_public_ip=True,
            security_groups=[security_group],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
            max_healthy_percent=200,
        )
        scaling = service.auto_scale_task_count(min_capacity=0, max_capacity=1)
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
        target_group = listener.add_targets(
            "OptimizerDashboardTargets",
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.load_balancer_target(container_name="dashboard", container_port=8501)],
            health_check=elbv2.HealthCheck(path="/_stcore/health", healthy_http_codes="200"),
            deregistration_delay=Duration.seconds(30),
        )

        stop_function = lambda_.Function(
            self,
            "PortfolioDemoStopFunction",
            function_name="or-fleet-stop-portfolio-demo",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(
                "import os\n"
                "import boto3\n\n"
                "def handler(event, context):\n"
                "    boto3.client('ecs').update_service(\n"
                "        cluster=os.environ['ECS_CLUSTER'],\n"
                "        service=os.environ['ECS_SERVICE'],\n"
                "        desiredCount=0,\n"
                "    )\n"
                "    return {'desired_count': 0}\n"
            ),
            timeout=Duration.seconds(30),
            environment={
                "ECS_CLUSTER": cluster.cluster_name,
                "ECS_SERVICE": service.service_name,
            },
        )
        stop_function.add_to_role_policy(
            iam.PolicyStatement(actions=["ecs:UpdateService"], resources=[service.service_arn])
        )
        scheduler_role = iam.Role(
            self,
            "PortfolioDemoSchedulerRole",
            role_name="or-fleet-demo-scheduler",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        stop_function.grant_invoke(scheduler_role)

        CfnOutput(self, "ArtifactsBucketName", value=bucket.bucket_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "TargetGroupArn", value=target_group.target_group_arn)
        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn)
        CfnOutput(self, "OptimizerPublicDashboardUrl", value=f"http://{load_balancer.load_balancer_dns_name}")
        CfnOutput(self, "PortfolioStopFunctionArn", value=stop_function.function_arn)
        CfnOutput(self, "PortfolioSchedulerRoleArn", value=scheduler_role.role_arn)

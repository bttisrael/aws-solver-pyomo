from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack, aws_codebuild as codebuild, aws_ecr as ecr, aws_iam as iam, aws_s3_assets as s3_assets
from constructs import Construct


class OptimizerBuildStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        project_root = Path(__file__).resolve().parents[1]

        self.repository = ecr.Repository(
            self,
            "OptimizerRepository",
            repository_name="or-fleet-optimizer",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=20, description="Retain the 20 newest optimizer images")],
        )

        source_asset = s3_assets.Asset(
            self,
            "OptimizerSource",
            path=str(project_root),
            exclude=[
                ".env",
                "*.env",
                "service-account*.json",
                ".git/*",
                ".github/*",
                ".venv/*",
                "infra/*",
                "scripts/*",
                "lambda/dsql_daily_demand/*",
                "data/*",
                ".pytest_cache/*",
                ".ruff_cache/*",
                "__pycache__/*",
                "**/__pycache__/*",
                "**/.pytest_cache/*",
            ],
        )
        self.image_tag = source_asset.asset_hash[:16]

        self.build_project = codebuild.Project(
            self,
            "OptimizerImageBuild",
            project_name="or-fleet-optimizer-image-build",
            source=codebuild.Source.s3(bucket=source_asset.bucket, path=source_asset.s3_object_key),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
                privileged=True,
            ),
            environment_variables={
                "REPOSITORY_URI": codebuild.BuildEnvironmentVariable(value=self.repository.repository_uri),
                "REPOSITORY_NAME": codebuild.BuildEnvironmentVariable(value=self.repository.repository_name),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=self.image_tag),
            },
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $REPOSITORY_URI",
                                "python -m pip install --quiet -r requirements.txt -r requirements-dev.txt",
                                "PYTHONPATH=src pytest -q",
                            ]
                        },
                        "build": {
                            "commands": [
                                "if aws ecr describe-images --repository-name $REPOSITORY_NAME --image-ids imageTag=$IMAGE_TAG >/dev/null 2>&1; then echo \"Reusing immutable image $REPOSITORY_URI:$IMAGE_TAG\"; else docker build --pull --tag $REPOSITORY_URI:$IMAGE_TAG .; fi",
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "if aws ecr describe-images --repository-name $REPOSITORY_NAME --image-ids imageTag=$IMAGE_TAG >/dev/null 2>&1; then echo \"Image already published\"; else docker push $REPOSITORY_URI:$IMAGE_TAG; fi",
                                "printf '{\"imageUri\":\"%s\"}' $REPOSITORY_URI:$IMAGE_TAG > image-detail.json",
                            ]
                        },
                    },
                    "artifacts": {"files": ["image-detail.json"]},
                }
            ),
        )
        self.repository.grant_pull_push(self.build_project)
        self.build_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecr:DescribeImages"],
                resources=[self.repository.repository_arn],
            )
        )
        source_asset.grant_read(self.build_project)
        self.build_project.add_to_role_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )

        CfnOutput(self, "BuildProjectName", value=self.build_project.project_name)
        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "ImageTag", value=self.image_tag)

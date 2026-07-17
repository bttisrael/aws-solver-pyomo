from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
STACK_NAME = "AwsSolverPyomoGitHubOidc"


def load_dotenv() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def existing_github_provider(iam) -> str:
    providers = iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
    return next(
        (
            item["Arn"]
            for item in providers
            if item["Arn"].endswith("oidc-provider/token.actions.githubusercontent.com")
        ),
        "",
    )


def deploy_stack() -> str:
    load_dotenv()
    region = os.getenv("AWS_DEFAULT_REGION") or "us-east-2"
    session = boto3.Session(region_name=region)
    cloudformation = session.client("cloudformation")
    provider_arn = existing_github_provider(session.client("iam"))
    template_body = (ROOT / "infra" / "github_oidc.yaml").read_text(encoding="utf-8")
    parameters = [
        {"ParameterKey": "GitHubOrganization", "ParameterValue": "bttisrael"},
        {"ParameterKey": "GitHubRepository", "ParameterValue": "aws-solver-pyomo"},
        {"ParameterKey": "ExistingGitHubOidcProviderArn", "ParameterValue": provider_arn},
    ]
    common = {
        "StackName": STACK_NAME,
        "TemplateBody": template_body,
        "Parameters": parameters,
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
    }
    try:
        cloudformation.describe_stacks(StackName=STACK_NAME)
    except ClientError as exc:
        if "does not exist" not in str(exc):
            raise
        cloudformation.create_stack(**common)
        cloudformation.get_waiter("stack_create_complete").wait(StackName=STACK_NAME)
    else:
        try:
            cloudformation.update_stack(**common)
        except ClientError as exc:
            if "No updates are to be performed" not in str(exc):
                raise
        else:
            cloudformation.get_waiter("stack_update_complete").wait(StackName=STACK_NAME)

    stack = cloudformation.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    return next(item["OutputValue"] for item in stack["Outputs"] if item["OutputKey"] == "DeployRoleArn")


if __name__ == "__main__":
    print(deploy_stack())

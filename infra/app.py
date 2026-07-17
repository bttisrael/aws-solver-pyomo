#!/usr/bin/env python3
import aws_cdk as cdk

from or_fleet_stack import OrFleetStack
from optimizer_build_stack import OptimizerBuildStack


app = cdk.App()
environment = cdk.Environment(
    account=app.node.try_get_context("account") or "922981236785",
    region=app.node.try_get_context("region") or "us-east-2",
)
build_stack = OptimizerBuildStack(
    app,
    "OptimizerBuildStack",
    env=environment,
)
OrFleetStack(
    app,
    "OrFleetOptimizationStack",
    image_repository=build_stack.repository,
    image_tag=build_stack.image_tag,
    env=environment,
)
app.synth()


#!/usr/bin/env python3
import aws_cdk as cdk

from or_fleet_stack import OrFleetStack


app = cdk.App()
OrFleetStack(
    app,
    "OrFleetOptimizationStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-2",
    ),
)
app.synth()


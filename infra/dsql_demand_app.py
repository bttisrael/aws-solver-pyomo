#!/usr/bin/env python3
import aws_cdk as cdk

from dsql_demand_stack import DsqlDailyDemandStack


app = cdk.App()
DsqlDailyDemandStack(
    app,
    "DsqlDailyDemandStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account") or "922981236785",
        region=app.node.try_get_context("region") or "us-east-2",
    ),
)
app.synth()

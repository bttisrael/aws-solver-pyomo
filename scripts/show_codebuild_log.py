from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_id")
    args = parser.parse_args()
    load_dotenv()
    region = os.getenv("AWS_DEFAULT_REGION") or "us-east-2"
    build = boto3.client("codebuild", region_name=region).batch_get_builds(ids=[args.build_id])["builds"][0]
    logs = build["logs"]
    events = boto3.client("logs", region_name=region).get_log_events(
        logGroupName=logs["groupName"],
        logStreamName=logs["streamName"],
        startFromHead=True,
    )["events"]
    for event in events:
        print(event["message"])


if __name__ == "__main__":
    main()

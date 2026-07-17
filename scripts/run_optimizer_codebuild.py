from __future__ import annotations

import os
import time
from pathlib import Path

import boto3


ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "or-fleet-optimizer-image-build"


def load_dotenv() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv()
    region = os.getenv("AWS_DEFAULT_REGION") or "us-east-2"
    client = boto3.client("codebuild", region_name=region)
    build = client.start_build(projectName=PROJECT_NAME)["build"]
    build_id = build["id"]
    print(f"Started CodeBuild: {build_id}", flush=True)
    previous = None
    while True:
        current = client.batch_get_builds(ids=[build_id])["builds"][0]
        status = current["buildStatus"]
        phase = current.get("currentPhase", "UNKNOWN")
        marker = (status, phase)
        if marker != previous:
            print(f"status={status} phase={phase}", flush=True)
            previous = marker
        if status not in {"IN_PROGRESS"}:
            print(f"Build finished: {status}", flush=True)
            if status != "SUCCEEDED":
                for item in current.get("phases", []):
                    for context in item.get("contexts", []):
                        print(f"{item['phaseType']}: {context.get('message')}", flush=True)
                raise SystemExit(1)
            return
        time.sleep(5)


if __name__ == "__main__":
    main()

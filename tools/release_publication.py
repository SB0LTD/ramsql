"""Choose publication identity without changing source/artifact versions."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
from release_matrix import metadata


def plan(*, version: str, event: str, ref_type: str, run_id: str, run_attempt: str,
         publish_requested: bool, repository: str, head_repository: str) -> dict:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",version):
        raise ValueError("invalid source version")
    if not all(re.fullmatch(r"[1-9][0-9]*",x) for x in (run_id,run_attempt)):
        raise ValueError("invalid workflow run identity")
    explicit = (event == "push" and ref_type == "tag") or (event == "workflow_dispatch" and publish_requested)
    if not explicit and event not in ("pull_request","workflow_dispatch"):
        raise ValueError("unsupported publication event")
    candidate = not explicit
    tag = f"ci-v{version}-run.{run_id}.{run_attempt}" if candidate else f"v{version}"
    # Fork tokens are read-only. Never elevate a PR checkout with pull_request_target.
    allowed = event != "pull_request" or bool(repository and repository == head_repository)
    return dict(tag=tag,prerelease=candidate or "-" in version,
                publish_allowed=allowed,candidate=candidate)


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    data = plan(version=metadata()["version"],event=os.environ["GITHUB_EVENT_NAME"],
                ref_type=os.environ["GITHUB_REF_TYPE"],run_id=os.environ["GITHUB_RUN_ID"],
                run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
                publish_requested=os.environ.get("REQUESTED_PUBLISH") == "true",
                repository=os.environ["GITHUB_REPOSITORY"],
                head_repository=event.get("pull_request",{}).get("head",{}).get("repo",{}).get("full_name", ""))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a",encoding="utf-8",newline="\n") as output:
        for key,value in data.items():
            output.write(f"{key}={json.dumps(value) if isinstance(value,bool) else value}\n")
    print(json.dumps(data,indent=2))

if __name__ == "__main__":main()

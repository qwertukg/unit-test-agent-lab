"""Resolve official Docker Hub tags without a local Docker daemon; no runtime claims."""
import concurrent.futures
import json
from pathlib import Path

import httpx

IMAGES = {
    "GITLAB_IMAGE": "gitlab/gitlab-ce:19.4.1-ce.0",
    "RUNNER_IMAGE": "gitlab/gitlab-runner:alpine-v19.4.0",
    "POSTGRES_IMAGE": "library/postgres:17.11-bookworm",
    "PYTHON_IMAGE": "library/python:3.13.7-slim-bookworm",
    "DIND_IMAGE": "library/docker:28.5.1-dind",
    "DOCKER_CLI_IMAGE": "library/docker:28.5.1-cli",
    "MAVEN_IMAGE": "library/maven:3.9.11-eclipse-temurin-17",
}


def resolve(item):
    key, image = item
    repo, tag = image.rsplit(":", 1)
    with httpx.Client(timeout=45) as client:
        token = client.get("https://auth.docker.io/token", params={"service": "registry.docker.io", "scope": f"repository:{repo}:pull"}).json()["token"]
        response = client.get(f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"})
        response.raise_for_status()
        data = response.json()
        platforms = [m.get("platform", {}) for m in data.get("manifests", [])]
        return key, {"tag": image.removeprefix("library/"), "digest": response.headers["docker-content-digest"],
                     "platforms": platforms, "verification": "registry manifest only; container not run"}


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result = dict(pool.map(resolve, IMAGES.items()))
    Path("config").mkdir(exist_ok=True)
    Path("config/images.lock.json").write_text(json.dumps(result, indent=2) + "\n")
    Path("config/images.env").write_text("\n".join(f"{k}={v['tag']}@{v['digest']}" for k, v in result.items()) + "\n")
    print(json.dumps({k: v["tag"] for k, v in result.items()}, indent=2))


if __name__ == "__main__":
    main()

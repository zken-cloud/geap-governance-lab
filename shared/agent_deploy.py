"""Deploy an ADK agent to Agent Runtime — shared by every agent in this repo.

Each agent's `deploy.py` is a thin entry point: it declares an `AgentSpec`
(display name, description, its own env vars and extra pins), optionally adds
its own flags, and calls `main()`. Everything below is the same for all of them:
the pinned dependency set, the telemetry environment, the Agent Identity +
gateway configuration, and the staging-directory workaround the runtime image
needs.

    GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) python deploy.py \\
        --update=projects/N/locations/R/reasoningEngines/ID \\
        --agent-gateway=projects/P/locations/R/agentGateways/agent-gateway

Every agent here is deployed with `identity_type=AGENT_IDENTITY`; that is the
point of the project. Omitting `--update` creates a new engine: an identity
shell first, so the principal exists and can be granted IAM, then the code.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

# One version everywhere: the operator pickles the AdkApp with these and the
# container unpickles it with these. Keep in step with each agent's
# pyproject.toml. [mcp] is a required extra since ADK 2.0; [a2a,agent-identity]
# are what registry discovery imports.
PINS = [
    "google-cloud-aiplatform[agent_engines]>=2.1.0,<2.2.0",
    "google-adk[a2a,agent-identity,mcp]==2.9.0",
    "google-auth>=2.0",
    "httpx",
    "cloudpickle",
    "pydantic",
]

# Demo settings, not production ones: prompts and responses are recorded on
# the spans and every trace is kept.
TELEMETRY_ENV = {
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
    "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
    "OTEL_TRACES_SAMPLER_ARG": "1.0",
    # Bound-token sharing must stay off for the agent's own calls to Google APIs.
    "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "false",
}

# The runtime image's Dockerfile runs `.venv/bin/python -m compileall ...` but
# does not create .venv. A bare symlink makes getsitepackages() point at the
# root-owned system site-packages and compileall fails as appuser; a pyvenv.cfg
# makes Python treat .venv/ as a real virtualenv.
_CREATE_VENV_SH = """#!/bin/bash
set -e
PYTHON3=$(which python3)
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
mkdir -p /code/.venv/bin
mkdir -p /code/.venv/lib/python${PY_VER}/site-packages
ln -sf "$PYTHON3" /code/.venv/bin/python
ln -sf "$PYTHON3" /code/.venv/bin/python3
cat > /code/.venv/pyvenv.cfg << PYCFG
home = $(dirname $PYTHON3)
include-system-site-packages = true
PYCFG
"""


@dataclass
class AgentSpec:
    """What differs between agents."""

    display_name: str
    description: str
    agent_dir: str  # directory holding the `agent/` package
    env: Callable[[argparse.Namespace], dict[str, str]] = lambda _args: {}
    add_args: Callable[[argparse.ArgumentParser], None] = lambda _parser: None
    requirements: list[str] = field(default_factory=list)
    min_instances: int = 1


def parse_args(spec: AgentSpec, argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"Deploy {spec.display_name} to Agent Runtime")
    p.add_argument("--project", default=os.environ.get("PROJECT_ID"), required="PROJECT_ID" not in os.environ)
    p.add_argument("--region", default=os.environ.get("REGION", "us-central1"))
    p.add_argument("--staging-bucket", default=None, help="default: gs://PROJECT-staging")
    p.add_argument("--display-name", default=spec.display_name)
    p.add_argument(
        "--update",
        default=None,
        metavar="RESOURCE_NAME",
        help="full reasoningEngines name to update in place",
    )
    p.add_argument(
        "--agent-gateway",
        default=None,
        help="agentGateways resource name to route egress through",
    )
    p.add_argument("--model", default="gemini-3.5-flash")
    p.add_argument(
        "--model-endpoint-location",
        default="global",
        help="GOOGLE_CLOUD_LOCATION for the model endpoint",
    )
    spec.add_args(p)
    return p.parse_args(argv)


def deploy(spec: AgentSpec, args: argparse.Namespace) -> str:
    """Deploy (or update) the agent and return its reasoning-engine resource name."""
    staging_bucket = args.staging_bucket or f"gs://{args.project}-staging"

    # The agent module builds root_agent at import time and reads these then,
    # so they must be set before the import — not just in the container env.
    agent_env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "GOOGLE_CLOUD_LOCATION": args.model_endpoint_location,
        "MODEL_NAME": args.model,
        **spec.env(args),
    }
    os.environ.update(agent_env)
    if spec.agent_dir not in sys.path:
        sys.path.insert(0, spec.agent_dir)

    import vertexai
    from vertexai.agent_engines import AdkApp

    vertexai.init(project=args.project, location=args.region, staging_bucket=staging_bucket)
    client = vertexai.Client(
        project=args.project,
        location=args.region,
        http_options=dict(api_version="v1beta1"),
    )

    root_agent = importlib.import_module("agent.agent").root_agent
    # Agent Runtime's default telemetry pipeline (OTLP to Cloud Trace + GenAI
    # SDK instrumentation) is enabled by the flag; ADK already emits the model
    # and tool spans, so no extra instrumentation is wired in.
    app = AdkApp(agent=root_agent, enable_tracing=True)

    config = dict(
        staging_bucket=staging_bucket,
        requirements=PINS + spec.requirements,
        extra_packages=["agent", "installation_scripts/create_venv.sh"],
        build_options={"installation_scripts": ["installation_scripts/create_venv.sh"]},
        env_vars={**TELEMETRY_ENV, **agent_env},
        display_name=args.display_name,
        description=spec.description,
        min_instances=spec.min_instances,
        resource_limits={"cpu": "4", "memory": "8Gi"},
        identity_type="AGENT_IDENTITY",
    )
    if args.agent_gateway:
        config["agent_gateway_config"] = {"agent_to_anywhere_config": {"agent_gateway": args.agent_gateway}}

    print(f"Deploying {args.display_name}: project={args.project} region={args.region} model={args.model}")
    print(f"  mode={'update ' + args.update if args.update else 'create'} gateway={args.agent_gateway or '-'}")

    staging_dir = tempfile.mkdtemp(prefix="agent_deploy_")
    cwd = os.getcwd()
    try:
        shutil.copytree(
            os.path.join(spec.agent_dir, "agent"),
            os.path.join(staging_dir, "agent"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        scripts_dir = os.path.join(staging_dir, "installation_scripts")
        os.makedirs(scripts_dir)
        script = os.path.join(scripts_dir, "create_venv.sh")
        with open(script, "w") as f:
            f.write(_CREATE_VENV_SH)
        os.chmod(script, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        os.chdir(staging_dir)

        name = args.update
        if not name:
            shell = client.agent_engines.create(
                config={
                    "display_name": args.display_name,
                    "description": spec.description,
                    "identity_type": "AGENT_IDENTITY",
                }
            )
            name = shell.api_resource.name
            print(f"Identity shell created: {name}")
        engine = client.agent_engines.update(name=name, agent=app, config=config)
    finally:
        os.chdir(cwd)
        shutil.rmtree(staging_dir, ignore_errors=True)

    name = engine.api_resource.name
    print(f"Agent {'updated' if args.update else 'deployed'}: {name}")
    if not args.update:
        print(
            "Grant the new principal roles/run.invoker on each Cloud Run service it calls and "
            "roles/iap.egressor on each registry resource it must reach."
        )
    return name


def main(spec: AgentSpec, argv: list[str] | None = None) -> str:
    return deploy(spec, parse_args(spec, argv))

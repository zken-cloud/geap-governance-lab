#!/usr/bin/env bash
#
# Build and push the LangGraph DIY agent image.
#   ./build.sh [TAG]        # default: v1
#
# Then deploy with deploy.sh. Bump the tag rather than mutating one -- the
# Cloud Run revision is keyed on it.

set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:?export PROJECT_ID first}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-gateway-docker}"
IMAGE_NAME="${IMAGE_NAME:-langgraph-agent}"
TAG="${1:-v1}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_NAME}:${TAG}"
cd "$(dirname "$0")"

echo "Building ${IMAGE}"
gcloud builds submit . --project="${PROJECT_ID}" --region="${REGION}" --tag="${IMAGE}"
echo
echo "Image: ${IMAGE}"

#!/usr/bin/env bash
#
# Build and push the Cymbal Partner Services image.
#   ./build.sh [TAG]        # default: v1
#
# Then: cd ../terraform/30-partner-services && terraform apply -var="image_tag=TAG"
# Bump the tag rather than mutating one -- the Cloud Run revision is keyed on it.

set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:?export PROJECT_ID first}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-gateway-docker}"
IMAGE_NAME="${IMAGE_NAME:-cymbal-partner-services}"
TAG="${1:-v1}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_NAME}:${TAG}"
cd "$(dirname "$0")"

echo "Building ${IMAGE}"
gcloud builds submit . --project="${PROJECT_ID}" --region="${REGION}" --tag="${IMAGE}"
echo
echo "Image: ${IMAGE}"

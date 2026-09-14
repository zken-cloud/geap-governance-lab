#!/usr/bin/env bash
#
# Create / update / delete one semanticGovernancePolicies resource.
#
#   policy.sh upsert <project> <location> <policy-id> <payload-file>
#   policy.sh delete <project> <location> <policy-id>
#
# semanticGovernancePolicies has no Terraform resource (aiplatform v1beta1,
# Preview), so the 10-sgp root drives it through terraform_data provisioners.
# `upsert` is idempotent: it creates the policy when absent and otherwise
# PATCHes the mutable fields, which is what a constraint re-word needs.

set -Eeuo pipefail

ACTION="${1:?upsert|delete}"
PROJECT="${2:?project}"
LOCATION="${3:?location}"
POLICY_ID="${4:?policy id}"

BASE="https://${LOCATION}-aiplatform.googleapis.com/v1beta1"
COLLECTION="${BASE}/projects/${PROJECT}/locations/${LOCATION}/semanticGovernancePolicies"
URI="${COLLECTION}/${POLICY_ID}"
TOKEN="$(gcloud auth print-access-token)"

api() {
  curl --fail-with-body --silent --show-error \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-goog-user-project: ${PROJECT}" \
    -H "Content-Type: application/json" "$@"
}

# Every mutating call returns an LRO. Policies settle in seconds, but wait so
# that a failure surfaces as a Terraform error rather than as silent drift.
wait_for_lro() {
  local operation="$1" response done error
  [[ -n "${operation}" && "${operation}" != "null" ]] || return 0
  for _ in $(seq 1 60); do
    response="$(api "${BASE}/${operation}")"
    done="$(jq -r '.done // false' <<<"${response}")"
    if [[ "${done}" == "true" ]]; then
      error="$(jq -r '.error.message // empty' <<<"${response}")"
      [[ -z "${error}" ]] || { echo "${error}" >&2; return 1; }
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for ${operation}" >&2
  return 1
}

exists() { api "${URI}" >/dev/null 2>&1; }

case "${ACTION}" in
  upsert)
    PAYLOAD_FILE="${5:?payload file}"
    if exists; then
      # agent and mcpTools are set at creation; only the human-readable fields
      # and the constraint are patchable.
      wait_for_lro "$(api -X PATCH \
        "${URI}?updateMask=displayName,description,naturalLanguageConstraint" \
        --data "@${PAYLOAD_FILE}" | jq -r '.name // empty')"
      echo "updated ${POLICY_ID}" >&2
    else
      wait_for_lro "$(api -X POST \
        "${COLLECTION}?semanticGovernancePolicyId=${POLICY_ID}" \
        --data "@${PAYLOAD_FILE}" | jq -r '.name // empty')"
      echo "created ${POLICY_ID}" >&2
    fi
    ;;

  delete)
    if exists; then
      wait_for_lro "$(api -X DELETE "${URI}" | jq -r '.name // empty')"
      echo "deleted ${POLICY_ID}" >&2
    fi
    ;;

  *)
    echo "Unknown action: ${ACTION}" >&2
    exit 2
    ;;
esac

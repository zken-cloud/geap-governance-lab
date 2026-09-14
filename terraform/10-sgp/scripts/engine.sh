#!/usr/bin/env bash
#
# Semantic Governance Policy engine helper.
#
#   engine.sh activate <project> <location>   Ensure the engine is ACTIVE.
#   engine.sh read     <project> <location>   Emit {"state":…,"psc_service_attachment":…}
#
# The engine is a singleton per project+location with no Terraform resource
# (aiplatform v1beta1, Preview). It is activated by PATCHing the empty object,
# which returns an LRO; provisioning takes 15-20 minutes on a cold project.
# Deleting it is deliberately not supported here — tearing the engine down
# would strand the PSC endpoint and every policy, so `terraform destroy` leaves
# it ACTIVE and only removes the binding.

set -Eeuo pipefail

ACTION="${1:?activate|read}"
PROJECT="${2:?project}"
LOCATION="${3:?location}"

BASE="https://${LOCATION}-aiplatform.googleapis.com/v1beta1"
URI="${BASE}/projects/${PROJECT}/locations/${LOCATION}/semanticGovernancePolicyEngine"
TOKEN="$(gcloud auth print-access-token)"

api() {
  curl --fail-with-body --silent --show-error \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-goog-user-project: ${PROJECT}" \
    -H "Content-Type: application/json" "$@"
}

state() { api "${URI}" | jq -r '.state // "UNKNOWN"'; }

case "${ACTION}" in
  activate)
    current="$(state)"
    if [[ "${current}" == "INACTIVE" || "${current}" == "FAILED" ]]; then
      echo "SGP engine is ${current}; activating (15-20 min)…" >&2
      api -X PATCH "${URI}?updateMask=SemanticGovernancePolicyEngine" --data '{}' >/dev/null
    fi

    for _ in $(seq 1 180); do
      current="$(state)"
      [[ "${current}" == "ACTIVE" ]] && break
      [[ "${current}" != "FAILED" ]] || { echo "SGP engine entered FAILED" >&2; exit 1; }
      sleep 10
    done

    [[ "${current}" == "ACTIVE" ]] || {
      echo "SGP engine did not become ACTIVE (last state: ${current})" >&2
      exit 1
    }
    echo "SGP engine ACTIVE" >&2
    ;;

  read)
    api "${URI}" | jq '{state: (.state // "UNKNOWN"), psc_service_attachment: (.pscServiceAttachment // "")}'
    ;;

  *)
    echo "Unknown action: ${ACTION}" >&2
    exit 2
    ;;
esac

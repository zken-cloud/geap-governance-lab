/**
 * Layer 1 — Semantic Governance Policies.
 *
 * Not part of the upstream agent-gateway demo. This is the lab's main
 * extension: a self-managed SGP engine binding plus five natural-language
 * policies evaluated by an LLM judge on every governed model call.
 *
 *   Agent Gateway  ──CONTENT_AUTHZ──▶  agent-gateway-sgp-authz
 *                                       service = us-central1.sgp.internal
 *                                            │ private DNS
 *                                            ▼
 *                                       sgp-psc-endpoint (10.21.0.2)
 *                                            │ PSC
 *                                            ▼
 *                                       SGP engine service attachment
 *
 * Two resource kinds have no Terraform provider support (aiplatform v1beta1,
 * Preview) and are driven through provisioners instead: the engine singleton
 * and the policies themselves. Everything else is a first-class resource.
 *
 * Prerequisite: layer 00-base, and `sgp.internal.` present in the gateway's
 * DNS peering domains (set in the base tfvars so a base re-apply does not
 * strip it back out).
 */

locals {
  sgp_hostname   = "${var.region}.${trimsuffix(var.dns_suffix, ".")}"
  scripts        = "${path.module}/scripts"
  policy_payload = "${path.module}/.policies"
}

data "google_project" "this" {
  project_id = var.project_id
}

# ── Network path to the engine ────────────────────────────────────────────

resource "google_compute_subnetwork" "sgp" {
  project       = var.project_id
  name          = var.sgp_subnet_name
  region        = var.region
  network       = var.network_name
  ip_cidr_range = var.sgp_subnet_cidr
  purpose       = "PRIVATE"
}

resource "google_dns_managed_zone" "sgp" {
  project     = var.project_id
  name        = trimsuffix(replace(var.dns_suffix, ".", "-"), "-")
  dns_name    = var.dns_suffix
  description = "Private DNS for Semantic Governance Policy engine"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = "projects/${var.project_id}/global/networks/${var.network_name}"
    }
  }
}

# Vertex writes engine-provisioning traces under this service agent.
resource "google_project_iam_member" "vertex_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# ── The engine singleton ──────────────────────────────────────────────────

# Activation is a no-op when the engine is already ACTIVE, so this is safe to
# re-run. It is deliberately not destroyed on `terraform destroy`.
resource "terraform_data" "engine" {
  triggers_replace = {
    project  = var.project_id
    location = var.region
  }

  provisioner "local-exec" {
    command = "${local.scripts}/engine.sh activate ${var.project_id} ${var.region}"
  }
}

data "external" "engine" {
  program    = ["${local.scripts}/engine.sh", "read", var.project_id, var.region]
  depends_on = [terraform_data.engine]
}

resource "google_compute_address" "sgp_psc" {
  project      = var.project_id
  name         = "sgp-psc-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.sgp.id
  address_type = "INTERNAL"
  purpose      = "GCE_ENDPOINT"
}

# Customer-managed PSC endpoint onto the engine's service attachment. The
# Agent Gateway already reaches this VPC through its own network attachment;
# this is a separate consumer endpoint and must not replace it.
resource "google_compute_forwarding_rule" "sgp_psc" {
  project               = var.project_id
  name                  = "sgp-psc-endpoint"
  region                = var.region
  network               = var.network_name
  ip_address            = google_compute_address.sgp_psc.id
  target                = data.external.engine.result.psc_service_attachment
  load_balancing_scheme = ""

  lifecycle {
    precondition {
      condition     = data.external.engine.result.state == "ACTIVE"
      error_message = "SGP engine is ${data.external.engine.result.state}, not ACTIVE — cannot bind the PSC endpoint yet."
    }
  }
}

resource "google_dns_record_set" "sgp" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.sgp.name
  name         = "${local.sgp_hostname}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.sgp_psc.address]
}

# ── Gateway binding ───────────────────────────────────────────────────────

resource "google_network_services_authz_extension" "sgp" {
  provider = google-beta

  project   = var.project_id
  name      = "${var.gateway_name}-sgp-authz"
  location  = var.region
  service   = local.sgp_hostname
  authority = local.sgp_hostname
  # Empty = LOAD_BALANCING_SCHEME_UNSPECIFIED, which is what an Agent Gateway
  # target requires; the INTERNAL/EXTERNAL_MANAGED values are for real LBs.
  load_balancing_scheme = ""
  timeout               = var.authz_extension_timeout
  fail_open             = var.fail_open

  # Enforcement is the *absence* of the key — an explicit "ENFORCED" value is
  # not accepted by the API.
  metadata = var.enforcement_mode == "DRY_RUN" ? { sgpEnforcementMode = "DRY_RUN" } : {}

  depends_on = [google_dns_record_set.sgp, google_compute_forwarding_rule.sgp_psc]
}

resource "google_network_security_authz_policy" "sgp" {
  provider = google-beta

  project        = var.project_id
  name           = "${var.gateway_name}-sgp-policy"
  location       = var.region
  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = ["projects/${var.project_id}/locations/${var.region}/agentGateways/${var.gateway_name}"]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.sgp.id]
    }
  }

  # Both filters matter. Excluding gRPC keeps MCP session startup out of the
  # judge's path, and restricting to the two model-inference verbs is what
  # keeps latency and judge token cost proportional to actual model calls.
  http_rules {
    to {
      operations {
        paths {
          prefix = "/"
        }
      }
    }

    when = "!request.headers['content-type'].startsWith('application/grpc') && (request.path.endsWith(':generateContent') || request.path.endsWith(':streamGenerateContent'))"
  }
}

# ── The policies ──────────────────────────────────────────────────────────

# One JSON body per policy. Written to disk because the payloads contain the
# constraint prose and pass through a shell.
resource "local_file" "policy" {
  for_each = var.policies

  filename        = "${local.policy_payload}/${each.key}.json"
  file_permission = "0600"

  content = jsonencode(merge(
    {
      displayName               = each.value.display_name
      description               = each.value.description
      naturalLanguageConstraint = each.value.constraint
      agent                     = "projects/${var.project_id}/locations/${var.region}/agents/${var.agent_registry_id}"
    },
    each.value.mcp_server == null ? {} : {
      mcpTools = [{
        mcpServer = "projects/${var.project_id}/locations/${var.region}/mcpServers/${var.mcp_server_ids[each.value.mcp_server]}"
        tools     = [each.value.tool]
      }]
    }
  ))
}

resource "terraform_data" "policy" {
  for_each = var.policies

  # Re-runs the upsert whenever the payload changes — which is how a constraint
  # re-word rolls out. `id` is carried so the destroy provisioner, which cannot
  # read var/each, still knows what to delete.
  triggers_replace = {
    id       = each.key
    project  = var.project_id
    location = var.region
    payload  = local_file.policy[each.key].content
  }

  provisioner "local-exec" {
    command = "${local.scripts}/policy.sh upsert ${self.triggers_replace.project} ${self.triggers_replace.location} ${self.triggers_replace.id} ${local.policy_payload}/${each.key}.json"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "${path.module}/scripts/policy.sh delete ${self.triggers_replace.project} ${self.triggers_replace.location} ${self.triggers_replace.id}"
  }

  depends_on = [terraform_data.engine]
}

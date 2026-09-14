output "engine_state" {
  description = "SGP engine state. Must be ACTIVE for the binding to work."
  value       = data.external.engine.result.state
}

output "sgp_endpoint" {
  description = "Private hostname and IP the Agent Gateway calls for policy evaluation."
  value       = "${local.sgp_hostname} -> ${google_compute_address.sgp_psc.address}"
}

output "enforcement_mode" {
  description = "DRY_RUN logs only; ENFORCED blocks denied tool calls."
  value       = var.enforcement_mode
}

output "policies" {
  description = "Policy IDs managed by this layer."
  value       = sort(keys(var.policies))
}

output "governance_log_command" {
  description = "Where every verdict, rationale and token count lands."
  value       = "gcloud logging read 'logName=\"projects/${var.project_id}/logs/semantic-governance-policy\"' --project=${var.project_id} --limit=20 --format=json"
}

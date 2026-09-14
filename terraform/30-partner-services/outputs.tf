output "service_url" {
  description = "Base URL of the synthetic SaaS."
  value       = google_cloud_run_v2_service.partner.uri
}

output "endpoints" {
  description = "The three partner endpoints, by credential mode."
  value = {
    api_key = "${google_cloud_run_v2_service.partner.uri}/credit-bureau/v1/score"
    two_lo  = "${google_cloud_run_v2_service.partner.uri}/insurance/v1/quote"
    three_lo = {
      accounts     = "${google_cloud_run_v2_service.partner.uri}/openbanking/v1/accounts"
      transactions = "${google_cloud_run_v2_service.partner.uri}/openbanking/v1/transactions"
      income       = "${google_cloud_run_v2_service.partner.uri}/openbanking/v1/income"
    }
  }
}

output "oauth" {
  description = "OAuth2 endpoints to register on the 2LO and 3LO auth providers."
  value = {
    authorization_url  = "${google_cloud_run_v2_service.partner.uri}/oauth/authorize"
    token_url          = "${google_cloud_run_v2_service.partner.uri}/oauth/token"
    two_lo_client_id   = "cymbal-insurance-client"
    three_lo_client_id = "cymbal-openbanking-client"
  }
}

# These are what get vaulted in Auth Manager. Read them with
#   terraform output -raw credit_bureau_api_key
output "credit_bureau_api_key" {
  description = "API key for the API-key auth provider."
  value       = random_password.credit_bureau_api_key.result
  sensitive   = true
}

output "two_lo_client_secret" {
  description = "Client secret for the 2LO auth provider."
  value       = random_password.two_lo_client_secret.result
  sensitive   = true
}

output "three_lo_client_secret" {
  description = "Client secret for the 3LO auth provider."
  value       = random_password.three_lo_client_secret.result
  sensitive   = true
}

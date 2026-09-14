provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

# networkservices resources for Agent Gateway are only exposed on the v1beta1
# surface, matching the upstream agent-gateway module's provider setup.
provider "google-beta" {
  project                          = var.project_id
  region                           = var.region
  network_services_custom_endpoint = "https://networkservices.googleapis.com/v1beta1/"
  user_project_override            = true
  billing_project                  = var.project_id
}

variable "project_id" {
  description = "Project hosting the synthetic partner SaaS."
  type        = string
}

variable "region" {
  description = "Region for the Cloud Run service."
  type        = string
  default     = "us-central1"
}

variable "name" {
  description = "Base name for every resource in this stack."
  type        = string
  default     = "cymbal-partner-services"
}

variable "artifact_registry_repo" {
  description = "Artifact Registry repository holding the image. Created by the base layer."
  type        = string
  default     = "gateway-docker"
}

variable "image_tag" {
  description = <<-EOT
    Tag of the partner-services image. Build it first with ../../partner-services/build.sh.
    Bump this to roll out changes -- the Cloud Run revision is keyed on the tag.
  EOT
  type        = string
  default     = "v3"
}

variable "oauth_redirect_prefixes" {
  description = <<-EOT
    Redirect-URI prefixes the mock OAuth server will accept.

    Both consent paths are allowed on purpose. Auth Manager's 3LO provider posts
    back to its own deterministic oauthcallback under
    agentidentitycredentials.googleapis.com; Gemini Enterprise drives consent
    through vertexaisearch.cloud.google.com/static/oauth/oauth.html. Keeping both
    means we are not blocked whichever way the handshake is driven.
  EOT
  type        = list(string)
  default = [
    "https://agentidentitycredentials.googleapis.com/",
    "https://vertexaisearch.cloud.google.com/",
  ]
}

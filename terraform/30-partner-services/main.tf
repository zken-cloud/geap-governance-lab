/**
 * Layer 3 - Cymbal Partner Services, the synthetic SaaS for the Auth Manager demo.
 *
 * Three mock partner systems, one per credential mode, plus the OAuth2 server
 * backing two of them:
 *
 *   /credit-bureau/v1/score       X-GOOG-API-KEY          -> API-key provider
 *   /insurance/v1/quote           Bearer, client creds    -> 2LO provider
 *   /openbanking/v1/*             Bearer, user consent    -> 3LO provider
 *
 * The credentials this layer generates are exactly what gets vaulted in
 * Auth Manager in layer 40. They are third-party config, not agent secrets:
 * the demo's whole point is that the agent never sees them.
 *
 * Ingress is public and unauthenticated on purpose. Google's token broker
 * (agentidentitycredentials) calls /oauth/token from outside the VPC, and a
 * human browser has to reach /oauth/authorize to consent. Locking this down
 * would break both flows. All data behind it is synthetic.
 */

locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/${var.name}:${var.image_tag}"
}

# Rotating a credential is a deliberate act: bump the keeper, apply, then update
# the matching authProvider. That two-step IS the rotation demo -- the agent is
# never touched, and the call starts failing until the vault catches up.
resource "random_password" "credit_bureau_api_key" {
  length  = 32
  special = false
  keepers = { rotation = "1" }
}

resource "random_password" "two_lo_client_secret" {
  length  = 40
  special = false
  keepers = { rotation = "1" }
}

resource "random_password" "three_lo_client_secret" {
  length  = 40
  special = false
  keepers = { rotation = "1" }
}

# Signs the mock's own tokens. Rotating it invalidates live demo sessions, so it
# is kept out of the rotation story above.
resource "random_password" "oauth_signing_key" {
  length  = 48
  special = false
}

resource "google_service_account" "partner" {
  project      = var.project_id
  account_id   = var.name
  display_name = "Runtime identity for Cymbal Partner Services (synthetic SaaS)"
}

resource "google_cloud_run_v2_service" "partner" {
  project             = var.project_id
  name                = var.name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.partner.email

    # min 1: the OAuth token endpoint is called synchronously inside the agent's
    # turn, and a cold start there reads as a broken demo.
    scaling {
      min_instance_count = 1
      max_instance_count = 4
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "CREDIT_BUREAU_API_KEYS"
        value = random_password.credit_bureau_api_key.result
      }
      env {
        name  = "TWO_LO_CLIENT_ID"
        value = "cymbal-insurance-client"
      }
      env {
        name  = "TWO_LO_CLIENT_SECRET"
        value = random_password.two_lo_client_secret.result
      }
      env {
        name  = "THREE_LO_CLIENT_ID"
        value = "cymbal-openbanking-client"
      }
      env {
        name  = "THREE_LO_CLIENT_SECRET"
        value = random_password.three_lo_client_secret.result
      }
      env {
        name  = "OAUTH_REDIRECT_PREFIXES"
        value = join(",", var.oauth_redirect_prefixes)
      }
      env {
        name  = "OAUTH_SIGNING_KEY"
        value = random_password.oauth_signing_key.result
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 2
        period_seconds        = 3
        failure_threshold     = 10
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Public by necessity -- see the header comment. Each endpoint enforces its own
# credential; there is no unauthenticated data path.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.partner.location
  name     = google_cloud_run_v2_service.partner.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

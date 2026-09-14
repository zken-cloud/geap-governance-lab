terraform {
  required_version = ">= 1.12.2"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.39.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 7.30.0"
    }
    external = {
      source  = "hashicorp/external"
      version = ">= 2.3"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.4"
    }
  }
}

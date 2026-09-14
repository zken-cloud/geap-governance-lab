# State lives beside the other layers, in the project's Terraform state bucket.
#   terraform init -backend-config=backend.conf
terraform {
  backend "gcs" {}
}

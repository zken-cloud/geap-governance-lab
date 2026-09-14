# State lives beside the base layer's, in the project's Terraform state bucket.
#   terraform init -backend-config=backend.conf
terraform {
  backend "gcs" {}
}

variable "project_id" {
  description = "Project holding the Agent Gateway and the SGP engine."
  type        = string
}

variable "region" {
  description = "Region of the Agent Gateway. The SGP engine is a singleton per project+location."
  type        = string
  default     = "us-central1"
}

variable "network_name" {
  description = "VPC created by layer 00-base."
  type        = string
  default     = "gateway-vpc"
}

variable "gateway_name" {
  description = "Agent Gateway the SGP authz policy attaches to."
  type        = string
  default     = "agent-gateway"
}

variable "sgp_subnet_name" {
  description = "Dedicated subnet hosting the PSC endpoint for the SGP engine."
  type        = string
  default     = "sgp-engine-subnet"
}

variable "sgp_subnet_cidr" {
  description = <<-EOT
    Range for sgp_subnet_name. Must not overlap the base-layer subnets, nor
    Agent Gateway's reserved 10.0.0.0/24-10.0.2.0/24.
  EOT
  type        = string
  default     = "10.21.0.0/24"
}

variable "dns_suffix" {
  description = "Private DNS suffix for the SGP endpoint. Trailing dot required."
  type        = string
  default     = "sgp.internal."
}

variable "enforcement_mode" {
  description = <<-EOT
    "DRY_RUN"  — evaluate and log every call, block nothing.
    "ENFORCED" — denials actually block the tool call.

    Start at DRY_RUN, read the semantic-governance-policy log, and only then
    promote. The switch is a metadata edit on the authz extension, so flipping
    this variable is a fast, reversible one-resource change.
  EOT
  type        = string
  default     = "ENFORCED"

  validation {
    condition     = contains(["DRY_RUN", "ENFORCED"], var.enforcement_mode)
    error_message = "enforcement_mode must be DRY_RUN or ENFORCED."
  }
}

variable "authz_extension_timeout" {
  description = <<-EOT
    Call timeout for the SGP authz extension.

    HARD API LIMIT: 10ms-10000ms inclusive. Anything larger is rejected with
    AUTHZ_EXTENSION_TIMEOUT_INVALID. The previous default here was "30s", which
    could never have applied - it was never exercised because this layer had
    not been applied against a live deployment until 2026-08-06.

    10s is the maximum the API allows, and this extension is fail-closed, so a
    timeout that fires blocks the governed model call rather than degrading.
    Judge evaluations run into the seconds and grow with conversation length,
    so take all the headroom available rather than tuning this down.

    The hand-built deployment left this unset and inherited the platform
    default; the provider requires a value, so adoption sets it explicitly.
  EOT
  type        = string
  default     = "10s"

  validation {
    condition     = can(regex("^([0-9]+(\\.[0-9]+)?)s$", var.authz_extension_timeout)) && tonumber(replace(var.authz_extension_timeout, "s", "")) <= 10
    error_message = "authz_extension_timeout must be <= 10s - the API rejects anything larger with AUTHZ_EXTENSION_TIMEOUT_INVALID."
  }
}

variable "fail_open" {
  description = <<-EOT
    false (default) means fail-closed: if the SGP engine is unreachable,
    governed model calls fail rather than silently bypassing policy. This is
    the deliberate posture for this demo and differs from the IAP and Model
    Armor extensions, which are fail-open.
  EOT
  type        = bool
  default     = false
}

variable "agent_registry_id" {
  description = <<-EOT
    Agent Registry agent ID the policies bind to (NOT the reasoning-engine ID).
    Read it from:
      gcloud ... agentregistry .../agents  -> the entry whose displayName is
      the agent, e.g. agentregistry-00000000-0000-0000-xxxx-xxxxxxxxxxxx
  EOT
  type        = string
}

variable "mcp_server_ids" {
  description = "Agent Registry mcpServer IDs, keyed by Cloud Run service name."
  type        = map(string)
}

variable "policies" {
  description = <<-EOT
    The natural-language policies. Each entry becomes one
    semanticGovernancePolicies resource bound to var.agent_registry_id.

    mcp_server = null makes the policy agent-wide. Otherwise it scopes to one
    tool on one MCP server — the API currently accepts at most one tool per
    MCP server per policy, which is why document search and retrieval are two
    separate policies rather than one.

    These constraints contain no secrets, but they are read by an LLM judge
    whose rationales are shown to end users. Review wording with lending,
    privacy, and compliance owners before applying.
  EOT
  type = map(object({
    display_name = string
    description  = string
    constraint   = string
    mcp_server   = optional(string)
    tool         = optional(string)
  }))

  default = {
    mortgage-minimum-necessary = {
      display_name = "Mortgage assistant: minimum necessary access"
      description  = "Agent-wide purpose limitation and human decision boundary"
      # Re-tuned 2026-07-19 after enforcement surfaced false positives: the
      # judge read the original "minimum necessary" wording hyper-literally and
      # denied get_current_time and a 3-year-vs-2-year document search. Every
      # hard prohibition is retained; the last two sentences are judge guidance.
      constraint = "Use tools only in service of the user's current mortgage-assistance request. Do not access or return information belonging to a different applicant than the one the current request concerns. Do not expose credentials or authentication tokens. Do not make or claim to make a final lending, eligibility, approval, denial, pricing, or underwriting decision. Reasonable supporting actions are permitted, including reading the current date or time, searching or retrieving documents with sensibly scoped parameters, and retrying or refining a previous step. Deny a tool call only when it clearly serves a purpose unrelated to the current request or clearly accesses unnecessary sensitive data."
    }

    mortgage-email-approval = {
      display_name = "Mortgage assistant: controlled outbound email"
      description  = "Requires clear user intent and limits sensitive data in outbound email"
      constraint   = "Call send_email only when the user has explicitly asked to send an email or has explicitly approved the recipient, purpose, and material content in the current conversation. Do not include passwords, authentication tokens, full Social Security numbers, full bank account numbers, or information belonging to another applicant."
      mcp_server   = "corporate-email"
      tool         = "send_email"
    }

    mortgage-income-purpose = {
      display_name = "Mortgage assistant: income verification purpose"
      description  = "Limits third-party income verification to the active applicant and task"
      constraint   = "Call verify_applicant only when income verification is necessary for the user's current mortgage-assistance request and the request concerns the active applicant. Do not use the result for an unrelated purpose or claim that the verification itself is a final lending decision."
      mcp_server   = "income-verification"
      tool         = "verify_applicant"
    }

    mortgage-document-search-purpose = {
      display_name = "Mortgage assistant: document search purpose limitation"
      description  = "Limits DMS searches to relevant applicant documents"
      constraint   = "Search only for documents that are directly relevant to the user's current mortgage-assistance request and belong to the active applicant. Do not search for unrelated documents, credentials, authentication tokens, or documents belonging to another applicant."
      mcp_server   = "legacy-dms"
      tool         = "search_documents"
    }

    mortgage-document-get-purpose = {
      display_name = "Mortgage assistant: document retrieval purpose limitation"
      description  = "Limits DMS retrieval to relevant applicant documents"
      constraint   = "Retrieve only a document that is directly relevant to the user's current mortgage-assistance request and belongs to the active applicant. Do not retrieve an unrelated document, credentials, authentication tokens, or a document belonging to another applicant."
      mcp_server   = "legacy-dms"
      tool         = "get_document"
    }
  }
}

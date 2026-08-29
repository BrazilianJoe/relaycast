variable "tenancy_ocid" { type = string }
variable "user_ocid" { type = string }
variable "fingerprint" { type = string }
variable "private_key_path" { type = string }
variable "compartment_ocid" { type = string }
variable "ssh_public_key" { type = string }

variable "region" {
  type    = string
  default = "us-ashburn-1"
}

variable "availability_domain" {
  type    = string
  default = ""
}

variable "image_ocid" {
  type    = string
  default = ""
}

variable "shape" {
  type    = string
  default = "VM.Standard.A1.Flex"
}

variable "ocpus" {
  type    = number
  default = 1
}

variable "memory_gbs" {
  type    = number
  default = 6
}

variable "git_repo" {
  type    = string
  default = "https://github.com/BrazilianJoe/relaycast.git"
}

variable "git_ref" {
  type    = string
  default = "main"
}

variable "admin_user" {
  type    = string
  default = "admin"
}

variable "admin_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "publish_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "ssh_cidr" {
  type    = string
  default = "0.0.0.0/0"
}

variable "admin_cidr" {
  type    = string
  default = "0.0.0.0/0"
}

variable "ingest_cidr" {
  type    = string
  default = "0.0.0.0/0"
}

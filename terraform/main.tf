terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.36.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
  state                    = "AVAILABLE"
}

resource "random_password" "admin" {
  length  = 20
  special = false
}

resource "random_password" "publish" {
  length  = 32
  special = false
}

locals {
  ad           = var.availability_domain != "" ? var.availability_domain : data.oci_identity_availability_domains.ads.availability_domains[0].name
  image_id     = var.image_ocid != "" ? var.image_ocid : data.oci_core_images.ubuntu.images[0].id
  admin_password = var.admin_password != "" ? var.admin_password : random_password.admin.result
  publish_key    = var.publish_key != "" ? var.publish_key : random_password.publish.result
}

resource "oci_core_vcn" "this" {
  cidr_block     = "10.42.0.0/16"
  compartment_id = var.compartment_ocid
  display_name   = "relaycast"
  dns_label      = "relaycast"
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "relaycast-ig"
  enabled        = true
}

resource "oci_core_route_table" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "relaycast-rt"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}

resource "oci_core_security_list" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "relaycast-sl"

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }

  ingress_security_rules {
    protocol = "6"
    source   = var.ssh_cidr
    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = var.admin_cidr
    tcp_options {
      min = 8080
      max = 8080
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = var.ingest_cidr
    tcp_options {
      min = 1935
      max = 1935
    }
  }

  ingress_security_rules {
    protocol = "17"
    source   = var.ingest_cidr
    udp_options {
      min = 8890
      max = 8890
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = var.ingest_cidr
    tcp_options {
      min = 8890
      max = 8890
    }
  }
}

resource "oci_core_subnet" "this" {
  cidr_block        = "10.42.1.0/24"
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.this.id
  display_name      = "relaycast-public"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.this.id
  security_list_ids = [oci_core_security_list.this.id]
}

resource "oci_core_instance" "this" {
  availability_domain = local.ad
  compartment_id      = var.compartment_ocid
  display_name        = "relaycast"
  shape               = var.shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = local.image_id
    boot_volume_size_in_gbs = 50
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.this.id
    assign_public_ip = true
    display_name     = "relaycast-vnic"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      git_repo       = var.git_repo
      git_ref        = var.git_ref
      admin_user     = var.admin_user
      admin_password = local.admin_password
      publish_key    = local.publish_key
    }))
  }
}

output "public_ip" {
  value = oci_core_instance.this.public_ip
}

output "admin_url" {
  value = "http://${oci_core_instance.this.public_ip}:8080"
}

output "rtmp_url" {
  value     = "rtmp://${oci_core_instance.this.public_ip}:1935/${local.publish_key}"
  sensitive = true
}

output "srt_url" {
  value     = "srt://${oci_core_instance.this.public_ip}:8890?streamid=publish:${local.publish_key}&pkt_size=1316&latency=250000"
  sensitive = true
}

output "admin_user" {
  value = var.admin_user
}

output "admin_password" {
  value     = local.admin_password
  sensitive = true
}

output "publish_key" {
  value     = local.publish_key
  sensitive = true
}

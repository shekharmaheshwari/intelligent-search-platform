# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

storage "file" {
  path = "/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr     = "http://0.0.0.0:8200"
disable_mlock = true
ui            = true

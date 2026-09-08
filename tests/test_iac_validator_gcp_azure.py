"""Tests for GCP and Azure static guardrail rules in the IaC validator."""
from __future__ import annotations

from tools.iac_validator import (
    check_static_azure_rules,
    check_static_gcp_rules,
    validate_artifact,
    validate_bundle,
)


def test_gcp_rules_non_compliant():
    bad_gcp = """
    resource "google_container_cluster" "primary" {
      name     = "my-gke-cluster"
      location = "us-central1"
    }

    resource "google_storage_bucket" "static_assets" {
      name     = "my-bucket"
      location = "US"
    }

    resource "google_sql_database_instance" "main" {
      name             = "main-instance"
      database_version = "POSTGRES_15"
      settings {
        tier = "db-f1-micro"
      }
    }
    """
    res = check_static_gcp_rules(bad_gcp)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "gke.public_nodes" in codes
    assert "gcs.missing_public_access_prevention" in codes
    assert "cloudsql.ssl_disabled" in codes


def test_gcp_rules_compliant():
    good_gcp = """
    resource "google_container_cluster" "primary" {
      name     = "my-gke-cluster"
      location = "us-central1"
      private_cluster_config {
        enable_private_nodes = true
      }
    }

    resource "google_storage_bucket" "static_assets" {
      name                     = "my-bucket"
      location                 = "US"
      public_access_prevention = "enforced"
    }

    resource "google_sql_database_instance" "main" {
      name             = "main-instance"
      database_version = "POSTGRES_15"
      settings {
        tier = "db-f1-micro"
        ip_configuration {
          require_ssl = true
        }
      }
    }
    """
    res = check_static_gcp_rules(good_gcp)
    assert res.status == "passed"
    assert len(res.findings) == 0


def test_azure_rules_non_compliant():
    bad_azure = """
    resource "azurerm_kubernetes_cluster" "k8s" {
      name                = "aks-cluster"
      location            = "eastus"
      resource_group_name = "myrg"
      dns_prefix          = "aks"
    }

    resource "azurerm_storage_account" "sa" {
      name                     = "storageacc"
      resource_group_name      = "myrg"
      location                 = "eastus"
      account_tier             = "Standard"
      account_replication_type = "LRS"
      allow_blob_public_access = true
    }

    resource "azurerm_key_vault" "kv" {
      name                = "mykeyvault"
      location            = "eastus"
      resource_group_name = "myrg"
      sku_name            = "standard"
      tenant_id           = "00000000-0000-0000-0000-000000000000"
    }
    """
    res = check_static_azure_rules(bad_azure)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "aks.missing_network_profile" in codes
    assert "storage.public_blob" in codes
    assert "keyvault.purge_protection" in codes


def test_azure_rules_compliant():
    good_azure = """
    resource "azurerm_kubernetes_cluster" "k8s" {
      name                = "aks-cluster"
      location            = "eastus"
      resource_group_name = "myrg"
      dns_prefix          = "aks"
      network_profile {
        network_plugin    = "azure"
        load_balancer_sku = "standard"
      }
    }

    resource "azurerm_storage_account" "sa" {
      name                     = "storageacc"
      resource_group_name      = "myrg"
      location                 = "eastus"
      account_tier             = "Standard"
      account_replication_type = "LRS"
      allow_blob_public_access = false
    }

    resource "azurerm_key_vault" "kv" {
      name                     = "mykeyvault"
      location                 = "eastus"
      resource_group_name      = "myrg"
      sku_name                 = "standard"
      tenant_id                = "00000000-0000-0000-0000-000000000000"
      purge_protection_enabled = true
    }
    """
    res = check_static_azure_rules(good_azure)
    assert res.status == "passed"
    assert len(res.findings) == 0


def test_validate_artifact_triggers_gcp_guardrails():
    content = """
    resource "google_storage_bucket" "b" {
      name     = "insecure-bucket"
      location = "US"
    }
    """
    result = validate_artifact(content, artifact_type="terraform")
    gcp_layer = next((layer for layer in result.layers if layer.name == "gcp-integrity-guardrails"), None)
    assert gcp_layer is not None
    assert gcp_layer.status == "failed"
    assert any(f.code == "gcs.missing_public_access_prevention" for f in gcp_layer.findings)


def test_validate_bundle_triggers_azure_guardrails():
    artifacts = [
        {
            "identifier": "main.tf",
            "type": "terraform",
            "content": """
            resource "azurerm_storage_account" "sa" {
              name                     = "insecuresa"
              resource_group_name      = "rg"
              location                 = "eastus"
              account_tier             = "Standard"
              account_replication_type = "LRS"
              allow_nested_items_to_be_public = true
            }
            """,
        }
    ]
    result = validate_bundle(artifacts)
    azure_layer = next((layer for layer in result.layers if layer.name == "azure-integrity-guardrails"), None)
    assert azure_layer is not None
    assert azure_layer.status == "failed"
    assert any(f.code == "storage.public_blob" for f in azure_layer.findings)

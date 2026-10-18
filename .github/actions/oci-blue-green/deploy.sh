#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

[[ "$OCI_TARGET" == "ford-service" || "$OCI_TARGET" == "ford-mcp" || "$OCI_TARGET" == "ford-scraper-scheduler" ]] \
  || fail "target must be ford-service, ford-mcp, or ford-scraper-scheduler"
[[ "$OCI_OPERATION" == "deploy" || "$OCI_OPERATION" == "rollback" ]] \
  || fail "operation must be deploy or rollback"
if [[ "$OCI_OPERATION" == "deploy" ]]; then
  [[ "$OCI_SOURCE_SHA" =~ ^[a-f0-9]{40}$ ]] || fail "deploy requires the verified 40-character source SHA"
else
  [[ "$OCI_SOURCE_SHA" == "recorded-rollback" ]] || fail "rollback must use the recorded prior image"
fi
[[ -n "$OCI_DEPLOY_HOST" && "$OCI_DEPLOY_HOST" =~ ^[A-Za-z0-9.-]+$ ]] \
  || fail "host must be a hostname or IPv4 address"
[[ -n "$OCI_DEPLOY_USER" && "$OCI_DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] \
  || fail "user must be a valid SSH account name"
[[ -n "$OCI_SSH_PRIVATE_KEY" ]] || fail "ssh-private-key is required"
[[ -n "$OCI_SSH_KNOWN_HOSTS" ]] || fail "ssh-known-hosts is required"
[[ "$OCI_REMOTE_ROOT" == /* && "$OCI_REMOTE_ROOT" != *$'\n'* && "$OCI_REMOTE_ROOT" != *'..'* ]] \
  || fail "remote-root must be a safe absolute path"
[[ "$OCI_REGISTRY_USERNAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail "registry-username is required"
[[ -n "$GHCR_TOKEN" ]] || fail "registry-token is required"

if [[ "$OCI_TARGET" == "ford-service" && "${OCI_SERVICE_CATALOG_TOOLS_ENABLED:-false}" == "true" ]]; then
  [[ "${OCI_SERVICE_CATALOG_JDBC_URL:-}" =~ ^jdbc:postgresql://[^/]+/[^?]+([?].*)?$ ]] \
    || fail "enabled catalog tools require a PostgreSQL catalog JDBC URL"
  [[ "${OCI_SERVICE_CATALOG_USERNAME:-}" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] \
    || fail "enabled catalog tools require a dedicated reader username"
  [[ "${FORD_VAULT_SECRET_OCID_FORD_CATALOG_PASSWORD:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ ]] \
    || fail "enabled catalog tools require a Vault secret OCID for the reader password"
fi

if [[ "$OCI_OPERATION" == "deploy" ]]; then
  [[ "$OCI_IMAGE" =~ ^ghcr\.io/[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] \
    || fail "deploy requires an immutable GHCR image digest"
elif [[ -n "$OCI_IMAGE" && ! "$OCI_IMAGE" =~ ^ghcr\.io/[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
  fail "rollback image, when provided, must be an immutable GHCR image digest"
fi

if [[ "$OCI_TARGET" == "ford-mcp" ]]; then
  [[ "$OCI_MCP_PRIVATE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] \
    || fail "ford-mcp requires the existing VM private IPv4 address"
  python3 -c 'import ipaddress, sys; ipaddress.IPv4Address(sys.argv[1])' "$OCI_MCP_PRIVATE_IP" >/dev/null 2>&1 \
    || fail "ford-mcp requires a valid existing VM private IPv4 address"
  [[ "${OCI_SECRETS_PROVIDER:-}" == "oci-vault" ]] \
    || fail "ford-mcp requires OCI Vault runtime secret references"
  [[ "${OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ \
    && "${OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ ]] \
    || fail "ford-mcp requires OCI Vault runtime secret references"
  if [[ "$OCI_OPERATION" == "deploy" ]]; then
    [[ "${OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID:-}" =~ ^[A-Za-z0-9._-]+$ \
      && "${OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ ]] \
      || fail "ford-mcp deploy requires OCI Vault credentials for the public endpoint smoke check"
  fi
elif [[ "$OCI_TARGET" == "ford-service" ]]; then
  [[ "$OCI_DATA_PRIVATE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] \
    || fail "ford-service requires the existing data VM private IPv4 address"
  python3 -c 'import ipaddress, sys; ipaddress.IPv4Address(sys.argv[1])' "$OCI_DATA_PRIVATE_IP" >/dev/null 2>&1 \
    || fail "ford-service requires a valid existing data VM private IPv4 address"
  [[ "${OCI_OPERATOR_USERNAME:-}" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] \
    || fail "ford-service requires a valid operator login email"
  meta_delivery_enabled="${OCI_META_DELIVERY_ENABLED:-false}"
  outbox_enabled="${OCI_OUTBOX_ENABLED:-false}"
  [[ "$meta_delivery_enabled" == "true" || "$meta_delivery_enabled" == "false" ]] \
    || fail "Ford Service Meta delivery flag must be true or false"
  [[ "$outbox_enabled" == "true" || "$outbox_enabled" == "false" ]] \
    || fail "Ford Service outbox flag must be true or false"
  if [[ "$meta_delivery_enabled" == "true" ]]; then
    [[ "$outbox_enabled" == "true" ]] \
      || fail "WhatsApp delivery requires the Ford Service outbox to be enabled"
    [[ "${OCI_META_PHONE_NUMBER_ID:-}" =~ ^[0-9]+$ ]] \
      || fail "WhatsApp delivery requires a numeric phone number ID"
    [[ "${OCI_META_WHATSAPP_DELIVERY_ENDPOINT:-}" =~ ^https://graph\.facebook\.com/v[0-9]+(\.[0-9]+)?/([0-9]+)/messages$ ]] \
      || fail "WhatsApp delivery requires an official Graph API messages endpoint"
    [[ "${BASH_REMATCH[2]}" == "$OCI_META_PHONE_NUMBER_ID" ]] \
      || fail "WhatsApp endpoint phone number ID must match the configured phone number ID"
    [[ "${OCI_META_APP_SECRET_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ \
      && "${OCI_META_VERIFY_TOKEN_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ \
      && "${OCI_META_WHATSAPP_ACCESS_TOKEN_SECRET_OCID:-}" =~ ^ocid1[.]vaultsecret[.][A-Za-z0-9.]+$ ]] \
      || fail "WhatsApp delivery requires the Meta app, verify, and access-token Vault references"
  fi
elif [[ "$OCI_TARGET" == "ford-scraper-scheduler" ]]; then
  [[ "${OCI_SECRETS_PROVIDER:-}" == "oci-vault" ]] \
    || fail "ford-scraper-scheduler requires OCI Vault runtime secret references"
fi
if [[ "$OCI_OPERATION" == "deploy" ]]; then
  if [[ "$OCI_TARGET" == "ford-service" || "$OCI_TARGET" == "ford-scraper-scheduler" ]]; then
    [[ -n "${OCI_BACKUP_HOST:-}" && "$OCI_BACKUP_HOST" =~ ^[A-Za-z0-9.-]+$ ]] \
      || fail "$OCI_TARGET deploy requires the existing data VM backup host"
    [[ -n "${OCI_BACKUP_SSH_PRIVATE_KEY:-}" ]] \
      || fail "backup-ssh-private-key is required for ford-service deploy"
  else
    [[ -n "${OCI_BACKUP_HOST:-}" && "$OCI_BACKUP_HOST" =~ ^[A-Za-z0-9.-]+$ ]] \
      || fail "ford-mcp deploy requires the existing data VM backup host"
  fi
  if [[ -n "${OCI_BACKUP_USER:-}" ]]; then
    [[ "$OCI_BACKUP_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || fail "backup-user must be a valid SSH account name"
  fi
fi

if [[ "${OCI_ACTION_VALIDATE_ONLY:-false}" == "true" ]]; then
  exit 0
fi

if [[ "$OCI_TARGET" == "ford-service" ]]; then
  [[ -n "$OCI_GRAFANA_CLOUD_OTLP_ENDPOINT" ]] || fail "ford-service requires the Grafana Cloud OTLP endpoint"
  [[ -n "$OCI_GRAFANA_CLOUD_INSTANCE_ID" ]] || fail "ford-service requires the Grafana Cloud instance ID"
  [[ "$OCI_GRAFANA_CLOUD_API_TOKEN_SECRET_OCID" =~ ^ocid1\.vaultsecret\. ]] || fail "ford-service requires the Grafana Cloud API token Vault secret OCID"
  [[ -n "$OCI_LANGFUSE_OTLP_ENDPOINT" ]] || fail "ford-service requires the Langfuse OTLP endpoint"
  [[ -n "$OCI_LANGFUSE_PUBLIC_KEY" ]] || fail "ford-service requires the Langfuse public key"
  [[ "$OCI_LANGFUSE_SECRET_KEY_SECRET_OCID" =~ ^ocid1\.vaultsecret\. ]] || fail "ford-service requires the Langfuse secret key Vault secret OCID"
  [[ -f "$OCI_SERVICE_SOURCE_ROOT/deployment/observability/otel-collector/grafana-cloud-config.yaml" ]] \
    || fail "ford-service requires the checked-out Collector configuration"
fi

ACTION_ROOT="$(cd "$(dirname "$0")" && pwd)"
REPOSITORY_ROOT="$(cd "$ACTION_ROOT/../../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT

python3 -m venv "$TEMP_ROOT/venv"
"$TEMP_ROOT/venv/bin/pip" install --disable-pip-version-check --quiet 'ansible-core==2.21.4'
mkdir -p "$TEMP_ROOT/collections"
ANSIBLE_COLLECTIONS_PATH="$TEMP_ROOT/collections" \
  "$TEMP_ROOT/venv/bin/ansible-galaxy" collection install \
  --requirements-file "$REPOSITORY_ROOT/deployment/ansible/requirements.yml"

install -d -m 0700 "$TEMP_ROOT/ssh"
printf '%s\n' "$OCI_SSH_PRIVATE_KEY" > "$TEMP_ROOT/ssh/deploy_key"
printf '%s\n' "$OCI_SSH_KNOWN_HOSTS" > "$TEMP_ROOT/ssh/known_hosts"
chmod 0600 "$TEMP_ROOT/ssh/deploy_key" "$TEMP_ROOT/ssh/known_hosts"
if [[ -n "${OCI_BACKUP_SSH_PRIVATE_KEY:-}" ]]; then
  printf '%s\n' "$OCI_BACKUP_SSH_PRIVATE_KEY" > "$TEMP_ROOT/ssh/backup_key"
  chmod 0600 "$TEMP_ROOT/ssh/backup_key"
fi

python3 - "$TEMP_ROOT/inventory.json" "$TEMP_ROOT/backup-inventory.json" "$TEMP_ROOT/extra-vars.json" "$TEMP_ROOT/playbook-path" <<'PY'
import json
import os
import sys

target = os.environ["OCI_TARGET"]
operation = os.environ["OCI_OPERATION"]
group = "app" if target == "ford-service" else "data"
host_vars = {
    "ansible_host": os.environ["OCI_DEPLOY_HOST"],
    "ansible_user": os.environ["OCI_DEPLOY_USER"],
    "remote_root": os.environ["OCI_REMOTE_ROOT"],
}
if target == "ford-mcp":
    host_vars["emme_2_private_ip"] = os.environ["OCI_MCP_PRIVATE_IP"]
elif target == "ford-service":
    host_vars["emme_2_private_ip"] = os.environ["OCI_DATA_PRIVATE_IP"]

inventory = {"all": {"children": {group: {"hosts": {"release-target": host_vars}}}}}
if target in ("ford-service", "ford-mcp", "ford-scraper-scheduler") and operation == "deploy":
    backup_host_vars = {
        "ansible_host": os.environ["OCI_BACKUP_HOST"],
        "ansible_user": os.environ.get("OCI_BACKUP_USER") or os.environ["OCI_DEPLOY_USER"],
        "remote_root": os.environ["OCI_REMOTE_ROOT"],
    }
    backup_inventory = {"all": {"children": {"data": {"hosts": {"backup-target": backup_host_vars}}}}}
else:
    backup_inventory = inventory
extra_vars = {
    "remote_root": os.environ["OCI_REMOTE_ROOT"],
    "ghcr_username": os.environ["OCI_REGISTRY_USERNAME"],
    "release_summary_path": os.path.join(os.environ["RUNNER_TEMP"], "oci-release-state.json"),
}
if target == "ford-service":
    extra_vars["app_operation"] = operation
    extra_vars["ford_catalog_tools_enabled"] = os.environ.get("OCI_SERVICE_CATALOG_TOOLS_ENABLED", "false")
    extra_vars["ford_catalog_jdbc_url"] = os.environ.get("OCI_SERVICE_CATALOG_JDBC_URL", "")
    extra_vars["ford_catalog_username"] = os.environ.get("OCI_SERVICE_CATALOG_USERNAME", "")
    extra_vars["ford_catalog_password_secret_ocid"] = os.environ.get("FORD_VAULT_SECRET_OCID_FORD_CATALOG_PASSWORD", "")
    extra_vars.update({
        "service_source_root": os.environ["OCI_SERVICE_SOURCE_ROOT"],
        "ford_operator_username": os.environ["OCI_OPERATOR_USERNAME"],
        "ford_meta_delivery_enabled": os.environ.get("OCI_META_DELIVERY_ENABLED", "false") == "true",
        "ford_outbox_enabled": os.environ.get("OCI_OUTBOX_ENABLED", "false") == "true",
        "ford_meta_whatsapp_delivery_endpoint": os.environ.get("OCI_META_WHATSAPP_DELIVERY_ENDPOINT", ""),
        "ford_meta_provider_account_id": os.environ.get("OCI_META_PHONE_NUMBER_ID", ""),
        "ford_meta_app_secret_ocid": os.environ.get("OCI_META_APP_SECRET_OCID", ""),
        "ford_meta_verify_token_ocid": os.environ.get("OCI_META_VERIFY_TOKEN_OCID", ""),
        "ford_meta_whatsapp_access_token_secret_ocid": os.environ.get("OCI_META_WHATSAPP_ACCESS_TOKEN_SECRET_OCID", ""),
        "grafana_cloud_otlp_endpoint": os.environ["OCI_GRAFANA_CLOUD_OTLP_ENDPOINT"],
        "grafana_cloud_instance_id": os.environ["OCI_GRAFANA_CLOUD_INSTANCE_ID"],
        "grafana_cloud_api_token_secret_ocid": os.environ["OCI_GRAFANA_CLOUD_API_TOKEN_SECRET_OCID"],
        "langfuse_otlp_endpoint": os.environ["OCI_LANGFUSE_OTLP_ENDPOINT"],
        "langfuse_public_key": os.environ["OCI_LANGFUSE_PUBLIC_KEY"],
        "langfuse_secret_key_secret_ocid": os.environ["OCI_LANGFUSE_SECRET_KEY_SECRET_OCID"],
    })
    if os.environ["OCI_IMAGE"]:
        extra_vars["app_image"] = os.environ["OCI_IMAGE"]
    playbook = "deployment/ansible/app-blue-green.yml"
elif target == "ford-mcp":
    extra_vars.update({
        "mcp_operation": operation,
        "mcp_secrets_provider": os.environ["OCI_SECRETS_PROVIDER"],
        "mcp_postgres_password_secret_ocid": os.environ["OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID"],
        "mcp_http_bearer_token_secret_ocid": os.environ["OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID"],
        "mcp_public_smoke_url": "https://ford-mcp.emme.business/mcp",
        "mcp_public_smoke_access_client_id": os.environ["OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID"],
        "mcp_public_smoke_access_client_secret_secret_ocid": os.environ["OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID"],
        "mcp_public_smoke_token_secret_ocid": os.environ["OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID"],
    })
    if os.environ["OCI_IMAGE"]:
        extra_vars["mcp_image"] = os.environ["OCI_IMAGE"]
    playbook = "deployment/ansible/mcp-blue-green.yml"
else:
    extra_vars["scheduler_operation"] = operation
    if os.environ["OCI_IMAGE"]:
        extra_vars["scheduler_image"] = os.environ["OCI_IMAGE"]
    playbook = "deployment/ansible/scraper-scheduler-blue-green.yml"

for path, value in ((sys.argv[1], inventory), (sys.argv[2], backup_inventory), (sys.argv[3], extra_vars)):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file)
with open(sys.argv[4], "w", encoding="utf-8") as file:
    file.write(playbook)
PY

PLAYBOOK="$(cat "$TEMP_ROOT/playbook-path")"
export ANSIBLE_CONFIG="$REPOSITORY_ROOT/deployment/ansible/ansible.cfg"
export ANSIBLE_COLLECTIONS_PATH="$TEMP_ROOT/collections"
export ANSIBLE_ROLES_PATH="$REPOSITORY_ROOT/deployment/ansible/roles"
export ANSIBLE_HOST_KEY_CHECKING=True
export ANSIBLE_DISPLAY_ARGS_TO_STDOUT=false
export ANSIBLE_SSH_ARGS="-o UserKnownHostsFile=$TEMP_ROOT/ssh/known_hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes"

cd "$REPOSITORY_ROOT"
if [[ "$OCI_OPERATION" == "deploy" ]]; then
  backup_key="$TEMP_ROOT/ssh/deploy_key"
  if [[ "$OCI_TARGET" == "ford-service" && -n "${OCI_BACKUP_SSH_PRIVATE_KEY:-}" ]]; then
    backup_key="$TEMP_ROOT/ssh/backup_key"
  fi
  "$TEMP_ROOT/venv/bin/ansible-playbook" \
    -i "$TEMP_ROOT/backup-inventory.json" \
    --private-key "$backup_key" \
    --extra-vars "@$TEMP_ROOT/extra-vars.json" \
    deployment/ansible/backup-now.yml
  OCI_BACKUP_RESULT=verified
else
  OCI_BACKUP_RESULT=not-required-schema-unchanged
fi

if ! "$TEMP_ROOT/venv/bin/ansible-playbook" \
  -i "$TEMP_ROOT/inventory.json" \
  --private-key "$TEMP_ROOT/ssh/deploy_key" \
  --extra-vars "@$TEMP_ROOT/extra-vars.json" \
  "$PLAYBOOK"; then
  {
    printf '%s\n' '## OCI deployment result'
    printf '%s\n' "- Target: $OCI_TARGET"
    printf '%s\n' "- Operation: $OCI_OPERATION"
    printf '%s\n' "- Source SHA: $OCI_SOURCE_SHA"
    printf '%s\n' "- Pre-release backup: $OCI_BACKUP_RESULT"
    printf '%s\n' '- Result: failed; inspect the redacted Ansible rollback result above.'
  } >> "$GITHUB_STEP_SUMMARY"
  exit 1
fi

if [[ "$OCI_OPERATION" == "deploy" ]]; then
  if ! "$TEMP_ROOT/venv/bin/ansible-playbook" \
    -i "$TEMP_ROOT/inventory.json" \
    --private-key "$TEMP_ROOT/ssh/deploy_key" \
    --extra-vars "@$TEMP_ROOT/extra-vars.json" \
    deployment/ansible/mcp-public-smoke.yml; then
    rollback_result=not-applicable
    if [[ "$OCI_TARGET" == "ford-mcp" ]]; then
      rollback_vars="$TEMP_ROOT/mcp-rollback-vars.json"
      python3 - "$TEMP_ROOT/extra-vars.json" "$rollback_vars" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    values = json.load(source)
values["mcp_operation"] = "rollback"
values.pop("mcp_image", None)
with open(sys.argv[2], "w", encoding="utf-8") as destination:
    json.dump(values, destination)
PY
      if "$TEMP_ROOT/venv/bin/ansible-playbook" \
        -i "$TEMP_ROOT/inventory.json" \
        --private-key "$TEMP_ROOT/ssh/deploy_key" \
        --extra-vars "@$rollback_vars" \
        deployment/ansible/mcp-blue-green.yml; then
        rollback_result=passed
      else
        rollback_result=failed
      fi
    fi
    {
      printf '%s\n' '## OCI deployment result'
      printf '%s\n' "- Target: $OCI_TARGET"
      printf '%s\n' "- Operation: $OCI_OPERATION"
      printf '%s\n' "- Source SHA: $OCI_SOURCE_SHA"
      printf '%s\n' '- Result: public MCP smoke check failed.'
      printf '%s\n' "- MCP route rollback: $rollback_result"
  if [[ "$OCI_TARGET" == "ford-service" || "$OCI_TARGET" == "ford-scraper-scheduler" ]]; then
        printf '%s\n' '- Ford Service slot remains deployed; its blue-green health checks passed.'
      fi
    } >> "$GITHUB_STEP_SUMMARY"
    exit 1
  fi
fi

python3 - "$RUNNER_TEMP/oci-release-state.json" "$OCI_TARGET" "$OCI_OPERATION" "$OCI_IMAGE" "$OCI_SOURCE_SHA" "$GITHUB_STEP_SUMMARY" <<'PY'
import json
import sys

state_path, target, operation, requested_image, source_sha, summary_path = sys.argv[1:]
with open(state_path, encoding="utf-8") as file:
    state = json.load(file)
image = state["active_image"]
if requested_image and operation == "deploy" and image != requested_image:
    raise SystemExit("deployment state image did not match the requested immutable digest")

lines = [
    "## OCI deployment result",
    f"- Target: {target}",
    f"- Operation: {operation}",
    f"- Source SHA: {source_sha}",
    f"- Active image reference: `{image}`",
    f"- Active slot: `{state['active_slot']}`",
    f"- Prior image reference: `{state.get('previous_image') or 'none'}`",
    "- Candidate and stable health checks: passed",
    "- Pre-release database backup: passed" if operation == "deploy" else "- Pre-release database backup: not required for rollback",
    "- Catalog migration: passed" if target == "ford-scraper-scheduler" and operation == "deploy" else "- Catalog migration: not run",
    "- MCP one-shot migration: passed" if target == "ford-mcp" and operation == "deploy" else "- MCP migration: not run",
    "- Rollback: operator rollback health checks passed" if operation == "rollback" else "- Automatic route restoration: armed for failed post-switch checks",
]
with open(summary_path, "a", encoding="utf-8") as file:
    file.write("\n".join(lines) + "\n")
PY

#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

[[ "$OCI_TARGET" == "ford-service" || "$OCI_TARGET" == "ford-mcp" ]] \
  || fail "target must be ford-service or ford-mcp"
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
fi
if [[ "$OCI_OPERATION" == "deploy" ]]; then
  if [[ "$OCI_TARGET" == "ford-service" ]]; then
    [[ -n "${OCI_BACKUP_HOST:-}" && "$OCI_BACKUP_HOST" =~ ^[A-Za-z0-9.-]+$ ]] \
      || fail "ford-service deploy requires the existing data VM backup host"
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

inventory = {"all": {"children": {group: {"hosts": {"release-target": host_vars}}}}}
if target in ("ford-service", "ford-mcp") and operation == "deploy":
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
    if os.environ["OCI_IMAGE"]:
        extra_vars["app_image"] = os.environ["OCI_IMAGE"]
    playbook = "deployment/ansible/app-blue-green.yml"
else:
    extra_vars["mcp_operation"] = operation
    if os.environ["OCI_IMAGE"]:
        extra_vars["mcp_image"] = os.environ["OCI_IMAGE"]
    playbook = "deployment/ansible/mcp-blue-green.yml"

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
    f"- Active image digest: `{image}`",
    f"- Active slot: `{state['active_slot']}`",
    f"- Prior image digest: `{state.get('previous_image') or 'none'}`",
    "- Candidate and stable health checks: passed",
    "- Pre-release database backup: passed" if operation == "deploy" else "- Pre-release database backup: not required for rollback",
    "- MCP one-shot migration: passed" if target == "ford-mcp" and operation == "deploy" else "- MCP migration: not run",
    "- Rollback: operator rollback health checks passed" if operation == "rollback" else "- Automatic route restoration: armed for failed post-switch checks",
]
with open(summary_path, "a", encoding="utf-8") as file:
    file.write("\n".join(lines) + "\n")
PY

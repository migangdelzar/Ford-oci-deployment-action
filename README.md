# Ford OCI deployment action

Composite GitHub Action for deploying or rolling back the Ford Service and
Ford MCP containers on their existing OCI VMs. It packages the Ansible
playbooks it runs.

Callers must use a reviewed, full 40-character commit SHA and run this action
inside their own protected production environment. Give the job only
`packages: read` permission for its short-lived GHCR pull token. Keep app,
database, model-provider, Meta, and observability credentials on the target
runtime; secret values are not action inputs. The Service caller passes
observability endpoints, public identifiers, and Vault secret OCIDs. The action
reads Grafana and Langfuse credentials directly from OCI Vault on emme-1 using
the VM's Instance Principal.

```yaml
permissions:
  contents: read
  packages: read

steps:
  - name: Deploy Ford MCP
    uses: migangdelzar/Ford-oci-deployment-action/.github/actions/oci-blue-green@<reviewed-full-commit-sha>
    with:
      target: ford-mcp
      image: ghcr.io/migangdelzar/ford-scraping@sha256:<verified-image-digest>
      source-sha: <verified-40-character-source-sha>
      host: ${{ vars.OCI_MCP_HOST }}
      user: ${{ vars.OCI_DEPLOY_USER }}
      ssh-private-key: ${{ secrets.OCI_MCP_SSH_PRIVATE_KEY }}
      ssh-known-hosts: ${{ secrets.OCI_SSH_KNOWN_HOSTS }}
      registry-username: ${{ github.actor }}
      registry-token: ${{ secrets.GITHUB_TOKEN }}
      mcp-private-ip: ${{ vars.OCI_MCP_PRIVATE_IP }}
```

Both Ford Service and Ford MCP deploys require `backup-host` so the action
runs and verifies the existing PostgreSQL and Redis backup before applying
release changes. Ford Service also requires `backup-ssh-private-key` because
its backup runs on emme-2; MCP already runs on emme-2 and uses its deployment
SSH key by default. Ford Service must pass `data-private-ip` for the existing
emme-2 address so Ansible can render PostgreSQL and Redis endpoints; the MCP
target continues to use `mcp-private-ip`. `operation: rollback` restores only the previously
recorded immutable image; it does not revert database migrations.

Ford Service callers must check out the Service `main` branch in the deployment
job because the Collector configuration is stored there. Configure production
environment variables for `GRAFANA_CLOUD_OTLP_ENDPOINT`,
`GRAFANA_CLOUD_INSTANCE_ID`, `LANGFUSE_OTLP_ENDPOINT`, `LANGFUSE_PUBLIC_KEY`,
`FORD_VAULT_SECRET_OCID_GRAFANA_CLOUD_API_TOKEN`, and
`FORD_VAULT_SECRET_OCID_LANGFUSE_SECRET_KEY`. The emme-1 Instance Principal
must have `read secret-bundles` access to those two Vault secrets. Secret values
are fetched on the VM and written only to the mode-0600 Collector environment
file; Ansible suppresses their command and task output.

## Local checks

```bash
python3 -m unittest discover -s tests -v
bash -n .github/actions/oci-blue-green/deploy.sh
```

No OCI deployment credentials, customer data, or Terraform state belong in
this repository.

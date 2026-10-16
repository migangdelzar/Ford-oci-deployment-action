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
the VM's Instance Principal. It also requires the existing Service operator
login email as the non-secret `operator-username` input; the action validates
it and passes it to the runtime template instead of inventing a username.
The Service caller must also pass the production MCP settings through
`service-mcp-enabled`, `service-mcp-url`, and `service-mcp-access-client-id`,
plus the MCP bearer-token and Cloudflare Access client-secret Vault OCIDs.
MCP is required for volatile vehicle facts, so the action rejects Service
rollouts with disabled MCP, a non-production MCP host, a missing Access client
ID, or either missing Vault reference. The values rendered to the Service
environment are the Vault references; secret values remain in OCI Vault and
are resolved by the Service's OCI Vault property loader.
For Ford Service, pass the `ui-upstream-url` input to select the Cloudflare
Pages origin. It defaults to `https://emme-crm-ui.pages.dev`; production callers
can set it from the `FORD_WEB_UI_UPSTREAM_URL` environment variable to preserve
an explicit rollback override.

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
target continues to use `mcp-private-ip`. `operation: rollback` restores only
the previously recorded image; it does not revert database migrations. When a
first blue-green deployment replaces a legacy container that has no registry
digest, the action snapshots the running legacy container into a local-only
image on the VM. This preserves the exact runtime configuration needed by that
older image, including credentials already present in its container config.
The snapshot stays on the VM and is never pushed to GHCR. The action records its
local tag as the rollback image, verifies it exists before use, and does not try
to pull it from GHCR.

Ford Service callers must check out the Service `main` branch in the deployment
job because the Collector configuration is stored there. Configure production
environment variables for `GRAFANA_CLOUD_OTLP_ENDPOINT`,
`GRAFANA_CLOUD_INSTANCE_ID`, `LANGFUSE_OTLP_ENDPOINT`, `LANGFUSE_PUBLIC_KEY`,
`FORD_OPERATOR_USERNAME`,
`FORD_VAULT_SECRET_OCID_GRAFANA_CLOUD_API_TOKEN`, and
`FORD_VAULT_SECRET_OCID_LANGFUSE_SECRET_KEY`. The emme-1 Instance Principal
must have `read secret-bundles` access to those two Vault secrets. Secret values
are fetched on the VM and written only to the mode-0600 Collector environment
file; Ansible suppresses their command and task output.
Also configure `FORD_MCP_ENABLED=true`,
`FORD_MCP_URL=https://ford-mcp.emme.business`, `FORD_MCP_ACCESS_CLIENT_ID`,
`FORD_VAULT_SECRET_OCID_FORD_MCP_TOKEN`, and
`FORD_VAULT_SECRET_OCID_FORD_MCP_ACCESS_CLIENT_SECRET` in the protected Service
production environment. The emme-1 Instance Principal needs `read secret-bundles`
access to both MCP Vault secrets.

Every deploy runs a read-only smoke against `https://ford-mcp.emme.business/mcp`
from the target VM after the blue-green route switch. It initializes the
Streamable HTTP session, checks the production tool list, and performs a small
`search_vehicles` call. The bearer token and Cloudflare Access client secret
are read from OCI Vault on that VM; the action never accepts their values, and
Ansible hides all secret reads and authenticated HTTP responses. The Service
deploy reuses its MCP Vault references. An MCP deploy also needs
`mcp-public-smoke-access-client-id` and
`mcp-public-smoke-access-client-secret-secret-ocid`, and emme-2's Instance
Principal needs `read secret-bundles` access to the Access secret as well as the
existing MCP bearer-token secret. If an MCP deployment's public smoke fails,
the action runs the existing MCP blue-green rollback and fails the release;
the Service image is left deployed if only its dependency smoke fails.

## Local checks

```bash
python3 -m unittest discover -s tests -v
bash -n .github/actions/oci-blue-green/deploy.sh
```

No OCI deployment credentials, customer data, or Terraform state belong in
this repository.

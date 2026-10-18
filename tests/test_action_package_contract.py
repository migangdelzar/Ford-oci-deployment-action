import unittest
import os
import subprocess
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / ".github/actions/oci-blue-green/deploy.sh"


class ActionPackageContractTest(unittest.TestCase):
    def validate_action(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "OCI_TARGET": "ford-service",
                "OCI_IMAGE": "ghcr.io/example/ford-service@sha256:" + "a" * 64,
                "OCI_OPERATION": "deploy",
                "OCI_SOURCE_SHA": "c" * 40,
                "OCI_DEPLOY_HOST": "203.0.113.10",
                "OCI_DEPLOY_USER": "deploy",
                "OCI_SSH_PRIVATE_KEY": "PRIVATE-KEY-SHOULD-NOT-LEAK",
                "OCI_SSH_KNOWN_HOSTS": "203.0.113.10 ssh-ed25519 PUBLIC-HOST-KEY",
                "OCI_BACKUP_SSH_PRIVATE_KEY": "BACKUP-PRIVATE-KEY-SHOULD-NOT-LEAK",
                "OCI_REGISTRY_USERNAME": "release-bot",
                "GHCR_TOKEN": "REGISTRY-TOKEN-SHOULD-NOT-LEAK",
                "OCI_REMOTE_ROOT": "/opt/ford",
                "OCI_BACKUP_HOST": "203.0.113.11",
                "OCI_BACKUP_USER": "deploy",
                "OCI_MCP_PRIVATE_IP": "10.0.0.2",
                "OCI_DATA_PRIVATE_IP": "10.0.0.3",
                "OCI_OPERATOR_USERNAME": "operator@example.test",
                "OCI_ACTION_VALIDATE_ONLY": "true",
                "OCI_SECRETS_PROVIDER": "oci-vault",
                "OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_SCHEDULER_POSTGRES_PASSWORD_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID": "mcp-access-id.example",
                "OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            ["bash", str(DEPLOY_SCRIPT)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_target_and_immutable_image_pass_input_validation(self):
        result = self.validate_action()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scraper_scheduler_target_passes_validation_without_mcp_listener_settings(self):
        result = self.validate_action(
            OCI_TARGET="ford-scraper-scheduler",
            OCI_MCP_PRIVATE_IP="",
            OCI_DATA_PRIVATE_IP="",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scheduler_target_requires_the_existing_database_vault_reference(self):
        result = self.validate_action(
            OCI_TARGET="ford-scraper-scheduler",
            OCI_SCHEDULER_POSTGRES_PASSWORD_SECRET_OCID="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ford-scraper-scheduler requires the existing PostgreSQL Vault secret reference", result.stderr)

    def test_scheduler_target_requires_backup_and_uses_scheduler_playbook(self):
        result = self.validate_action(
            OCI_TARGET="ford-scraper-scheduler",
            OCI_BACKUP_HOST="",
            OCI_BACKUP_SSH_PRIVATE_KEY="",
            OCI_MCP_PRIVATE_IP="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ford-scraper-scheduler deploy requires the existing data VM backup host", result.stderr)
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('playbook = "deployment/ansible/scraper-scheduler-blue-green.yml"', script)
        self.assertIn('extra_vars["scheduler_image"]', script)

    def test_scheduler_release_is_backup_gated_and_rolls_back_to_one_active_container(self):
        role = (ROOT / "deployment/ansible/roles/scraper_scheduler/tasks/release.yml").read_text(encoding="utf-8")
        compose = (ROOT / "deployment/ansible/roles/scraper_scheduler/templates/compose.yml.j2").read_text(encoding="utf-8")
        playbook = (ROOT / "deployment/ansible/scraper-scheduler-blue-green.yml").read_text(encoding="utf-8")
        deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertLess(deploy.index("deployment/ansible/backup-now.yml"), deploy.index('"$PLAYBOOK"'))
        self.assertLess(role.index("Migrate the catalog"), role.index("Replace the single active scheduler"))
        self.assertIn("Restore the previous scheduler image", role)
        self.assertIn("'active_slot': 'scheduler'", role)
        self.assertIn('command: ["node", "dist/src/cli.js", "scheduler"]', compose)
        self.assertNotIn("ports:", compose)
        self.assertIn("hosts: data", playbook)

    def test_scheduler_reader_is_provisioned_after_migrations_and_before_swap(self):
        role = (ROOT / "deployment/ansible/roles/scraper_scheduler/tasks/release.yml").read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(encoding="utf-8")
        deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        migration = role.index("Migrate the catalog using the candidate image")
        reader_setup = role.index("Provision or repair the catalog reader role")
        scheduler_swap = role.index("Replace the single active scheduler")
        self.assertLess(migration, reader_setup)
        self.assertLess(reader_setup, scheduler_swap)
        self.assertIn("ford_catalog_reader", role)
        self.assertIn("REVOKE ALL PRIVILEGES ON SCHEMA ford", role)
        self.assertIn("GRANT SELECT ON ALL TABLES IN SCHEMA ford_read", role)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE ford IN SCHEMA ford_read", role)
        self.assertIn("has_table_privilege", role)
        self.assertIn("no_log: true", role[role.index("Provision or repair the catalog reader role"):scheduler_swap])
        self.assertIn("scheduler-postgres-password-secret-ocid:", action)
        self.assertIn("OCI_SCHEDULER_POSTGRES_PASSWORD_SECRET_OCID:", action)
        self.assertIn('extra_vars["scheduler_postgres_password_secret_ocid"] = os.environ["OCI_SCHEDULER_POSTGRES_PASSWORD_SECRET_OCID"]', deploy)

    def test_scheduler_playbook_returns_active_and_prior_images_to_the_action_runner(self):
        playbook = (ROOT / "deployment/ansible/scraper-scheduler-blue-green.yml").read_text(encoding="utf-8")

        self.assertIn("ansible.builtin.import_role:", playbook)
        self.assertIn("tasks_from: release.yml", playbook)
        self.assertIn("Fetch redacted scheduler release metadata", playbook)
        self.assertIn('src: "{{ remote_root }}/mcp/scheduler-release.json"', playbook)
        self.assertIn('dest: "{{ release_summary_path }}"', playbook)

    def test_service_deploy_requires_the_existing_database_private_ip(self):
        missing = self.validate_action(OCI_DATA_PRIVATE_IP="")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(
            "ford-service requires the existing data VM private IPv4 address",
            missing.stderr,
        )

        malformed = self.validate_action(OCI_DATA_PRIVATE_IP="999.999.999.999")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn(
            "ford-service requires a valid existing data VM private IPv4 address",
            malformed.stderr,
        )

    def test_service_deploy_requires_a_valid_operator_login_email(self):
        missing = self.validate_action(OCI_OPERATOR_USERNAME="")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("ford-service requires a valid operator login email", missing.stderr)

        malformed = self.validate_action(OCI_OPERATOR_USERNAME="operator")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("ford-service requires a valid operator login email", malformed.stderr)

    def test_service_defaults_to_deepseek_cloud_without_local_or_secondary_providers(self):
        template = (
            ROOT / "deployment/ansible/roles/app/templates/app.env.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("FORD_AI_CHAT_PROVIDER={{ ford_ai_chat_provider | default('openai') }}", template)
        self.assertIn("FORD_AI_CLOUD_PROVIDER={{ ford_ai_cloud_provider | default('openai') }}", template)
        self.assertIn("FORD_AI_CLOUD_ENABLED={{ (ford_ai_cloud_enabled | default(true))", template)
        self.assertIn("FORD_AI_LOCAL_ENABLED={{ (ford_ai_local_enabled | default(false))", template)
        self.assertIn("FORD_AI_CLOUD_BASE_URL={{ ford_ai_cloud_base_url | default('https://api.deepseek.com') }}", template)
        self.assertIn("FORD_AI_CLOUD_MODEL_VERSION={{ ford_ai_cloud_model_version | default('deepseek-flash') }}", template)
        self.assertIn("FORD_AI_PROVIDER_MAX_ATTEMPTS={{ ford_ai_provider_max_attempts | default(1) }}", template)
        self.assertIn("FORD_AI_FALLBACK_ORDER={{ ford_ai_fallback_order | default('') }}", template)

    def test_public_mcp_smoke_uses_vault_credentials_and_rolls_back_failed_mcp_cutovers(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(
            encoding="utf-8"
        )
        smoke = (ROOT / "deployment/ansible/mcp-public-smoke.yml").read_text(
            encoding="utf-8"
        )

        for input_name in (
            "mcp-public-smoke-access-client-id:",
            "mcp-public-smoke-access-client-secret-secret-ocid:",
        ):
            with self.subTest(input_name=input_name):
                self.assertIn(input_name, action)

        self.assertIn("OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID:", action)
        self.assertIn("OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID:", action)
        self.assertIn('"mcp_public_smoke_access_client_id": os.environ["OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID"]', script)
        self.assertIn('"mcp_public_smoke_access_client_secret_secret_ocid": os.environ["OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID"]', script)
        self.assertIn('"mcp_public_smoke_token_secret_ocid": os.environ["OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID"]', script)
        self.assertIn("deployment/ansible/mcp-public-smoke.yml", script)
        self.assertIn("mcp_operation\"] = \"rollback\"", script)
        self.assertIn("hosts: all", smoke)
        self.assertIn("instance_principal", smoke)
        self.assertIn("no_log: true", smoke)
        self.assertIn("method: tools/call", smoke)
        self.assertIn("name: search_vehicles", smoke)
        self.assertIn('regex_search(', smoke)
        self.assertIn('"isError"', smoke)
        self.assertIn('is not none', smoke)
        self.assertIn("modelPageUrl", smoke)
        self.assertIn("'modelPageUrl' not in mcp_public_search.content", smoke)
        self.assertIn("'https://www.forduruapan.mx/' not in mcp_public_search.content", smoke)
        self.assertNotIn("'isError' in mcp_public_search.content", smoke)

    def test_service_operator_login_email_is_passed_to_ansible(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("operator-username:", action)
        self.assertIn("OCI_OPERATOR_USERNAME: ${{ inputs['operator-username'] }}", action)
        self.assertIn('"ford_operator_username": os.environ["OCI_OPERATOR_USERNAME"]', script)

    def test_service_ui_upstream_is_passed_to_ansible_environment(self):
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(
            encoding="utf-8"
        )
        template = (
            ROOT / "deployment/ansible/roles/app/templates/app.env.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("ui-upstream-url:", action)
        self.assertIn("default: https://emme-crm-ui.pages.dev", action)
        self.assertIn(
            "FORD_WEB_UI_UPSTREAM_URL: ${{ inputs['ui-upstream-url'] }}", action
        )
        self.assertIn(
            "default('https://emme-crm-ui.pages.dev', true)", template
        )

    def test_service_inventory_uses_the_data_private_ip(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(encoding="utf-8")

        self.assertIn(
            'host_vars["emme_2_private_ip"] = os.environ["OCI_DATA_PRIVATE_IP"]',
            script,
        )
        self.assertIn("data-private-ip:", action)
        self.assertIn("operator-username:", action)
        self.assertIn(
            "OCI_DATA_PRIVATE_IP: ${{ inputs['data-private-ip'] }}",
            action,
        )

    def test_service_deploy_passes_observability_settings_and_vault_secret_ids(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('"grafana_cloud_otlp_endpoint": os.environ["OCI_GRAFANA_CLOUD_OTLP_ENDPOINT"]', script)
        self.assertIn('"grafana_cloud_instance_id": os.environ["OCI_GRAFANA_CLOUD_INSTANCE_ID"]', script)
        self.assertIn('"grafana_cloud_api_token_secret_ocid": os.environ["OCI_GRAFANA_CLOUD_API_TOKEN_SECRET_OCID"]', script)
        self.assertIn('"langfuse_otlp_endpoint": os.environ["OCI_LANGFUSE_OTLP_ENDPOINT"]', script)
        self.assertIn('"langfuse_public_key": os.environ["OCI_LANGFUSE_PUBLIC_KEY"]', script)
        self.assertIn('"langfuse_secret_key_secret_ocid": os.environ["OCI_LANGFUSE_SECRET_KEY_SECRET_OCID"]', script)
        self.assertIn('"service_source_root": os.environ["OCI_SERVICE_SOURCE_ROOT"]', script)
        self.assertIn("secrets-provider:", action)
        self.assertIn("FORD_SECRETS_PROVIDER: ${{ inputs['secrets-provider'] }}", action)
        for env_name in (
            "OCI_GRAFANA_CLOUD_OTLP_ENDPOINT",
            "OCI_GRAFANA_CLOUD_INSTANCE_ID",
            "OCI_GRAFANA_CLOUD_API_TOKEN_SECRET_OCID",
            "OCI_LANGFUSE_OTLP_ENDPOINT",
            "OCI_LANGFUSE_PUBLIC_KEY",
            "OCI_LANGFUSE_SECRET_KEY_SECRET_OCID",
            "OCI_SERVICE_SOURCE_ROOT",
        ):
            with self.subTest(env_name=env_name):
                self.assertIn(f"{env_name}:", action)

    def test_service_collector_secrets_are_read_on_host_without_logging_values(self):
        tasks = (ROOT / "deployment/ansible/roles/app/tasks/configure-observability.yml").read_text(
            encoding="utf-8"
        )
        template = (
            ROOT / "deployment/ansible/roles/app/templates/collector.env.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("--auth\n      - instance_principal", tasks)
        self.assertIn("no_log: true", tasks)
        self.assertIn("grafana_cloud_api_token: \"{{ observability_secret_bundles.results[0].stdout", tasks)
        self.assertIn("langfuse_secret_key: \"{{ observability_secret_bundles.results[1].stdout", tasks)
        self.assertIn("GRAFANA_CLOUD_API_TOKEN={{ grafana_cloud_api_token }}", template)
        self.assertIn("LANGFUSE_SECRET_KEY={{ langfuse_secret_key }}", template)
        self.assertNotIn("lookup('ansible.builtin.env', 'GRAFANA_CLOUD_API_TOKEN')", template)

    def test_mcp_deploy_requires_oci_vault_runtime_secrets(self):
        result = self.validate_action(
            OCI_TARGET="ford-mcp",
            OCI_SECRETS_PROVIDER="",
            OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID="",
            OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ford-mcp requires OCI Vault runtime secret references", result.stderr)

        valid = self.validate_action(OCI_TARGET="ford-mcp")
        self.assertEqual(valid.returncode, 0, valid.stderr)

    def test_mcp_deploy_requires_vault_credentials_for_public_smoke(self):
        missing = self.validate_action(
            OCI_TARGET="ford-mcp",
            OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID="",
            OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID="",
        )

        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("ford-mcp deploy requires OCI Vault credentials for the public endpoint smoke check", missing.stderr)

    def test_mcp_vault_secret_references_reach_the_remote_role_without_values(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("postgres-password-secret-ocid:", action)
        self.assertIn("mcp-http-bearer-token-secret-ocid:", action)
        self.assertIn('"mcp_postgres_password_secret_ocid": os.environ["OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID"]', script)
        self.assertIn('"mcp_http_bearer_token_secret_ocid": os.environ["OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID"]', script)

    def test_blue_green_playbooks_prepare_runtime_configuration_before_rollout(self):
        app = (ROOT / "deployment/ansible/app-blue-green.yml").read_text(
            encoding="utf-8"
        )
        mcp = (ROOT / "deployment/ansible/mcp-blue-green.yml").read_text(
            encoding="utf-8"
        )
        app_configure = app.index("tasks_from: configure-observability.yml")
        app_rollout = app.index("tasks_from: blue-green.yml")
        mcp_configure = mcp.index("tasks_from: configure-runtime.yml")
        mcp_rollout = mcp.index("tasks_from: blue-green.yml")

        self.assertLess(app_configure, app_rollout)
        self.assertLess(mcp_configure, mcp_rollout)

    def test_runtime_secret_reads_use_instance_principal_and_hide_values(self):
        app = (
            ROOT / "deployment/ansible/roles/app/tasks/configure-observability.yml"
        ).read_text(encoding="utf-8")
        mcp = (
            ROOT / "deployment/ansible/roles/mcp/tasks/configure-runtime.yml"
        ).read_text(encoding="utf-8")

        for source in (app, mcp):
            with self.subTest(source=source[:24]):
                self.assertIn("instance_principal", source)
                self.assertIn("no_log: true", source)
                self.assertIn("secret-bundle", source)

        self.assertIn("src: app.env.j2", app)
        self.assertIn("src: collector.env.j2", app)
        self.assertIn("src: mcp-runtime.env.j2", mcp)

    def test_action_rejects_mutable_images(self):
        result = self.validate_action(OCI_IMAGE="ghcr.io/example/ford-service:latest")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("immutable GHCR image digest", result.stderr)

    def test_validation_errors_never_print_secret_values(self):
        result = self.validate_action(OCI_TARGET="unknown")
        output = result.stdout + result.stderr

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PRIVATE-KEY-SHOULD-NOT-LEAK", output)
        self.assertNotIn("BACKUP-PRIVATE-KEY-SHOULD-NOT-LEAK", output)
        self.assertNotIn("REGISTRY-TOKEN-SHOULD-NOT-LEAK", output)

    def test_service_action_exposes_separate_read_only_catalog_runtime_settings(self):
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(encoding="utf-8")
        deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        template = (ROOT / "deployment/ansible/roles/app/templates/app.env.j2").read_text(encoding="utf-8")

        for input_name in (
            "service-catalog-tools-enabled",
            "service-catalog-jdbc-url",
            "service-catalog-username",
        ):
            self.assertIn(input_name, action)
        for runtime_name in (
            "FORD_CATALOG_TOOLS_ENABLED",
            "FORD_CATALOG_JDBC_URL",
            "FORD_CATALOG_USERNAME",
        ):
            self.assertIn(runtime_name, deploy + template)
        self.assertIn("FORD_CATALOG_TOOLS_ENABLED={{ (ford_catalog_tools_enabled | default(false))", template)
        self.assertIn("FORD_CATALOG_JDBC_URL={{ ford_catalog_jdbc_url | default('') }}", template)
        self.assertIn("FORD_CATALOG_USERNAME={{ ford_catalog_username | default('') }}", template)
        self.assertNotIn("service-catalog-password-secret-ocid", action)
        self.assertNotIn("FORD_VAULT_SECRET_OCID_FORD_CATALOG_PASSWORD", deploy + template)
        self.assertNotIn("FORD_MCP_READ_ONLY_TOOL_NAMES", template)
        self.assertNotIn("FORD_MCP_", template)

    def test_enabled_catalog_tools_require_reader_url_and_user_without_a_new_secret(self):
        missing = self.validate_action(OCI_SERVICE_CATALOG_TOOLS_ENABLED="true")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("require a PostgreSQL catalog JDBC URL", missing.stderr)

        missing_username = self.validate_action(
            OCI_SERVICE_CATALOG_TOOLS_ENABLED="true",
            OCI_SERVICE_CATALOG_JDBC_URL="jdbc:postgresql://10.0.0.11:5432/ford?connectTimeout=5",
        )
        self.assertNotEqual(missing_username.returncode, 0)
        self.assertIn("require a dedicated reader username", missing_username.stderr)

        configured = self.validate_action(
            OCI_SERVICE_CATALOG_TOOLS_ENABLED="true",
            OCI_SERVICE_CATALOG_JDBC_URL="jdbc:postgresql://10.0.0.11:5432/ford?connectTimeout=5",
            OCI_SERVICE_CATALOG_USERNAME="ford_catalog_reader",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def test_deploy_script_installs_its_pinned_ansible_collection(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('ansible-galaxy" collection install', script)
        self.assertIn("deployment/ansible/requirements.yml", script)

    def test_deploy_script_uses_collections_and_roles_packaged_with_the_action(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('ANSIBLE_COLLECTIONS_PATH="$TEMP_ROOT/collections"', script)
        self.assertIn('ANSIBLE_ROLES_PATH="$REPOSITORY_ROOT/deployment/ansible/roles"', script)

    def test_action_package_contains_both_target_playbooks_and_backup_playbook(self):
        for playbook in (
            "app-blue-green.yml",
            "mcp-blue-green.yml",
            "scraper-scheduler-blue-green.yml",
            "backup-now.yml",
        ):
            with self.subTest(playbook=playbook):
                self.assertTrue((ROOT / "deployment/ansible" / playbook).is_file())

    def test_blue_green_playbooks_import_the_blue_green_role_tasks(self):
        playbooks = (("app-blue-green.yml", "app"), ("mcp-blue-green.yml", "mcp"))
        for playbook, role in playbooks:
            with self.subTest(playbook=playbook):
                source = (ROOT / "deployment/ansible" / playbook).read_text(
                    encoding="utf-8"
                )
                self.assertIn("ansible.builtin.import_role:", source)
                self.assertIn(f"name: {role}", source)
                self.assertIn("tasks_from: blue-green.yml", source)
                self.assertNotIn("\n  roles:", source)

    def test_action_ci_checks_selected_playbooks_list_blue_green_tasks(self):
        source = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")

        self.assertIn("deployment/ansible/app-blue-green.yml --list-tasks", source)
        self.assertIn("deployment/ansible/mcp-blue-green.yml --list-tasks", source)
        self.assertIn("Validate blue-green Ford Service inputs", source)
        self.assertIn("Validate blue-green MCP deployment inputs", source)
        self.assertIn("Read observability credentials from OCI Vault", source)
        self.assertIn("Read MCP runtime credentials from OCI Vault", source)

    def test_mcp_environment_file_is_checked_on_the_remote_host(self):
        source = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("path: \"{{ remote_root }}/mcp/.env\"", source)
        self.assertIn("register: mcp_runtime_env_file", source)
        self.assertIn("mcp_runtime_env_file.stat.exists", source)
        self.assertNotIn("(remote_root ~ '/mcp/.env') is file", source)

    def test_service_environment_file_is_checked_on_the_remote_host(self):
        source = (
            ROOT / "deployment/ansible/roles/app/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("path: \"{{ remote_root }}/app/.env\"", source)
        self.assertIn("register: app_runtime_env_file", source)
        self.assertIn("app_runtime_env_file.stat.exists", source)
        self.assertNotIn("(remote_root ~ '/app/.env') is file", source)

    def test_backup_staging_and_redis_dump_paths_are_unique_per_invocation(self):
        source = (
            ROOT
            / "deployment/ansible/roles/backup/templates/backup-databases.sh.j2"
        ).read_text(encoding="utf-8")

        self.assertIn('run_id="${timestamp}-$(date +%s%N)-$$"', source)
        self.assertIn('run_dir="$staging_root/$run_id"', source)
        self.assertIn('redis_tmp="/tmp/ford-backup-${run_id}.rdb"', source)
        self.assertIn('REDIS_BACKUP_PATH="$redis_tmp"', source)

    def test_legacy_mcp_rollback_uses_the_container_image_id(self):
        source = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("Read the legacy MCP container image ID", source)
        self.assertIn("{{.Image}}", source)
        self.assertIn("mcp_legacy_container_image.stdout", source)
        self.assertIn("docker image tag", source)
        self.assertIn("ford-mcp-legacy:legacy-", source)
        self.assertNotIn("RepoDigests", source)
        self.assertIn("^ford-mcp-legacy:legacy-[a-f0-9]{64}$", source)
        self.assertIn("mcp_candidate_image is match('^ghcr", source)
        self.assertIn("mcp_operation | default('deploy') == 'rollback'", source)
        self.assertIn("mcp_candidate_image is match('^ghcr[.]io/')", source)

    def test_legacy_service_rollback_snapshots_running_runtime_configuration(self):
        tasks = yaml.safe_load(
            (
                ROOT / "deployment/ansible/roles/app/tasks/blue-green.yml"
            ).read_text(encoding="utf-8")
        )
        source = str(tasks)

        self.assertIn("Read the legacy Ford Service container ID", source)
        self.assertIn("{{.Id}}", source)
        self.assertIn("app_legacy_container_id.stdout", source)
        self.assertIn("docker commit --pause=false", source)
        self.assertIn("Commit the running legacy Ford Service container for rollback", source)
        self.assertIn("ford-service-legacy:legacy-", source)
        self.assertIn("app_current_slot != 'legacy' or app_current_image is match('^ford-service-legacy:legacy-", source)
        self.assertIn("app_operation | default('deploy') == 'rollback'", source)
        self.assertIn("app_candidate_image is match('^ghcr[.]io/')", source)

        self.assertLess(
            source.index("Commit the running legacy Ford Service container for rollback"),
            source.index("Start candidate Ford Service slot"),
        )
        self.assertLess(
            source.index("Commit the running legacy Ford Service container for rollback"),
            source.index("Stop the legacy Ford Service container before binding the stable listener"),
        )

    def test_candidate_ports_are_selected_from_resolved_slots(self):
        for role, variable, blue_port, green_port in (
            ("app", "app", 18081, 18082),
            ("mcp", "mcp", 3101, 3102),
        ):
            with self.subTest(role=role):
                tasks = yaml.safe_load(
                    (
                        ROOT
                        / f"deployment/ansible/roles/{role}/tasks/blue-green.yml"
                    ).read_text(encoding="utf-8")
                )
                task_label = (
                    "Select candidate Ford Service port"
                    if role == "app"
                    else "Select candidate MCP port"
                )
                port_task = next(
                    task
                    for task in tasks
                    if task.get("name") == task_label
                )
                expression = port_task["ansible.builtin.set_fact"][
                    f"{variable}_candidate_port"
                ]
                template = jinja2.Environment().from_string(expression)
                self.assertEqual(
                    str(blue_port),
                    template.render(
                        **{
                            f"{variable}_candidate_slot": "blue",
                            f"{variable}_blue_port_resolved": blue_port,
                            f"{variable}_green_port_resolved": green_port,
                        }
                    ),
                )
                self.assertEqual(
                    str(green_port),
                    template.render(
                        **{
                            f"{variable}_candidate_slot": "green",
                            f"{variable}_blue_port_resolved": blue_port,
                            f"{variable}_green_port_resolved": green_port,
                        }
                    ),
                )

    def test_compose_templates_use_nonrecursive_resolved_port_defaults(self):
        for role, variable, blue_port, green_port, render_label in (
            ("app", "app", 18081, 18082, "Render blue-green Ford Service Compose file"),
            ("mcp", "mcp", 3101, 3102, "Render blue-green MCP Compose file"),
        ):
            with self.subTest(role=role):
                tasks = yaml.safe_load(
                    (
                        ROOT
                        / f"deployment/ansible/roles/{role}/tasks/blue-green.yml"
                    ).read_text(encoding="utf-8")
                )
                resolve_task = next(
                    task
                    for task in tasks
                    if task.get("name") == f"Resolve {role} blue-green slot ports"
                )
                self.assertEqual(
                    f"{{{{ {variable}_blue_port | default({blue_port}) | int }}}}",
                    resolve_task["ansible.builtin.set_fact"][
                        f"{variable}_blue_port_resolved"
                    ],
                )
                render_task = next(
                    task for task in tasks if task.get("name") == render_label
                )
                template = jinja2.Environment().from_string(
                    render_task["vars"][f"{variable}_blue_port"]
                )
                self.assertEqual(
                    str(blue_port),
                    template.render(
                        **{f"{variable}_blue_port_resolved": blue_port}
                    ),
                )
                self.assertNotIn(
                    f"{variable}_blue_port | default",
                    render_task["vars"][f"{variable}_blue_port"],
                )

    def test_task_variables_do_not_recursively_reference_their_own_names(self):
        for role in ("app", "mcp"):
            with self.subTest(role=role):
                tasks = yaml.safe_load(
                    (
                        ROOT
                        / f"deployment/ansible/roles/{role}/tasks/blue-green.yml"
                    ).read_text(encoding="utf-8")
                )
                pending = list(tasks)
                while pending:
                    task = pending.pop()
                    pending.extend(task.get("block", []))
                    pending.extend(task.get("rescue", []))
                    pending.extend(task.get("always", []))
                    for name, expression in task.get("vars", {}).items():
                        if isinstance(expression, str):
                            self.assertNotRegex(
                                expression,
                                rf"{{{{\s*{name}(?:\s|\|)",
                                f"{role} task '{task.get('name')}' recursively scopes {name}",
                            )

    def test_temporary_registry_config_preserves_the_active_docker_context(self):
        for role in ("app", "mcp"):
            with self.subTest(role=role):
                tasks = yaml.safe_load(
                    (
                        ROOT
                        / f"deployment/ansible/roles/{role}/tasks/blue-green.yml"
                    ).read_text(encoding="utf-8")
                )
                flattened_tasks = []

                def collect_nested_tasks(task_list):
                    for task in task_list:
                        flattened_tasks.append(task)
                        for key in ("block", "rescue", "always"):
                            collect_nested_tasks(task.get(key, []))

                collect_nested_tasks(tasks)
                resolve_name = f"Resolve {role} Docker client config directory"
                resolve_task = next(
                    task
                    for task in flattened_tasks
                    if task.get("name") == resolve_name
                )
                config_var = f"{role}_docker_config_path"
                self.assertIn(
                    "ansible_env.DOCKER_CONFIG",
                    resolve_task["ansible.builtin.set_fact"][config_var],
                )
                self.assertIn(
                    "ansible_env.HOME",
                    resolve_task["ansible.builtin.set_fact"][config_var],
                )

                context_name = f"Capture {role} Docker context for temporary GHCR config"
                capture_task = next(
                    task
                    for task in flattened_tasks
                    if task.get("name") == context_name
                )
                self.assertEqual(
                    "docker context show",
                    capture_task["ansible.builtin.command"]["cmd"],
                )

                context_copy_name = (
                    f"Preserve {role} Docker context in temporary GHCR config"
                )
                context_copy_task = next(
                    task
                    for task in flattened_tasks
                    if task.get("name") == context_copy_name
                )
                config_write_name = (
                    f"Write {role} isolated Docker CLI config"
                )
                config_write_task = next(
                    task
                    for task in flattened_tasks
                    if task.get("name") == config_write_name
                )
                login_index = next(
                    index
                    for index, task in enumerate(flattened_tasks)
                    if task.get("name")
                    == f"Authenticate to GHCR for the candidate pull"
                )
                resolve_index = flattened_tasks.index(resolve_task)
                capture_index = flattened_tasks.index(capture_task)
                for copy_task in (context_copy_task, config_write_task):
                    self.assertLess(flattened_tasks.index(copy_task), login_index)
                    self.assertTrue(copy_task["no_log"])
                    self.assertIn(
                        f"{role}_registry_config.path",
                        copy_task["ansible.builtin.copy"]["dest"],
                    )
                self.assertLess(resolve_index, capture_index)
                self.assertLess(capture_index, login_index)
                self.assertLess(
                    flattened_tasks.index(context_copy_task),
                    flattened_tasks.index(config_write_task),
                )
                self.assertTrue(
                    context_copy_task["ansible.builtin.copy"]["remote_src"]
                )
                self.assertTrue(config_write_task["ansible.builtin.copy"]["content"])
                self.assertEqual(
                    "0600",
                    config_write_task["ansible.builtin.copy"]["mode"],
                )
                config_content = config_write_task["ansible.builtin.copy"]["content"]
                self.assertIn("currentContext", config_content)
                self.assertNotRegex(
                    config_content,
                    r"auths|credHelpers|credsStore",
                )
                self.assertEqual(
                    f"{{{{ {config_var} }}}}/contexts/",
                    context_copy_task["ansible.builtin.copy"]["src"],
                )

    def test_app_environment_does_not_invent_an_operator_login_email(self):
        template = (
            ROOT / "deployment/ansible/roles/app/templates/app.env.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("ford_operator_username | default('')", template)

    def test_on_demand_backup_installs_oci_cli_before_running_backup_script(self):
        source = (ROOT / "deployment/ansible/backup-now.yml").read_text(
            encoding="utf-8"
        )
        installer = (
            ROOT / "deployment/ansible/roles/backup/tasks/install-oci-cli.yml"
        ).read_text(encoding="utf-8")

        install = source.index("tasks_from: install-oci-cli.yml")
        install_timer = source.index("tasks_from: install-timer.yml")
        backup = source.index("Create a verified PostgreSQL and Redis backup")

        self.assertLess(install, backup)
        self.assertLess(install_timer, backup)
        self.assertIn("ansible.builtin.import_role:", source)
        self.assertIn("name: backup", source)
        self.assertIn(
            "Verify OCI CLI is available for Object Storage uploads", installer
        )
        self.assertIn("cmd: /usr/local/bin/oci --version", installer)
        self.assertRegex(
            source,
            r"environment:\s*\n\s+PATH: /usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin",
        )
        timer_tasks = (
            ROOT / "deployment/ansible/roles/backup/tasks/install-timer.yml"
        ).read_text(encoding="utf-8")
        timer_template = (
            ROOT / "deployment/ansible/roles/backup/templates/ford-backup.timer.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("src: ford-backup.timer.j2", timer_tasks)
        self.assertIn("dest: /etc/systemd/system/ford-backup.timer", timer_tasks)
        self.assertIn("daemon_reload: true", timer_tasks)
        self.assertIn("state: restarted", timer_tasks)
        self.assertIn("OnCalendar=*-*-01 03:15:00 UTC", timer_template)


    def test_mcp_deploy_requires_existing_data_host_backup_credentials(self):
        environment = os.environ.copy()
        environment.update(
            {
                "OCI_TARGET": "ford-mcp",
                "OCI_IMAGE": "ghcr.io/example/ford-mcp@sha256:" + "a" * 64,
                "OCI_OPERATION": "deploy",
                "OCI_SOURCE_SHA": "c" * 40,
                "OCI_DEPLOY_HOST": "203.0.113.10",
                "OCI_DEPLOY_USER": "deploy",
                "OCI_SSH_PRIVATE_KEY": "PRIVATE-KEY-SHOULD-NOT-LEAK",
                "OCI_SSH_KNOWN_HOSTS": "203.0.113.10 ssh-ed25519 PUBLIC-HOST-KEY",
                "OCI_REGISTRY_USERNAME": "release-bot",
                "GHCR_TOKEN": "REGISTRY-TOKEN-SHOULD-NOT-LEAK",
                "OCI_REMOTE_ROOT": "/opt/ford",
                "OCI_MCP_PRIVATE_IP": "10.0.0.2",
                "OCI_ACTION_VALIDATE_ONLY": "true",
                "OCI_SECRETS_PROVIDER": "oci-vault",
                "OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_ID": "mcp-access-id.example",
                "OCI_MCP_PUBLIC_SMOKE_ACCESS_CLIENT_SECRET_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
            }
        )
        missing = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT)], cwd=ROOT, env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("ford-mcp deploy requires the existing data VM backup host", missing.stderr)

        environment.update(
            {
                "OCI_BACKUP_HOST": "203.0.113.11",
                "OCI_BACKUP_SSH_PRIVATE_KEY": "BACKUP-PRIVATE-KEY-SHOULD-NOT-LEAK",
            }
        )
        valid = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT)], cwd=ROOT, env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

    def test_mcp_deploy_runs_and_verifies_backup_before_ansible_migrations(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        backup_condition = script.index('if [[ "$OCI_OPERATION" == "deploy" ]]; then\n  backup_key=')
        backup_playbook = script.index('deployment/ansible/backup-now.yml', backup_condition)
        target_playbook = script.index('"$PLAYBOOK"', backup_playbook)
        self.assertLess(backup_playbook, target_playbook)
        backup_inventory = script[script.index('if target == "ford-mcp":'):script.index('extra_vars = {')]
        self.assertIn('os.environ["OCI_BACKUP_HOST"]', backup_inventory)
        self.assertIn('os.environ.get("OCI_BACKUP_USER") or os.environ["OCI_DEPLOY_USER"]', backup_inventory)
        self.assertIn('if target in ("ford-service", "ford-mcp", "ford-scraper-scheduler") and operation == "deploy":', backup_inventory)
        self.assertIn('if [[ "$OCI_TARGET" == "ford-service" && -n "${OCI_BACKUP_SSH_PRIVATE_KEY:-}" ]]; then', script)
        self.assertIn('backup_key="$TEMP_ROOT/ssh/backup_key"', script)

    def test_mcp_compose_templates_select_the_http_command(self):
        templates = (
            ROOT / "deployment/ansible/roles/mcp/templates/compose.yml.j2",
            ROOT / "deployment/ansible/roles/mcp/templates/blue-green-compose.yml.j2",
        )

        for path in templates:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertRegex(
                    source,
                    r'command:\s*\["node", "dist/src/cli\.js", "mcp-http"\]',
                )

    def test_mcp_role_accepts_the_immutable_digest_used_by_release_workflows(self):
        source = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "mcp_image is match('^ghcr[.]io/[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$')",
            source,
        )
        self.assertNotIn(
            "mcp_image is match('^ghcr[.]io/[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+$')",
            source,
        )

    def test_blue_green_runs_catalog_migrations_before_starting_mcp(self):
        source = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")
        migrate = source.index("Run catalog migrations once from the immutable candidate image")
        start = source.index("Start the candidate MCP slot")

        self.assertLess(migrate, start)
        self.assertIn('run --rm --no-deps mcp node dist/src/cli.js migrate', source)

    def test_migration_runs_with_the_candidate_image_and_compose_database_environment(self):
        compose = (
            ROOT / "deployment/ansible/roles/mcp/templates/compose.yml.j2"
        ).read_text(encoding="utf-8")
        override = (
            ROOT / "deployment/ansible/roles/mcp/templates/migration-compose.yml.j2"
        ).read_text(encoding="utf-8")
        tasks = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")
        migration_start = tasks.index(
            "Run catalog migrations once from the immutable candidate image"
        )
        migration_end = tasks.index("\n- name:", migration_start + 1)
        migration = tasks[migration_start:migration_end]

        self.assertIn("DATABASE_URL: postgres://ford:${POSTGRES_PASSWORD", compose)
        self.assertIn("FORD_BASE_URL: https://www.forduruapan.mx", compose)
        self.assertIn("image: {{ mcp_candidate_image }}", override)
        self.assertIn("-f \"{{ remote_root }}/mcp/migration-compose.yml\"", migration)
        self.assertIn("run --rm --no-deps mcp node dist/src/cli.js migrate", migration)
        self.assertIn("no_log: true", migration)

    def test_initial_mcp_migration_uses_the_same_compose_environment_as_the_service(self):
        tasks = (
            ROOT / "deployment/ansible/roles/mcp/tasks/main.yml"
        ).read_text(encoding="utf-8")
        migration_start = tasks.index("Run MCP catalog migrations once before starting the service")
        migration_end = tasks.index("\n- name:", migration_start + 1)
        migration = tasks[migration_start:migration_end]

        self.assertIn("docker compose", migration)
        self.assertIn("run --rm --no-deps mcp node dist/src/cli.js migrate", migration)
        self.assertIn("no_log: true", migration)

    def test_scheduler_candidate_is_healthy_before_the_mcp_route_switch(self):
        source = (
            ROOT / "deployment/ansible/roles/mcp/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")
        migration = source.index("Run catalog migrations once from the immutable candidate image")
        candidate = source.index("Start the candidate MCP slot")
        protocol = source.index("Verify candidate MCP protocol and tool contract")
        scheduler = source.index("Start or update the monthly scraper scheduler candidate")
        route = source.index("Start or reload the stable MCP proxy")

        self.assertLess(migration, candidate)
        self.assertLess(candidate, protocol)
        self.assertLess(protocol, scheduler)
        self.assertLess(scheduler, route)
        self.assertIn("Restore the prior scheduler image in Compose", source)
        self.assertIn("Stop the scheduler when no prior MCP release existed", source)

    def test_service_meta_reply_configuration_reaches_candidate_environment(self):
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(encoding="utf-8")
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        tasks = (ROOT / "deployment/ansible/app-blue-green.yml").read_text(encoding="utf-8")
        template = (ROOT / "deployment/ansible/roles/app/templates/app.env.j2").read_text(encoding="utf-8")

        for name in (
            "meta-delivery-enabled:",
            "outbox-enabled:",
            "meta-whatsapp-delivery-endpoint:",
            "meta-phone-number-id:",
            "meta-app-secret-ocid:",
            "meta-verify-token-ocid:",
            "meta-whatsapp-access-token-secret-ocid:",
        ):
            with self.subTest(name=name):
                self.assertIn(name, action)

        for name in (
            "OCI_META_DELIVERY_ENABLED",
            "OCI_OUTBOX_ENABLED",
            "OCI_META_WHATSAPP_DELIVERY_ENDPOINT",
            "OCI_META_PHONE_NUMBER_ID",
            "OCI_META_APP_SECRET_OCID",
            "OCI_META_VERIFY_TOKEN_OCID",
            "OCI_META_WHATSAPP_ACCESS_TOKEN_SECRET_OCID",
        ):
            with self.subTest(name=name):
                self.assertIn(f"{name}:", action)
                self.assertIn(f'os.environ.get("{name}"', script)

        self.assertIn('"ford_meta_delivery_enabled": os.environ.get("OCI_META_DELIVERY_ENABLED", "false") == "true"', script)
        self.assertIn('"ford_outbox_enabled": os.environ.get("OCI_OUTBOX_ENABLED", "false") == "true"', script)
        self.assertIn('"ford_meta_whatsapp_delivery_endpoint": os.environ.get("OCI_META_WHATSAPP_DELIVERY_ENDPOINT", "")', script)
        self.assertIn('"ford_meta_provider_account_id": os.environ.get("OCI_META_PHONE_NUMBER_ID", "")', script)
        self.assertIn("ford_meta_delivery_enabled", script)
        self.assertIn("ford_outbox_enabled", script)
        self.assertIn("no_log: true", tasks)
        self.assertIn("FORD_VAULT_SECRET_OCID_FORD_META_APP_SECRET={{ ford_meta_app_secret_ocid", template)
        self.assertIn("FORD_VAULT_SECRET_OCID_FORD_META_VERIFY_TOKEN={{ ford_meta_verify_token_ocid", template)
        self.assertIn("FORD_VAULT_SECRET_OCID_FORD_META_WHATSAPP_ACCESS_TOKEN={{ ford_meta_whatsapp_access_token_secret_ocid", template)

    def test_service_proxy_preserves_the_public_https_scheme_for_spring(self):
        proxy = (
            ROOT / "deployment/ansible/roles/app/templates/proxy.conf.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("proxy_set_header X-Forwarded-Proto https;", proxy)
        self.assertNotIn("proxy_set_header X-Forwarded-Proto $scheme;", proxy)
        self.assertIn('proxy_set_header Forwarded "";', proxy)
        self.assertIn("proxy_set_header X-Forwarded-Host $host;", proxy)
        self.assertIn("proxy_set_header X-Forwarded-Port 443;", proxy)

    def test_service_proxy_route_uses_a_directory_mount_for_atomic_updates(self):
        compose = (
            ROOT / "deployment/ansible/roles/app/templates/blue-green-compose.yml.j2"
        ).read_text(encoding="utf-8")
        tasks = (
            ROOT / "deployment/ansible/roles/app/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("./nginx:/etc/nginx/conf.d:ro", compose)
        self.assertIn("Create Nginx config directory for stable route", tasks)
        self.assertIn('path: "{{ remote_root }}/app/nginx"', tasks)
        self.assertIn('dest: "{{ remote_root }}/app/nginx/default.conf"', tasks)
        self.assertIn("docker exec ford-service-proxy cat /etc/nginx/conf.d/default.conf", tasks)
        self.assertIn('src: "{{ remote_root }}/app/proxy.conf.previous"', tasks)
        install = tasks.index("Install the candidate Ford Service proxy route")
        start_proxy = tasks.index("Start the stable Ford Service proxy")
        validate = tasks.index("Validate stable Ford Service proxy configuration")
        reload_proxy = tasks.index("Reload stable Ford Service proxy")
        self.assertLess(install, validate)
        self.assertLess(start_proxy, validate)
        self.assertLess(validate, reload_proxy)

    def test_service_runtime_environment_is_restored_when_blue_green_rollout_fails(self):
        playbook = (ROOT / "deployment/ansible/app-blue-green.yml").read_text(
            encoding="utf-8"
        )

        snapshot = playbook.index("Snapshot the current Ford Service environment")
        render = playbook.index("tasks_from: configure-observability.yml")
        rescue = playbook.index("Restore Ford Service environment after failed rollout")
        rollout = playbook.index("tasks_from: blue-green.yml")

        self.assertLess(snapshot, render)
        self.assertLess(render, rollout)
        self.assertGreater(rescue, rollout)
        self.assertIn("no_log: true", playbook[playbook.index("block:"):])
        self.assertIn("Fail after restoring Ford Service environment", playbook)

    def test_enabled_whatsapp_delivery_requires_a_matching_official_endpoint_and_vault_secrets(self):
        base = {
            "OCI_META_DELIVERY_ENABLED": "true",
            "OCI_OUTBOX_ENABLED": "true",
            "OCI_META_WHATSAPP_DELIVERY_ENDPOINT": "https://graph.facebook.com/v26.0/123456789/messages",
            "OCI_META_PHONE_NUMBER_ID": "123456789",
            "OCI_META_APP_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
            "OCI_META_VERIFY_TOKEN_OCID": "ocid1.vaultsecret.oc1.example",
            "OCI_META_WHATSAPP_ACCESS_TOKEN_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
        }
        self.assertEqual(self.validate_action(**base).returncode, 0)

        invalid = (
            ("OCI_META_WHATSAPP_DELIVERY_ENDPOINT", "https://evil.example/v26.0/123456789/messages"),
            ("OCI_META_WHATSAPP_DELIVERY_ENDPOINT", "https://graph.facebook.com/v26.0/987654321/messages"),
            ("OCI_META_PHONE_NUMBER_ID", "not-numeric"),
            ("OCI_META_APP_SECRET_OCID", ""),
            ("OCI_META_VERIFY_TOKEN_OCID", ""),
            ("OCI_META_WHATSAPP_ACCESS_TOKEN_SECRET_OCID", ""),
            ("OCI_OUTBOX_ENABLED", "false"),
        )
        for name, value in invalid:
            with self.subTest(name=name, value=value):
                result = self.validate_action(**(base | {name: value}))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("123456789", result.stderr)

    def test_blue_green_includes_a_private_monthly_scheduler_service(self):
        source = (
            ROOT
            / "deployment/ansible/roles/mcp/templates/blue-green-compose.yml.j2"
        ).read_text(encoding="utf-8")

        self.assertRegex(source, r"(?m)^  scheduler:")
        self.assertRegex(
            source,
            r'command:\s*\["node", "dist/src/cli\.js", "scheduler"\]',
        )
        self.assertIn("networks: [data_default]", source)
        self.assertIn('SYNC_SCHEDULE: "0 12 1 * *"', source)
        self.assertIn("SYNC_TIMEZONE: America/Mexico_City", source)
        scheduler = source.split("\n  scheduler:", 1)[1].split("\n  mcp-proxy:", 1)[0]
        self.assertNotIn("ports:", scheduler)


if __name__ == "__main__":
    unittest.main()

import unittest
import os
import subprocess
from pathlib import Path


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
                "OCI_ACTION_VALIDATE_ONLY": "true",
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
            "backup-now.yml",
        ):
            with self.subTest(playbook=playbook):
                self.assertTrue((ROOT / "deployment/ansible" / playbook).is_file())


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
        self.assertIn('if target in ("ford-service", "ford-mcp") and operation == "deploy":', backup_inventory)
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

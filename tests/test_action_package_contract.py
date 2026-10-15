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
                "OCI_ACTION_VALIDATE_ONLY": "true",
                "OCI_SECRETS_PROVIDER": "oci-vault",
                "OCI_MCP_POSTGRES_PASSWORD_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
                "OCI_MCP_HTTP_BEARER_TOKEN_SECRET_OCID": "ocid1.vaultsecret.oc1.example",
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

    def test_service_inventory_uses_the_data_private_ip(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        action = (ROOT / ".github/actions/oci-blue-green/action.yml").read_text(encoding="utf-8")

        self.assertIn(
            'host_vars["emme_2_private_ip"] = os.environ["OCI_DATA_PRIVATE_IP"]',
            script,
        )
        self.assertIn("data-private-ip:", action)
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

    def test_legacy_service_rollback_uses_the_container_image_id(self):
        source = (
            ROOT / "deployment/ansible/roles/app/tasks/blue-green.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("Read the legacy Ford Service container image ID", source)
        self.assertIn("{{.Image}}", source)
        self.assertIn("app_legacy_container_image.stdout", source)
        self.assertIn("docker image tag", source)
        self.assertIn("ford-service-legacy:legacy-", source)
        self.assertIn("app_current_slot != 'legacy' or app_current_image is match('^ford-service-legacy:legacy-", source)
        self.assertIn("app_operation | default('deploy') == 'rollback'", source)
        self.assertIn("app_candidate_image is match('^ghcr[.]io/')", source)

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

    def test_app_environment_defaults_operator_username_for_action_deployments(self):
        template = (
            ROOT / "deployment/ansible/roles/app/templates/app.env.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("ford_operator_username | default('operator')", template)

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

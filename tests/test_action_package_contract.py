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


if __name__ == "__main__":
    unittest.main()

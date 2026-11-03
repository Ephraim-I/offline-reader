"""Static regression checks for the GitHub release-attestation workflow."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
DOC = ROOT / "SOURCE_RELEASE.md"


class ReleaseAttestationConfigTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.docs = DOC.read_text(encoding="utf-8")

    def test_workflow_grants_minimum_attestation_permissions(self):
        self.assertIn("  contents: read", self.workflow)
        self.assertIn("  id-token: write", self.workflow)
        self.assertIn("  attestations: write", self.workflow)

    def test_attestation_action_targets_versioned_source_archive(self):
        self.assertIn("uses: actions/attest-build-provenance@v2", self.workflow)
        self.assertIn("subject-path: dist/offline-reader-source-${{ env.RELEASE_VERSION }}.zip", self.workflow)

    def test_attestation_occurs_after_archive_verification_and_before_upload(self):
        verify = self.workflow.index("- name: Verify source-release archive contents")
        attest = self.workflow.index("- name: Attest source-release archive")
        upload = self.workflow.index("- name: Upload source-release artifact")
        self.assertLess(verify, attest)
        self.assertLess(attest, upload)

    def test_docs_explain_cli_verification_and_no_long_lived_key(self):
        self.assertIn("gh attestation verify", self.docs)
        self.assertIn("No long-lived signing key", self.docs)
        self.assertIn("OWNER/REPOSITORY", self.docs)


if __name__ == "__main__":
    unittest.main()

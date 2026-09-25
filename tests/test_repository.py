"""Repository contracts inherited from ferrumctl."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "commit_messages", ROOT / "scripts" / "check_commit_message.py"
)
messages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(messages)


class RepositoryTests(unittest.TestCase):
    def test_scoped_commits_and_git_generated_messages(self):
        for message in [
            "feat(cli): allow an explicit RPC choice",
            "fix(privacy): suppress TLS session key logging",
            "chore(repo): establish contribution conventions",
            "fixup! feat(cli): allow an explicit RPC choice",
            'Revert "previous change"',
        ]:
            self.assertEqual(messages.validate_message(message), [])

    def test_bad_commits_are_rejected(self):
        for message in [
            "fix things",
            "feat(threadctl): wrong project scope",
            "fix(cli): too short.",
        ]:
            self.assertTrue(messages.validate_message(message))

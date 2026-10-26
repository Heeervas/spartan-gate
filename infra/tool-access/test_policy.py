import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from policy import AccessError, Policy

TOKEN = "a" * 64


def client(principal="fixture", token=TOKEN, capabilities=None):
    return {"principal": principal, "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
            "browserUser": principal + "-scratch",
            "capabilities": capabilities if capabilities is not None else ["search", "reader"]}


def write_policy(path, clients=None):
    path.write_text(json.dumps({"version": 1, "clients": [client()] if clients is None else clients}))
    path.chmod(0o600)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "clients.json"
        write_policy(self.path)
        self.policy = Policy(self.path)

    def denied(self, token="Bearer " + TOKEN, capability="search", code="unauthorized"):
        with self.assertRaises(AccessError) as caught:
            self.policy.authenticate(token, capability)
        self.assertEqual(caught.exception.code, code)

    def test_fixed_binding_capability_and_atomic_revocation(self):
        principal = self.policy.authenticate("Bearer " + TOKEN, "search")
        self.assertEqual((principal.principal, principal.browser_user), ("fixture", "fixture-scratch"))
        self.denied(capability="browser", code="forbidden")
        replacement = self.path.with_suffix(".new")
        write_policy(replacement, [])
        replacement.replace(self.path)
        self.denied()

    def test_entire_policy_is_validated_not_only_matching_client(self):
        malformed = client("other", "b" * 64)
        malformed["capabilities"] = ["admin"]
        write_policy(self.path, [client(), malformed])
        self.denied()
        self.path.write_text('{"version":1,"clients":[],"clients":[]}')
        self.denied()

    def test_file_security_missing_symlink_permissions_and_bound(self):
        self.path.chmod(0o644)
        self.denied()
        self.path.unlink()
        self.denied()
        target = self.path.with_suffix(".target")
        write_policy(target)
        self.path.symlink_to(target)
        self.denied()
        self.path.unlink()
        self.path.write_bytes(b" " * 65537)
        self.path.chmod(0o600)
        self.denied()

    def test_exact_token_shape_and_invalid_policy_json(self):
        for value in (None, "", "Basic " + TOKEN, "Bearer " + "A" * 64,
                      "Bearer " + "b" * 64, "Bearer " + TOKEN + " "):
            with self.subTest(value=value):
                self.denied(token=value)
        self.path.write_text('{"version":true,"clients":[]}')
        self.denied()


if __name__ == "__main__":
    unittest.main()

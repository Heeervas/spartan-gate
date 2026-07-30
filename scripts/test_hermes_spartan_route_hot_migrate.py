import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("hermes_spartan_route_hot_migrate.py")


def load_module():
    spec = importlib.util.spec_from_file_location("hermes_spartan_route_hot_migrate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HermesSpartanRouteHotMigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative_path, text):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_migrates_live_configs_and_cron_jobs_with_backup(self):
        module = load_module()
        env = self.write(".env", "CUSTOM_1_API_KEY=root-route-token\nOPENAI_BASE_URL=${OPENAI_BASE_URL}\n")
        config = self.write(
            "config.yaml",
            "\n".join([
                "model:",
                "  default: codex/gpt-5.5",
                "  provider: custom:spartan-route",
                "  api_key_env: CUSTOM_1_API_KEY",
                "  summary_provider: custom:spartan-route",
                "  summary_model: spartan-route/google/gemini-3-flash-preview",
                "custom_providers:",
                "  - name: Spartan Route",
                "    base_url: ${OPENAI_BASE_URL}",
                "    key_env: CUSTOM_1_API_KEY",
                "    model: spartan-route/clawroute/auto",
                "  - name: unrelated",
                "    base_url: http://llm.internal:8000/v1",
                "    key_env: OTHER_KEY",
                "    model: local-model",
                "",
            ]),
        )
        profile_env = self.write("profiles/work/.env", "CUSTOM_1_API_KEY=profile-route-token")
        profile = self.write(
            "profiles/work/config.yaml",
            "\n".join([
                "model:",
                "  default: custom-1/clawroute/auto",
                "  provider: custom-1",
                "custom_providers:",
                "  - name: custom-1",
                "    base_url: http://spartan-route:18790/v1",
                "    key_env: CUSTOM_1_API_KEY",
                "    model: custom-1/clawroute/auto",
                "",
            ]),
        )
        jobs = self.write(
            "profiles/work/cron/jobs.json",
            json.dumps({
                "jobs": [
                    {"provider": "custom:spartan-route", "model": "spartan-route/clawroute/auto"},
                    {"provider_snapshot": "custom", "model_snapshot": "clawroute/auto"},
                    {"provider": "custom", "model": "local-model"},
                    {"summary_provider": "custom-1", "summary_model": "custom-1/google/gemini-3-flash-preview"},
                ]
            }),
        )

        results = module.migrate_root(self.root, backup_stamp="test")

        changed = {result.path for result in results if result.changed}
        self.assertEqual(changed, {env, config, profile_env, profile, jobs})
        self.assertIn("CUSTOM_1_API_KEY=root-route-token\nOPENAI_API_KEY=root-route-token\n", env.read_text(encoding="utf-8"))
        self.assertEqual(
            profile_env.read_text(encoding="utf-8"),
            "CUSTOM_1_API_KEY=profile-route-token\nOPENAI_API_KEY=profile-route-token\n",
        )
        config_text = config.read_text(encoding="utf-8")
        self.assertIn("provider: openai-api", config_text)
        self.assertIn("api_key_env: OPENAI_API_KEY", config_text)
        self.assertIn("summary_provider: openai-api", config_text)
        self.assertIn("summary_model: google/gemini-3-flash-preview", config_text)
        self.assertIn("default: codex/gpt-5.5", config_text)
        self.assertNotIn("name: Spartan Route", config_text)
        self.assertNotIn("model: spartan-route/clawroute/auto", config_text)
        self.assertIn("name: unrelated", config_text)
        self.assertIn("model: local-model", config_text)
        profile_text = profile.read_text(encoding="utf-8")
        self.assertIn("default: clawroute/auto", profile_text)
        self.assertIn("provider: openai-api", profile_text)
        self.assertNotIn("custom_providers:", profile_text)
        migrated_jobs = json.loads(jobs.read_text(encoding="utf-8"))
        self.assertEqual(migrated_jobs["jobs"][0]["provider"], "openai-api")
        self.assertEqual(migrated_jobs["jobs"][0]["model"], "clawroute/auto")
        self.assertEqual(migrated_jobs["jobs"][1]["provider_snapshot"], "openai-api")
        self.assertEqual(migrated_jobs["jobs"][1]["model_snapshot"], "clawroute/auto")
        self.assertEqual(migrated_jobs["jobs"][2]["provider"], "custom")
        self.assertEqual(migrated_jobs["jobs"][2]["model"], "local-model")
        self.assertEqual(migrated_jobs["jobs"][3]["summary_provider"], "openai-api")
        self.assertEqual(migrated_jobs["jobs"][3]["summary_model"], "google/gemini-3-flash-preview")
        self.assertTrue((self.root / "backups" / "openai-api-provider-hot-migration-test" / ".env").exists())
        self.assertTrue((self.root / "backups" / "openai-api-provider-hot-migration-test" / "config.yaml").exists())
        self.assertTrue((self.root / "backups" / "openai-api-provider-hot-migration-test" / "profiles/work/.env").exists())
        self.assertTrue((self.root / "backups" / "openai-api-provider-hot-migration-test" / "profiles/work/config.yaml").exists())

        second = module.migrate_root(self.root, backup=False)
        self.assertFalse(any(result.changed for result in second))

    def test_existing_route_provider_gets_responses_mode_idempotently(self):
        module = load_module()
        config = self.write(
            "config.yaml",
            "model:\n  default: codex/gpt-5.6-sol\n  provider: openai-api\n  api_mode: chat_completions\n",
        )

        first = module.migrate_root(self.root, backup=False)
        self.assertTrue(any(result.changed for result in first))
        self.assertIn("api_mode: codex_responses", config.read_text(encoding="utf-8"))
        self.assertIn("- spartan-cache-lineage", config.read_text(encoding="utf-8"))
        second = module.migrate_root(self.root, backup=False)
        self.assertFalse(any(result.changed for result in second))

    def test_responses_profile_enables_reasoning_plugin_without_reformatting(self):
        module = load_module()
        (self.root / "profiles/vault/plugins").mkdir(parents=True)
        config = self.write(
            "profiles/vault/config.yaml",
            "\n".join([
                "model:",
                "  default: codex/gpt-5.6-terra",
                "  provider: openai-api",
                "  api_mode: codex_responses",
                "plugins:",
                "  enabled:",
                "    - hermes-lcm",
                "  disabled: []",
                "",
            ]),
        )

        first = module.migrate_root(self.root, backup=False)

        self.assertTrue(any(result.path == config and result.changed for result in first))
        migrated = config.read_text(encoding="utf-8")
        self.assertIn("    - hermes-lcm\n    - spartan-cache-lineage\n", migrated)
        self.assertEqual(module.yaml.safe_load(migrated)["plugins"]["disabled"], [])
        plugin_link = self.root / "profiles/vault/plugins/spartan-cache-lineage"
        self.assertTrue(plugin_link.is_symlink())
        self.assertEqual(os.readlink(plugin_link), "../../../plugins/spartan-cache-lineage")
        second = module.migrate_root(self.root, backup=False)
        self.assertFalse(any(result.changed for result in second))

    def test_reasoning_plugin_collision_blocks_all_writes(self):
        module = load_module()
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        config = self.write(
            "profiles/vault/config.yaml",
            "model:\n  default: codex/gpt-5.6-terra\n  provider: openai-api\n  api_mode: codex_responses\nplugins:\n  enabled: []\n",
        )
        collision = self.root / "profiles/vault/plugins/spartan-cache-lineage"
        collision.mkdir(parents=True)
        original_env = env.read_text(encoding="utf-8")
        original_config = config.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not a managed link"):
            module.migrate_root(self.root)

        self.assertEqual(env.read_text(encoding="utf-8"), original_env)
        self.assertEqual(config.read_text(encoding="utf-8"), original_config)
        self.assertFalse((self.root / "backups").exists())

    def test_auto_route_does_not_enable_reasoning_plugin(self):
        module = load_module()
        config = self.write(
            "config.yaml",
            "model:\n  default: clawroute/auto\n  provider: openai-api\n  api_mode: chat_completions\nplugins:\n  enabled: []\n",
        )

        module.migrate_root(self.root, backup=False)

        self.assertNotIn("spartan-cache-lineage", config.read_text(encoding="utf-8"))

    def test_auto_route_does_not_assume_subscription_realm(self):
        module = load_module()
        config = self.write(
            "config.yaml",
            "model:\n  default: clawroute/auto\n  provider: openai-api\n  api_mode: chat_completions\n",
        )

        module.migrate_root(self.root, backup=False)

        self.assertIn("api_mode: chat_completions", config.read_text(encoding="utf-8"))

    def test_cross_realm_background_review_blocks_all_writes(self):
        module = load_module()
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        config = self.write(
            "config.yaml",
            "\n".join([
                "model:",
                "  default: codex/gpt-5.6-sol",
                "  provider: openai-api",
                "auxiliary:",
                "  background_review:",
                "    provider: openai-api",
                "    model: gpt-5.6",
                "    api_mode: codex_responses",
                "    base_url: https://api.openai.com/v1",
                "",
            ]),
        )
        original_env = env.read_text(encoding="utf-8")
        original_config = config.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "background_review crosses"):
            module.migrate_root(self.root)

        self.assertEqual(env.read_text(encoding="utf-8"), original_env)
        self.assertEqual(config.read_text(encoding="utf-8"), original_config)
        self.assertFalse((self.root / "backups").exists())

    def test_dry_run_does_not_write_or_create_backups(self):
        module = load_module()
        config = self.write("config.yaml", "model:\n  provider: custom-1\n")
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        before_config = config.read_text(encoding="utf-8")
        before_env = env.read_text(encoding="utf-8")

        results = module.migrate_root(self.root, dry_run=True)

        self.assertTrue(any(result.changed for result in results))
        self.assertEqual(config.read_text(encoding="utf-8"), before_config)
        self.assertEqual(env.read_text(encoding="utf-8"), before_env)
        self.assertFalse((self.root / "backups").exists())

    def test_invalid_json_blocks_all_writes(self):
        module = load_module()
        config = self.write("config.yaml", "model:\n  provider: custom-1\n")
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        self.write("cron/jobs.json", "{not-json")
        before_config = config.read_text(encoding="utf-8")
        before_env = env.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            module.migrate_root(self.root)

        self.assertEqual(config.read_text(encoding="utf-8"), before_config)
        self.assertEqual(env.read_text(encoding="utf-8"), before_env)
        self.assertFalse((self.root / "backups").exists())

    def test_invalid_yaml_blocks_all_writes(self):
        module = load_module()
        config = self.write("config.yaml", "model:\n  provider: custom-1\n- orphan\n")
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        before_env = env.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            module.migrate_root(self.root)

        self.assertEqual(env.read_text(encoding="utf-8"), before_env)
        self.assertFalse((self.root / "backups").exists())

    def test_mid_install_failure_rolls_back_every_replaced_file(self):
        module = load_module()
        env = self.write(".env", "CUSTOM_1_API_KEY=route-token\n")
        config = self.write(
            "config.yaml",
            "model:\n  default: custom-1/clawroute/auto\n  provider: custom-1\n",
        )
        originals = {env: env.read_bytes(), config: config.read_bytes()}
        real_replace = module.os.replace
        calls = 0

        def fail_second_install(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-file install failure")
            return real_replace(source, destination)

        with mock.patch.object(module.os, "replace", side_effect=fail_second_install):
            with self.assertRaises(OSError):
                module.migrate_root(self.root, backup=False)

        self.assertEqual(env.read_bytes(), originals[env])
        self.assertEqual(config.read_bytes(), originals[config])
        self.assertEqual(list(self.root.rglob("*.route-migrate.*")), [])
        self.assertEqual(list(self.root.rglob("*.route-rollback.*")), [])

    def test_unchanged_jobs_json_preserves_formatting(self):
        module = load_module()
        jobs = self.write("cron/jobs.json", '{"jobs":[{"provider":"anthropic","model":"claude-test"}]}')

        results = module.migrate_root(self.root, dry_run=True)

        self.assertFalse(any(result.changed for result in results))
        self.assertEqual(jobs.read_text(encoding="utf-8"), '{"jobs":[{"provider":"anthropic","model":"claude-test"}]}')

    def test_env_with_openai_key_is_preserved(self):
        module = load_module()
        env = self.write("profiles/work/.env", "CUSTOM_1_API_KEY=route-token\nOPENAI_API_KEY=explicit-token\n")

        results = module.migrate_root(self.root)

        self.assertFalse(any(result.changed for result in results))
        self.assertEqual(
            env.read_text(encoding="utf-8"),
            "CUSTOM_1_API_KEY=route-token\nOPENAI_API_KEY=explicit-token\n",
        )

    def test_literal_openai_key_placeholder_is_preserved(self):
        module = load_module()
        env = self.write("profiles/market/.env", "CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}\nOPENAI_API_KEY=${OPENAI_API_KEY}\n")

        results = module.migrate_root(self.root)

        self.assertFalse(any(result.changed for result in results))
        self.assertEqual(
            env.read_text(encoding="utf-8"),
            "CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}\nOPENAI_API_KEY=${OPENAI_API_KEY}\n",
        )

    def test_literal_custom_key_adds_openai_self_reference(self):
        module = load_module()
        env = self.write("profiles/market/.env", "CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}\n")

        results = module.migrate_root(self.root)

        self.assertTrue(any(result.path == env and result.changed for result in results))
        self.assertEqual(
            env.read_text(encoding="utf-8"),
            "CUSTOM_1_API_KEY=${CUSTOM_1_API_KEY}\nOPENAI_API_KEY=${OPENAI_API_KEY}\n",
        )

    def test_route_named_block_without_route_evidence_is_preserved(self):
        module = load_module()
        config = self.write(
            "config.yaml",
            "\n".join([
                "custom_providers:",
                "  - name: Spartan Route",
                "    base_url: http://other-router:8000/v1",
                "    key_env: OTHER_KEY",
                "    model: unrelated-model",
                "",
            ]),
        )

        results = module.migrate_root(self.root)

        self.assertFalse(any(result.changed for result in results))
        self.assertIn("name: Spartan Route", config.read_text(encoding="utf-8"))

    def test_zero_indent_custom_provider_sequence_keeps_header(self):
        module = load_module()
        config = self.write(
            "config.yaml",
            "\n".join([
                "custom_providers:",
                "- name: Spartan Route",
                "  base_url: http://spartan-route:18790/v1",
                "  key_env: CUSTOM_1_API_KEY",
                "  model: clawroute/auto",
                "- name: unrelated",
                "  base_url: http://llm.internal:8000/v1",
                "  key_env: OTHER_KEY",
                "  model: local-model",
                "auxiliary:",
                "  vision:",
                "    provider: unrelated",
                "",
            ]),
        )

        module.migrate_root(self.root, backup=False)

        migrated = config.read_text(encoding="utf-8")
        self.assertIn("custom_providers:\n- name: unrelated", migrated)
        self.assertNotIn("name: Spartan Route", migrated)
        self.assertIn("auxiliary:", migrated)
        yaml_data = module.yaml.safe_load(migrated)
        self.assertEqual(yaml_data["custom_providers"][0]["name"], "unrelated")


if __name__ == "__main__":
    unittest.main()

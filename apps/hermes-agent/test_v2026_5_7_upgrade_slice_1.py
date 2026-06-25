import pathlib
import unittest


HERMES_DIR = pathlib.Path(__file__).parent
REPO_ROOT = HERMES_DIR.parents[1]
DOCKERFILE_PATH = HERMES_DIR / 'Dockerfile'
ENTRYPOINT_PATH = HERMES_DIR / 'entrypoint-wrapper.sh'
PACKAGE_INSTALLS_DOC_PATH = HERMES_DIR / 'runtime-docs' / 'package-installs.md'
COMPOSE_PATH = REPO_ROOT / 'infra' / 'compose' / 'compose.yml'
TIER_COMPOSE_DIR = REPO_ROOT / 'infra' / 'compose' / 'tiers'
REMOVED_PATCH_PATH = HERMES_DIR / 'patches' / 'patch_post_tool_empty_retry.py'


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


class HermesMainPinUpgradeTests(unittest.TestCase):
    def test_uses_pinned_main_base_image(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn(
            'FROM docker.io/nousresearch/hermes-agent:main@'
            'sha256:05a1e2ac8293b3a94bdfc1d8068bf495d7bd9e94d0300661bfa773f1e7576488',
            dockerfile,
        )

    def test_dockerfile_keeps_upstream_init_as_pid_1(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn('ENTRYPOINT ["/init", "/opt/hermes/entrypoint-wrapper.sh"]', dockerfile)
        self.assertNotIn('ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]', dockerfile)

    def test_dockerfile_patches_stage2_cdp_env_before_services(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn('RUN python3 /opt/hermes/patches/patch_stage2_cdp_env.py', dockerfile)
        self.assertIn('RUN python3 /opt/hermes/patches/patch_gateway_service_cdp_env.py', dockerfile)
        self.assertIn('RUN python3 /opt/hermes/patches/patch_s6_service_permissions.py', dockerfile)
        self.assertIn('RUN python3 /opt/hermes/patches/patch_runtime_identity_drop.py', dockerfile)
        self.assertLess(
            dockerfile.index('patch_stage2_install_chown_guard.py'),
            dockerfile.index('patch_s6_service_permissions.py'),
        )
        self.assertLess(
            dockerfile.index('patch_s6_service_permissions.py'),
            dockerfile.index('patch_runtime_identity_drop.py'),
        )
        self.assertLess(
            dockerfile.index('patch_runtime_identity_drop.py'),
            dockerfile.index('patch_stage2_cdp_env.py'),
        )
        self.assertLess(
            dockerfile.index('patch_stage2_cdp_env.py'),
            dockerfile.index('patch_gateway_service_cdp_env.py'),
        )

    def test_dockerfile_keeps_install_tree_root_owned(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn('COPY runtime-docs/ /opt/hermes/runtime-docs/', dockerfile)
        self.assertIn('RUN chown -R root:root /opt/hermes', dockerfile)
        self.assertNotIn('/opt/hermes-plugins', dockerfile)
        self.assertNotIn('RUN chown -R 10000:10000 /opt/hermes/.venv', dockerfile)
        self.assertNotIn('RUN chown -R 1000:1000 /opt/hermes/.venv', dockerfile)

    def test_dockerfile_bakes_manim_native_and_tex_dependencies(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        for package in (
            'libcairo2-dev',
            'libpango1.0-dev',
            'ffmpeg',
            'dvisvgm',
            'texlive-latex-base',
            'texlive-latex-extra',
            'texlive-fonts-recommended',
            'texlive-science',
        ):
            with self.subTest(package=package):
                self.assertIn(package, dockerfile)

    def test_entrypoint_delegates_to_upstream_main_wrapper(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertIn('#!/command/with-contenv bash', entrypoint)
        self.assertIn('run_as_hermes ln -sfn "${INSTALL_DIR}/.venv/bin/hermes" "${HERMES_HOME}/.local/bin/hermes"', entrypoint)
        self.assertIn('SPARTAN_HERMES_RUNTIME_OWNER="${SPARTAN_HERMES_RUNTIME_UID}:${SPARTAN_HERMES_RUNTIME_GID}"', entrypoint)
        self.assertIn('/command/s6-applyuidgid -u "$SPARTAN_HERMES_RUNTIME_UID" -g "$SPARTAN_HERMES_RUNTIME_GID" "$@"', entrypoint)
        self.assertIn('# hermes-runtime-user-path', entrypoint)
        self.assertIn('export PATH="/opt/data/.local/bin:/opt/hermes/.venv/bin:$PATH"', entrypoint)
        self.assertIn('export PYTHONUSERBASE="${PYTHONUSERBASE:-/opt/data/.local}"', entrypoint)
        self.assertIn('export PIP_TARGET="${PIP_TARGET:-/opt/data/hermes-extra-site}"', entrypoint)
        self.assertIn('export PYTHONPATH="${PYTHONPATH:-/opt/hermes/bootstrap:/opt/data/hermes-extra-site:/opt/hermes}"', entrypoint)
        self.assertIn('export npm_config_prefix="${npm_config_prefix:-/opt/data/.local}"', entrypoint)
        self.assertIn('exec /opt/hermes/docker/main-wrapper.sh "$@"', entrypoint)
        self.assertNotIn('exec hermes "$@"', entrypoint)
        self.assertNotIn('hermes dashboard --host', entrypoint)
        self.assertNotIn('SPARTAN_HERMES_DATA_GID', entrypoint)

    def test_web_search_plus_is_not_bundled_or_seeded(self):
        dockerfile = read_text(DOCKERFILE_PATH)
        entrypoint = read_text(ENTRYPOINT_PATH)
        compose = read_text(COMPOSE_PATH)

        self.assertNotIn('hermes-web-search-plus', dockerfile)
        self.assertNotIn('web-search-plus', dockerfile)
        self.assertNotIn('/opt/hermes-plugins', dockerfile)
        self.assertNotIn('/opt/hermes-plugins', entrypoint)
        self.assertNotIn('web-search-plus', entrypoint)
        self.assertIn('SEARXNG_URL: http://searxng:8080', compose)
        self.assertIn('SEARXNG_INSTANCE_URL: http://searxng:8080', compose)

    def test_entrypoint_seeds_package_install_runtime_doc_once(self):
        entrypoint = read_text(ENTRYPOINT_PATH)
        package_doc = read_text(PACKAGE_INSTALLS_DOC_PATH)

        self.assertIn('runtime_doc_src="$INSTALL_DIR/runtime-docs/package-installs.md"', entrypoint)
        self.assertIn('runtime_docs_dir="$HERMES_HOME/brain/runtime_docs"', entrypoint)
        self.assertIn('runtime_doc_dst="$runtime_docs_dir/package-installs.md"', entrypoint)
        self.assertIn('if [ -f "$runtime_doc_src" ] && [ ! -f "$runtime_doc_dst" ]; then', entrypoint)
        self.assertIn('run_as_hermes mkdir -p "$runtime_docs_dir"', entrypoint)
        self.assertIn('run_as_hermes cp "$runtime_doc_src" "$runtime_doc_dst"', entrypoint)
        self.assertNotIn('cp -f "$runtime_doc_src" "$runtime_doc_dst"', entrypoint)
        self.assertIn('uv pip install --python .venv/bin/python package==version', package_doc)
        self.assertIn('npm install package@version', package_doc)
        self.assertIn('sg-whitelist-domain 15d api.nuget.org', package_doc)

    def test_config_migration_is_not_deferred_to_main_wrapper(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertNotIn('run_install_patch', entrypoint)
        self.assertNotIn(
            'run_user_patch "$INSTALL_DIR/patches/patch_clawroute_named_provider.py"',
            entrypoint,
        )

    def test_entrypoint_persists_cdp_env_before_with_contenv_delegation(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        persist_call = 'persist_cdp_env_for_with_contenv'
        delegation = 'exec /opt/hermes/docker/main-wrapper.sh "$@"'

        self.assertIn('local env_dir="/run/s6/container_environment"', entrypoint)
        self.assertIn('rm -f "${env_dir}/BROWSER_CDP_LAUNCH_URL"', entrypoint)
        self.assertIn('printf \'%s\' "$BROWSER_CDP_LAUNCH_URL" > "${env_dir}/BROWSER_CDP_LAUNCH_URL"', entrypoint)
        self.assertIn('rm -f "${env_dir}/BROWSER_CDP_URL"', entrypoint)
        self.assertIn('printf \'%s\' "$BROWSER_CDP_URL" > "${env_dir}/BROWSER_CDP_URL"', entrypoint)
        self.assertIn('rm -f "${env_dir}/BROWSER_CDP_MAIN_URL"', entrypoint)
        self.assertIn('printf \'%s\' "$BROWSER_CDP_MAIN_URL" > "${env_dir}/BROWSER_CDP_MAIN_URL"', entrypoint)
        self.assertIn('CAMOFOX_ACCESS_KEY', entrypoint)
        self.assertIn('printf \'%s\' "${!key}" > "${env_dir}/${key}"', entrypoint)
        self.assertIn('camofox_browser_mode_enabled()', entrypoint)
        self.assertIn('CAMOFOX_URL set; Browserless CDP launch and main broker skipped', entrypoint)
        self.assertIn('unset BROWSER_CDP_URL', entrypoint)
        self.assertLess(entrypoint.rfind(persist_call), entrypoint.rfind(delegation))

    def test_camofox_access_key_patch_is_applied(self):
        dockerfile = read_text(DOCKERFILE_PATH)
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertIn('RUN python3 /opt/hermes/patches/patch_camofox_access_key.py', dockerfile)
        self.assertNotIn('run_install_patch "$INSTALL_DIR/patches/patch_camofox_access_key.py"', entrypoint)

    def test_entrypoint_no_longer_owns_cdp_broker_lifecycle(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertNotIn('continuing without main broker', entrypoint)
        self.assertNotIn('broker_is_listening()', entrypoint)
        self.assertNotIn('browserless-cdp-broker.py', entrypoint)
        self.assertNotIn('broker_pid=$!', entrypoint)
        self.assertNotIn('Browserless main CDP broker exited during startup; see $broker_log" >&2\n            exit 1', entrypoint)
        self.assertNotIn('Browserless main CDP broker did not become ready; see $broker_log" >&2\n        exit 1', entrypoint)

    def test_compose_uses_upstream_dashboard_env_contract(self):
        for path in [
            COMPOSE_PATH,
            TIER_COMPOSE_DIR / 'compose.l0.yml',
            TIER_COMPOSE_DIR / 'compose.l1.yml',
            TIER_COMPOSE_DIR / 'compose.l2.yml',
        ]:
            with self.subTest(path=path.name):
                compose = read_text(path)

                self.assertIn('HERMES_DASHBOARD: ${HERMES_DASHBOARD:-1}', compose)
                self.assertIn('HERMES_DASHBOARD_HOST: 0.0.0.0', compose)
                self.assertIn('HERMES_DASHBOARD_INSECURE: ${HERMES_DASHBOARD_INSECURE:-1}', compose)
                self.assertIn('HERMES_DASHBOARD_BASIC_AUTH_USERNAME:', compose)
                self.assertIn('HERMES_DASHBOARD_BASIC_AUTH_PASSWORD:', compose)
                self.assertIn('HERMES_DASHBOARD_BASIC_AUTH_SECRET:', compose)
                self.assertIn('SPARTAN_HERMES_RUN_UID: ${SPARTAN_HERMES_RUN_UID:-}', compose)
                self.assertIn('SPARTAN_HERMES_RUN_GID: ${SPARTAN_HERMES_RUN_GID:-}', compose)
                self.assertNotIn('SPARTAN_HERMES_DATA_GID:', compose)
                self.assertNotIn('SPARTAN_HERMES_DATA_GROUP_REPAIR:', compose)

    def test_tier_compose_allows_user_level_python_and_node_installs(self):
        for name in ('compose.l0.yml', 'compose.l1.yml', 'compose.l2.yml'):
            with self.subTest(name=name):
                compose = read_text(TIER_COMPOSE_DIR / name)

                self.assertIn('PYTHONUSERBASE: /opt/data/.local', compose)
                self.assertIn('PIP_TARGET: /opt/data/hermes-extra-site', compose)
                self.assertIn('PYTHONPATH: /opt/hermes/bootstrap:/opt/data/hermes-extra-site:/opt/hermes', compose)
                self.assertIn('npm_config_prefix: /opt/data/.local', compose)
                self.assertIn('NPM_CONFIG_PREFIX: /opt/data/.local', compose)
                self.assertNotIn('PIP_USER: "1"', compose)

    def test_full_compose_allows_user_level_python_and_node_installs(self):
        compose = read_text(COMPOSE_PATH)

        self.assertIn('PYTHONUSERBASE: /opt/data/.local', compose)
        self.assertIn('PIP_TARGET: /opt/data/hermes-extra-site', compose)
        self.assertIn('PYTHONPATH: /opt/hermes/bootstrap:/opt/data/hermes-extra-site:/opt/hermes', compose)
        self.assertIn('npm_config_prefix: /opt/data/.local', compose)
        self.assertIn('NPM_CONFIG_PREFIX: /opt/data/.local', compose)
        self.assertNotIn('PIP_USER: "1"', compose)

    def test_dockerfile_does_not_wire_removed_post_tool_patch(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertNotIn('patch_post_tool_empty_retry.py', dockerfile)

    def test_entrypoint_does_not_reapply_removed_post_tool_patch(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertNotIn('patch_post_tool_empty_retry.py', entrypoint)

    def test_removed_post_tool_patch_is_absent_from_local_patch_set(self):
        self.assertFalse(REMOVED_PATCH_PATH.exists())


if __name__ == '__main__':
    unittest.main()

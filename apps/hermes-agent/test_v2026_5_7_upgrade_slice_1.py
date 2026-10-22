import pathlib
import unittest


HERMES_DIR = pathlib.Path(__file__).parent
REPO_ROOT = HERMES_DIR.parents[1]
DOCKERFILE_PATH = HERMES_DIR / 'Dockerfile'
ENTRYPOINT_PATH = HERMES_DIR / 'entrypoint-wrapper.sh'
RUNTIME_DOCS_DIR = HERMES_DIR / 'runtime-docs'
PACKAGE_INSTALLS_DOC_PATH = RUNTIME_DOCS_DIR / 'local' / 'package-installs.md'
CAMOFOX_DOC_PATH = RUNTIME_DOCS_DIR / 'local' / 'camofox.md'
COMPOSE_PATH = REPO_ROOT / 'infra' / 'compose' / 'compose.yml'
PRIVATE_COMPOSE_PATH = REPO_ROOT / 'private.example' / 'compose.local.yml'
TIER_COMPOSE_DIR = REPO_ROOT / 'infra' / 'compose' / 'tiers'
REMOVED_PATCH_PATH = HERMES_DIR / 'patches' / 'patch_post_tool_empty_retry.py'
ARCHIVED_CAMOFOX_PATCH_PATH = (
    HERMES_DIR / 'patches' / 'archive' / '2026-06-30-patch_camofox_access_key.py'
)
ARCHIVED_GATEWAY_RETRY_PATCH_PATH = (
    HERMES_DIR / 'patches' / 'archive' / '2026-06-30-patch_gateway_retryable_startup.py'
)


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8')


class HermesMainPinUpgradeTests(unittest.TestCase):
    def test_uses_pinned_main_base_image(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        instructions = [line.strip() for line in dockerfile.splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
        pinned_default = (
            'ARG HERMES_BASE_IMAGE=docker.io/nousresearch/hermes-agent:main@'
            'sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde'
        )
        self.assertEqual(
            [line for line in instructions if line.startswith('ARG HERMES_BASE_IMAGE')],
            [pinned_default],
        )
        self.assertEqual(
            [line for line in instructions if line.startswith('FROM ')],
            ['FROM ${HERMES_BASE_IMAGE}'],
        )
        self.assertLess(instructions.index(pinned_default),
                        instructions.index('FROM ${HERMES_BASE_IMAGE}'))
        self.assertIn(
            'Upstream main revision: e8c97320ac8691d4de92af49f98459f9ef9ddb08',
            dockerfile,
        )

        self.assertNotIn('FROM node:22-bookworm-slim AS node22', dockerfile)
        self.assertNotIn('apt-get upgrade', dockerfile)

    def test_dockerfile_keeps_upstream_init_as_pid_1(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn('ENTRYPOINT ["/init", "/opt/hermes/entrypoint-wrapper.sh"]', dockerfile)
        self.assertNotIn('ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]', dockerfile)

    def test_dockerfile_patches_stage2_cdp_env_before_services(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertIn('python3 /opt/hermes/patches/patch_stage2_cdp_env.py', dockerfile)
        self.assertIn('python3 /opt/hermes/patches/patch_gateway_service_cdp_env.py', dockerfile)
        self.assertIn('python3 /opt/hermes/patches/patch_s6_service_permissions.py', dockerfile)
        self.assertIn('python3 /opt/hermes/patches/patch_runtime_identity_drop.py', dockerfile)
        self.assertIn('python3 /opt/hermes/patches/patch_route_env_placeholders.py', dockerfile)
        self.assertNotIn('python3 /opt/hermes/patches/patch_startup_overlay_precedence.py', dockerfile)
        self.assertLess(
            dockerfile.index('patch_stage2_runtime_owner.py'),
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

        self.assertNotIn('runtime-docs/ /opt/hermes/runtime-docs/', dockerfile)
        self.assertNotIn('RUN chown -R root:root /opt/hermes', dockerfile)
        self.assertIn('chmod 0755 /opt/hermes/bootstrap', dockerfile)
        self.assertIn('chmod 0644 /opt/hermes/.venv/.lock', dockerfile)
        self.assertIn('audit-install-tree.py /opt/hermes', dockerfile)
        self.assertNotIn('"/opt/hermes/bootstrap/cdp-patch.js"', dockerfile)
        self.assertNotIn('/opt/hermes-plugins', dockerfile)
        self.assertNotIn('RUN chown -R 10000:10000 /opt/hermes/.venv', dockerfile)
        self.assertNotIn('RUN chown -R 1000:1000 /opt/hermes/.venv', dockerfile)

    def test_dockerfile_bakes_manim_native_and_tex_dependencies(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        for package in (
            'libcairo2-dev',
            'libpango1.0-dev',
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
        self.assertIn('export PIP_TARGET="${PIP_TARGET:-/opt/data/hermes-extra-site-py314}"', entrypoint)
        self.assertIn('export PYTHONPATH="${PYTHONPATH:-/opt/hermes/bootstrap:/opt/data/hermes-extra-site-py314:/opt/hermes}"', entrypoint)
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

    def test_runtime_docs_are_read_only_compose_mounts(self):
        dockerfile = read_text(DOCKERFILE_PATH)
        entrypoint = read_text(ENTRYPOINT_PATH)
        compose = read_text(COMPOSE_PATH)
        private_compose = read_text(PRIVATE_COMPOSE_PATH)
        package_doc = read_text(PACKAGE_INSTALLS_DOC_PATH)
        camofox_doc = read_text(CAMOFOX_DOC_PATH)

        self.assertIn(
            '../../apps/hermes-agent/runtime-docs:/opt/data/brain/runtime_docs:ro',
            compose,
        )
        self.assertIn(
            '../../private/docs/hermes-shared:/opt/data/brain/runtime_docs/local/private:ro',
            private_compose,
        )
        # The private overlay's source and mountpoint are installation-local,
        # not published checkout fixtures. Assert both read-only mount contracts
        # above without requiring or reading private operator state.
        self.assertNotIn('runtime-docs/ /opt/hermes/runtime-docs/', dockerfile)
        self.assertNotIn('runtime_docs_dir=', entrypoint)
        self.assertNotIn('runtime_doc_src=', entrypoint)
        self.assertNotIn('Runtime doc copied', entrypoint)
        self.assertIn('uv pip install --python .venv/bin/python package==version', package_doc)
        self.assertIn('npm install package@version', package_doc)
        self.assertIn('sg-whitelist-domain 15d api.nuget.org', package_doc)
        for tool in (
            'browser_navigate',
            'browser_snapshot',
            'browser_click',
            'browser_type',
            'browser_scroll',
            'browser_back',
            'browser_press',
            'browser_get_images',
            'browser_vision',
            'browser_console',
        ):
            with self.subTest(tool=tool):
                self.assertIn(f'`{tool}`', camofox_doc)
        self.assertIn('POST /tabs/{tabId}/spartan/viewport', camofox_doc)
        self.assertIn('Contexts / `userId` sessions | 8', camofox_doc)
        self.assertIn('Tabs per context | 4', camofox_doc)
        self.assertIn('Tabs across Camofox | 12', camofox_doc)
        self.assertIn('Recycle backoff | 0 s, 30 s, 2 min, 10 min, 30 min', camofox_doc)
        self.assertIn('`POST /tabs/open` is intentionally disabled with HTTP 410', camofox_doc)
        self.assertIn('invalidate every active Hermes and operator', camofox_doc)

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
        self.assertIn('for key in HERMES_MEET_CDP_PROFILE HERMES_MEET_CDP_URL BROWSER_CDP_LAUNCH_URL BROWSER_CDP_URL BROWSER_CDP_MAIN_URL;', entrypoint)
        self.assertIn('rm -f "${env_dir}/${key}"', entrypoint)
        self.assertNotIn('> "${env_dir}/BROWSER_CDP_URL"', entrypoint)
        self.assertIn('CAMOFOX_API_KEY', entrypoint)
        self.assertIn('CAMOFOX_ACCESS_KEY', entrypoint)
        self.assertIn('printf \'%s\' "${!key}" > "${env_dir}/${key}"', entrypoint)
        self.assertIn('camofox_browser_mode_enabled()', entrypoint)
        self.assertNotIn('browserless-cdp-url.js', entrypoint)
        self.assertIn('unset BROWSER_CDP_URL', entrypoint)
        self.assertLess(entrypoint.rfind(persist_call), entrypoint.rfind(delegation))

    def test_entrypoint_keeps_compose_openai_base_url_authoritative(self):
        entrypoint = read_text(ENTRYPOINT_PATH)

        sync_call = 'sync_openai_base_url_for_with_contenv'
        persist_call = 'persist_cdp_env_for_with_contenv'
        delegation = 'exec /opt/hermes/docker/main-wrapper.sh "$@"'

        self.assertIn('sync_openai_base_url_for_with_contenv()', entrypoint)
        self.assertIn('local desired="${OPENAI_BASE_URL:-}"', entrypoint)
        self.assertIn('local env_file="$HERMES_HOME/.env"', entrypoint)
        self.assertIn('rm -f "${env_dir}/OPENAI_BASE_URL"', entrypoint)
        self.assertIn('printf \'%s\' "$desired" > "${env_dir}/OPENAI_BASE_URL"', entrypoint)
        self.assertIn('/^OPENAI_BASE_URL=/', entrypoint)
        self.assertIn('print "OPENAI_BASE_URL=" desired', entrypoint)
        self.assertIn('[entrypoint] Synced OPENAI_BASE_URL in ${env_file}', entrypoint)
        self.assertLess(entrypoint.rfind(persist_call), entrypoint.rfind(sync_call))
        self.assertLess(entrypoint.rfind(sync_call), entrypoint.rfind(delegation))

    def test_camofox_access_key_patch_is_archived(self):
        dockerfile = read_text(DOCKERFILE_PATH)
        entrypoint = read_text(ENTRYPOINT_PATH)

        self.assertNotIn('python3 /opt/hermes/patches/patch_camofox_access_key.py', dockerfile)
        self.assertNotIn('run_install_patch "$INSTALL_DIR/patches/patch_camofox_access_key.py"', entrypoint)
        self.assertTrue(ARCHIVED_CAMOFOX_PATCH_PATH.exists())

    def test_gateway_retryable_startup_patch_is_archived(self):
        dockerfile = read_text(DOCKERFILE_PATH)

        self.assertNotIn('python3 /opt/hermes/patches/patch_gateway_retryable_startup.py', dockerfile)
        self.assertTrue(ARCHIVED_GATEWAY_RETRY_PATCH_PATH.exists())

    def test_compose_maps_camofox_access_key_to_hermes_api_key(self):
        for path in [
            COMPOSE_PATH,
            TIER_COMPOSE_DIR / 'compose.l1.yml',
            TIER_COMPOSE_DIR / 'compose.l2.yml',
        ]:
            with self.subTest(path=path.name):
                compose = read_text(path)

                self.assertIn('CAMOFOX_API_KEY: ${CAMOFOX_ACCESS_KEY:-', compose)
                self.assertIn('CAMOFOX_ACCESS_KEY:', compose)

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
                self.assertIn(
                    'SPARTAN_HERMES_RUN_UID: ${SPARTAN_HERMES_RUN_UID:-${HERMES_UID:-${PUID:-1000}}}',
                    compose,
                )
                self.assertIn(
                    'SPARTAN_HERMES_RUN_GID: ${SPARTAN_HERMES_RUN_GID:-${HERMES_GID:-${PGID:-1000}}}',
                    compose,
                )
                self.assertIn('HERMES_UID: ${HERMES_UID:-}', compose)
                self.assertIn('HERMES_GID: ${HERMES_GID:-}', compose)
                self.assertNotIn('SPARTAN_HERMES_DATA_GID:', compose)
                self.assertNotIn('SPARTAN_HERMES_DATA_GROUP_REPAIR:', compose)

    def test_tier_compose_allows_user_level_python_and_node_installs(self):
        for name in ('compose.l0.yml', 'compose.l1.yml', 'compose.l2.yml'):
            with self.subTest(name=name):
                compose = read_text(TIER_COMPOSE_DIR / name)

                self.assertIn('PYTHONUSERBASE: /opt/data/.local', compose)
                self.assertIn('PIP_TARGET: /opt/data/hermes-extra-site-py314', compose)
                self.assertIn('PYTHONPATH: /opt/hermes/bootstrap:/opt/data/hermes-extra-site-py314:/opt/hermes', compose)
                self.assertIn('npm_config_prefix: /opt/data/.local', compose)
                self.assertIn('NPM_CONFIG_PREFIX: /opt/data/.local', compose)
                self.assertNotIn('PIP_USER: "1"', compose)

    def test_tier_spartan_route_mounts_cache_lineage_plugin(self):
        compose = read_text(TIER_COMPOSE_DIR / 'compose.spartan-route.yml')

        self.assertIn('OPENAI_BASE_URL: http://spartan-route:18790/v1', compose)
        self.assertIn(
            '../../../infra/hermes-plugins/spartan-cache-lineage:/opt/data/plugins/spartan-cache-lineage:ro',
            compose,
        )
        self.assertNotIn(':/opt/data/plugins:ro', compose)

    def test_full_compose_uses_spartan_route_as_base_router(self):
        compose = read_text(COMPOSE_PATH)

        self.assertIn('spartan-route:', compose)
        self.assertIn('container_name: spartan_gate_spartan_route', compose)
        self.assertIn('OPENAI_BASE_URL: http://spartan-route:18790/v1', compose)
        self.assertIn('spartan-route:', compose.split('depends_on:', 1)[1])
        self.assertNotIn('container_name: spartan_gate_clawroute', compose)
        self.assertIn('../hermes-plugins/spartan-cache-lineage:/opt/data/plugins/spartan-cache-lineage:ro', compose)

    def test_full_compose_allows_user_level_python_and_node_installs(self):
        compose = read_text(COMPOSE_PATH)

        self.assertIn('PYTHONUSERBASE: /opt/data/.local', compose)
        self.assertIn('PIP_TARGET: /opt/data/hermes-extra-site-py314', compose)
        self.assertIn('PYTHONPATH: /opt/hermes/bootstrap:/opt/data/hermes-extra-site-py314:/opt/hermes', compose)
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

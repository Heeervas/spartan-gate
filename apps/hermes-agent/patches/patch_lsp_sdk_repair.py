"""Repair an absent TypeScript companion SDK even when its server is installed."""
import ast
from pathlib import Path

TARGET = Path('/opt/hermes/agent/lsp/install.py')
MARKER = '# Spartan Gate: a server binary alone does not prove its companion SDK is usable.'
HELPER = '''# Spartan Gate: a server binary alone does not prove its companion SDK is usable.
def _companion_ready(pkg, binary):
    if pkg not in {"typescript-language-server", "@vue/language-server"}:
        return True
    from agent.lsp.servers import _node_modules_trees, _typescript_sdk_dir
    sdk = _typescript_sdk_dir(_node_modules_trees(binary, None))
    return bool(sdk and (Path(sdk) / "tsserver.js").is_file())


'''
TRY = '''def try_install(pkg: str, strategy: str = "auto") -> Optional[str]:
    recipe = INSTALL_RECIPES.get(pkg, {})
    if strategy != "auto":
        binary = _existing_binary(recipe.get("bin", pkg))
        return binary if binary and _companion_ready(pkg, binary) else None
    # Install results and locks belong to the profile whose staging tree they describe.
    key = (str(hermes_lsp_bin_dir().parent.resolve()), pkg)
    with _install_lock_meta:
        lock = _install_locks.setdefault(key, threading.Lock())
    with lock:
        if key not in _install_results:
            _install_results[key] = _do_install(pkg)
        return _install_results[key]
'''
DO = '''def _do_install(pkg: str) -> Optional[str]:
    recipe = INSTALL_RECIPES.get(pkg)
    if recipe is None:
        return shutil.which(pkg)
    strategy = recipe.get("strategy", "manual")
    bin_name = recipe.get("bin", pkg)
    if existing := _existing_binary(bin_name):
        if _companion_ready(pkg, existing):
            return existing
        # Preserve the installed server; install only the supported JavaScript SDK.
        repaired = _install_npm(TYPESCRIPT_SDK_PKG, bin_name)
        return repaired if repaired and _companion_ready(pkg, repaired) else None
    if strategy == "manual":
        return None
    installer = _INSTALLERS.get(strategy)
    if installer is None:
        return None
    binary = installer(recipe, bin_name)
    return binary if binary and _companion_ready(pkg, binary) else None
'''
STATUS = '''def detect_status(pkg: str) -> str:
    recipe = INSTALL_RECIPES.get(pkg)
    binary = _existing_binary(recipe.get("bin", pkg) if recipe else pkg)
    if binary and _companion_ready(pkg, binary):
        return "installed"
    return "manual-only" if recipe and recipe.get("strategy") == "manual" else "missing"
'''


def _replace(source, name, replacement):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise ValueError('LSP installer function missing or ambiguous')
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + ''.join(lines[node.end_lineno:])


def patch_text(source):
    if MARKER in source:
        if any(source.count(part) != 1 for part in [HELPER, TRY, DO, STATUS]):
            raise ValueError('incomplete LSP companion SDK repair')
        return source
    for anchor in ['if pkg in _install_results:', 'if existing := _existing_binary(bin_name):', 'return "manual-only" if recipe']:
        if source.count(anchor) != 1:
            raise ValueError('LSP installer anchor changed')
    result = _replace(source, 'try_install', TRY)
    result = _replace(result, '_do_install', HELPER + DO)
    result = _replace(result, 'detect_status', STATUS)
    result = result.replace('_install_locks: Dict[str, threading.Lock]', '_install_locks: Dict[tuple[str, str], threading.Lock]')
    result = result.replace('_install_results: Dict[str, Optional[str]]', '_install_results: Dict[tuple[str, str], Optional[str]]')
    compile(result, str(TARGET), 'exec')
    return result


def main():
    source = TARGET.read_text()
    result = patch_text(source)
    if result != source:
        TARGET.write_text(result)
    print('OK: TypeScript companion SDK readiness and repair are profile scoped')


if __name__ == '__main__':
    main()

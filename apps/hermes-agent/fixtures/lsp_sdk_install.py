"""Pinned native installer functions for offline regression tests."""
def try_install(pkg: str, strategy: str = "auto") -> Optional[str]:
    """Try to install ``pkg``; return the binary path or ``None``.

    Only ``"auto"`` installs; ``"manual"``/``"off"`` just probe for an existing
    binary.  Results are cached per package and concurrent calls are serialized.
    """
    if strategy != "auto":
        return _existing_binary(INSTALL_RECIPES.get(pkg, {}).get("bin", pkg))
    if pkg in _install_results:
        return _install_results[pkg]
    with _install_lock_meta:
        lock = _install_locks.setdefault(pkg, threading.Lock())
    with lock:
        if pkg not in _install_results:
            _install_results[pkg] = _do_install(pkg)
        return _install_results[pkg]


def _do_install(pkg: str) -> Optional[str]:
    recipe = INSTALL_RECIPES.get(pkg)
    if recipe is None:
        return shutil.which(pkg)  # not in our registry — best-effort: just probe PATH
    strategy = recipe.get("strategy", "manual")
    bin_name = recipe.get("bin", pkg)
    if existing := _existing_binary(bin_name):
        return existing
    if strategy == "manual":
        logger.debug("[install] %s requires manual install (recipe=%s)", pkg, recipe)
        return None
    installer = _INSTALLERS.get(strategy)
    if installer is None:
        logger.warning("[install] unknown strategy %r for %s", strategy, pkg)
        return None
    return installer(recipe, bin_name)


def detect_status(pkg: str) -> str:
    """Return ``installed``, ``missing``, or ``manual-only`` (for ``hermes lsp status``; spawns nothing)."""
    recipe = INSTALL_RECIPES.get(pkg)
    if _existing_binary(recipe.get("bin", pkg) if recipe else pkg):
        return "installed"
    return "manual-only" if recipe and recipe.get("strategy") == "manual" else "missing"


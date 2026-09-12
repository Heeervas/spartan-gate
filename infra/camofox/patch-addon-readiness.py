#!/usr/bin/env python3
"""Make upstream temporary-addon installation once-per-launch."""

from __future__ import annotations
import hashlib
import os
import pathlib
import tempfile
import zipfile

ARCHIVE = pathlib.Path('/root/.cache/camoufox/browser/omni.ja')
ENTRY = 'chrome/browser/content/browser/browser-init.js'
SOURCE_SHA256 = 'e8f6df8c63ba08a0de19d30e5a64fd7b1a8bb7566237a9ccdc1ef1940b4c49b3'
PATCHED_SHA256 = '0c39b9cfa2040971a17092848db9ea40686df3ff789696b56e6c9eb5490afe85'
MARKER = 'SPARTAN_ADDON_READINESS_V1'

OLD = '''    // Install addons if specified
    let addonPaths = ChromeUtils.camouGetStringList("addons");
    if (addonPaths?.length) {
      Promise.all(addonPaths.map(path => this.installTemporaryAddon(path)))
        .then(addons => ChromeUtils.camouDebug("Installed " + addons.length + " addon(s)"))
        .catch(e => ChromeUtils.camouDebug("Failed to install addons: " + e));
    }
'''
NEW = '''    // SPARTAN_ADDON_READINESS_V1: this launch profile is shared by every
    // chrome window. Claim installation synchronously so later windows cannot
    // restart an already-loading or active extension. A failed launch is
    // discarded by the application readiness gate.
    let addonPaths = ChromeUtils.camouGetStringList("addons");
    const addonInstallStartedPref = "camoufox.spartan.addonInstallStarted";
    if (addonPaths?.length && !Services.prefs.getBoolPref(addonInstallStartedPref, false)) {
      Services.prefs.setBoolPref(addonInstallStartedPref, true);
      Promise.all(addonPaths.map(path => this.installTemporaryAddon(path)))
        .then(addons => ChromeUtils.camouDebug("Installed " + addons.length + " addon(s)"))
        .catch(e => ChromeUtils.camouDebug("Failed to install addons: " + e));
    }
'''


def read_entry(path: pathlib.Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        if archive.namelist().count(ENTRY) != 1:
            raise RuntimeError('expected exactly one browser-init entry')
        return archive.read(ENTRY)


def rewrite(path: pathlib.Path, replacement: bytes) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, name = tempfile.mkstemp(prefix='.omni.ja.', dir=path.parent)
    os.close(fd)
    temp = pathlib.Path(name)
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(temp, 'w') as target:
            target.comment = source.comment
            for info in source.infolist():
                target.writestr(info, replacement if info.filename == ENTRY else source.read(info.filename))
        os.chmod(temp, mode)
        with temp.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    path = pathlib.Path(os.environ.get('CAMOFOX_BROWSER_OMNI', ARCHIVE))
    raw = read_entry(path)
    source = raw.decode()
    if MARKER in source:
        if hashlib.sha256(raw).hexdigest() != PATCHED_SHA256:
            raise RuntimeError('unexpected patched browser-init source hash')
        return
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('unexpected browser-init source hash')
    if source.count(OLD) != 1:
        raise RuntimeError('expected exactly one addon-install anchor')
    patched = source.replace(OLD, NEW)
    rewrite(path, patched.encode())


if __name__ == '__main__':
    main()

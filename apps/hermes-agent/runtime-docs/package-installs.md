# Package Installs In Hermes

Use project-local sandboxes for extra tools. Do not install into `/opt/hermes`,
`/usr`, `/usr/local`, `/etc`, or `/var/lib` from a live Hermes session.

## Python

Create a local virtual environment and install through `uv` with pinned versions:

```sh
uv venv .venv
uv pip install --python .venv/bin/python package==version
.venv/bin/python -c "import package"
```

Pure Python packages and packages with compatible wheels are expected to work.
Packages that need missing native headers or system libraries require an image
change by an operator.

## Node

Use project-local installs with pinned versions:

```sh
npm init -y
npm install package@version
npx --yes package@version ...
```

If a global-style command is needed, keep the prefix inside the workspace:

```sh
export NPM_CONFIG_PREFIX="$PWD/.npm-global"
export PATH="$NPM_CONFIG_PREFIX/bin:$PATH"
npm install -g package@version
```

## Network And Native Dependencies

Package traffic must go through Spartan Gate's proxy and DNS policy. PyPI,
pythonhosted, npm, GitHub, and Debian/Ubuntu mirrors are the generic baseline.
For package postinstall downloads from other domains, inspect the failed domain
first and add the narrowest private temporary allowlist entry, for example:

```sh
sg-whitelist-domain 15d api.nuget.org
```

Keep `apt-get install`, Docker socket access, unpinned packages, persistent
global PATH changes, credential-bearing operations, and runtime writes outside
`/opt/data` blocked unless an operator explicitly performs the image or host
change.

#!/usr/bin/env python3
"""Operator Google login: native Gog PKCE, shared compatible grants, native files."""
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "v0.48.0"
SERVICES = "gmail,calendar,chat,classroom,drive,docs,slides,contacts,tasks,sheets,people,forms,appscript,ads"
SCOPE = "https://www.googleapis.com/auth/"
EXTRA_SCOPES = [SCOPE + name for name in (
    "adwords", "analytics.readonly", "analytics.edit", "tagmanager.readonly",
    "tagmanager.edit.containers", "tagmanager.edit.containerversions",
    "tagmanager.publish", "tagmanager.delete.containers", "tagmanager.manage.accounts",
    "tagmanager.manage.users", "script.projects", "script.deployments",
    "script.processes", "script.metrics",
)]
WORKSPACE_SCOPES = [SCOPE + name for name in (
    "gmail.readonly", "gmail.send", "gmail.modify", "calendar", "drive",
    "contacts.readonly", "spreadsheets", "documents",
)]
LIMIT = 65536
REFRESH_PROGRAM = """
import json, sys
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
credentials = Credentials.from_authorized_user_info(json.load(sys.stdin))
credentials.refresh(Request())
payload = json.loads(credentials.to_json())
payload['type'] = 'authorized_user'
print('SG_GOOGLE_AUTH_JSON:' + json.dumps(payload))
"""


class AuthError(Exception):
    """Actionable, public-safe operator error."""


def load_json(path, limit=LIMIT):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise AuthError(f"Fichero demasiado grande: {Path(path).name}")
    return json.loads(raw)


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values


def client_info(payload):
    client = payload.get("installed", payload.get("web", payload))
    if not client.get("client_id") or not client.get("client_secret"):
        raise AuthError("Cliente OAuth incompleto; conserva client_id y client_secret emparejados.")
    return {key: client[key] for key in ("client_id", "client_secret")}


def preflight_file(path):
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise AuthError(f"Ruta de credenciales con enlace simbólico: {path.name}")
    if not path.parent.is_dir() or not os.access(path.parent, os.W_OK):
        raise AuthError(f"Directorio de credenciales no escribible: {path.parent}")
    if path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
        raise AuthError(f"Propietario/tipo de credenciales incompatible: {path.name}")
    backup = path.with_name(path.name + ".sg-previous")
    if backup.is_symlink() or (backup.exists() and not backup.is_file()):
        raise AuthError(f"Backup de credenciales inseguro: {backup.name}")


def atomic_bytes(path, data):
    fd, temp = tempfile.mkstemp(prefix=".sg-google-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def publish_files(files):
    """Preflight whole group; retain one bounded backup per native file."""
    encoded = {Path(p): (json.dumps(v, indent=2) + "\n").encode() for p, v in files.items()}
    for path in encoded:
        preflight_file(path)
    before = {p: p.read_bytes() if p.exists() else None for p in encoded}
    written = []
    try:
        for path, data in encoded.items():
            if before[path] is not None:
                atomic_bytes(path.with_name(path.name + ".sg-previous"), before[path])
            atomic_bytes(path, data)
            written.append(path)
    except OSError:
        for path in reversed(written):
            if path.read_bytes() == encoded[path]:
                if before[path] is None:
                    path.unlink()
                else:
                    atomic_bytes(path, before[path])
        raise AuthError("No se pudieron guardar las credenciales; revisa permisos/espacio y los backups .sg-previous.") from None


@contextlib.contextmanager
def child(args, env):
    process = subprocess.Popen(args, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        yield process
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        process.stdin.close()
        process.stdout.close()


def command(args, env=None, input_data=None, timeout=90):
    with child(args, env) as process:
        try:
            output, _ = process.communicate(input_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AuthError("Tiempo de espera agotado; no quedan procesos de login pendientes.") from None
        if len(output) > LIMIT:
            raise AuthError("Salida de autenticación excesiva.")
        if process.returncode:
            code = re.search(rb"\b(invalid_grant|invalid_client|invalid_scope|access_denied|invalid_rapt)\b", output)
            reason = code.group().decode() if code else f"salida {process.returncode}"
            raise AuthError(f"Google/Gog: {reason}. Revisa cliente, permisos y consentimiento; no se ha revocado ningún acceso.")
        return output


def authorization_url(process):
    deadline = time.monotonic() + 30
    output = b""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(timeout=1):
                continue
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output += chunk
            if len(output) > LIMIT:
                break
            match = re.search(rb"https://accounts\.google\.com/[^\s]+\n", output)
            if match:
                return match.group().decode().strip()
    raise AuthError("Gog no generó el enlace de Google; revisa el cliente OAuth y el keyring.")


def validate_callback(raw, auth_url):
    auth = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query)
    expected = urllib.parse.urlsplit(auth["redirect_uri"][0])
    callback = urllib.parse.urlsplit(raw.strip())
    params = urllib.parse.parse_qs(callback.query)
    if (callback.scheme, callback.netloc, callback.path) != (expected.scheme, expected.netloc, expected.path):
        raise AuthError(f"Pega la URL que empieza por {urllib.parse.urlunsplit(expected)}; conserva ese HTTP/HTTPS, host y ruta.")
    if params.get("state") != auth.get("state"):
        raise AuthError("La URL pertenece a otra sesión. Pega la URL final del enlace mostrado ahora.")
    if "error" in params:
        raise AuthError("Google no concedió el acceso. Cancela y revisa el consentimiento.")
    if len(params.get("code", [])) != 1 or not params["code"][0]:
        raise AuthError("Falta el código: copia la URL final completa, incluidos code y state.")
    return raw.strip()


def manual_login(args, env):
    with child(args + ["--manual", "--redirect-uri", "http://localhost:1/", "--timeout", "10m"], env) as process:
        url = authorization_url(process)
        callback = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["redirect_uri"][0]
        print(f"\nAbre en Safari/Chrome (no navegador incrustado):\n\n{url}\n")
        print(f"Después pega la URL final COMPLETA. Debe empezar por:\n{callback}")
        print("Respeta HTTP/HTTPS. Si localhost no carga en el móvil, es normal: copia la barra de direcciones.\n")
        while True:
            raw = getpass.getpass("URL final (entrada oculta; Ctrl-C cancela): ")
            if len(raw) > LIMIT:
                print("URL demasiado larga; vuelve a copiarla.")
                continue
            try:
                value = validate_callback(raw, url)
                break
            except (AuthError, ValueError) as exc:
                print(exc)
        try:
            output, _ = process.communicate((value + "\n").encode(), timeout=90)
        except subprocess.TimeoutExpired:
            raise AuthError("Google no completó el intercambio. Vuelve a ejecutar el comando.") from None
        if process.returncode:
            code = re.search(rb"\b(invalid_grant|invalid_client|invalid_scope|access_denied)\b", output)
            raise AuthError("Intercambio rechazado: " + (code.group().decode() if code else "revisa consentimiento/cliente y genera un enlace nuevo"))


def export_grant(gog, account, client, env):
    with tempfile.TemporaryDirectory(prefix="sg-google-") as directory:
        path = Path(directory) / "grant.json"
        command([gog, "--client", client, "auth", "tokens", "export", account, "--out", str(path)], env)
        grant = load_json(path)
    if grant.get("email", "").lower() != account.lower() or grant.get("client", "default") != client:
        raise AuthError("La cuenta o cliente exportados no coinciden con el login seleccionado.")
    if not grant.get("refresh_token"):
        raise AuthError("Falta refresh token; repite el consentimiento con acceso offline.")
    return grant


def native_credentials(grant, client):
    payload = {"type": "authorized_user", **client, "refresh_token": grant["refresh_token"],
               "token_uri": "https://oauth2.googleapis.com/token", "scopes": grant["scopes"]}
    # Native exports use access_token_expires_at; ignore their optional cache.
    # Only our forced-refresh result has an expiry suitable for publication.
    if grant.get("access_token") and grant.get("expiry"):
        payload.update(token=grant["access_token"], expiry=grant["expiry"])
    return payload


def refresh_grant(grant, client):
    docker = os.environ.get("DOCKER_BIN", "docker")
    container = os.environ.get("SPARTAN_HERMES_CONTAINER", "spartan_gate_hermes")
    raw = command([docker, "exec", "-i", "-u", str(os.getuid()), "-e", "HOME=/opt/data",
                   container, "python3", "-c", REFRESH_PROGRAM],
                  input_data=json.dumps(native_credentials(grant, client)).encode())
    # Hermes bootstrap can emit audit diagnostics before the native result.
    _, marker, payload = raw.rpartition(b"SG_GOOGLE_AUTH_JSON:")
    if not marker:
        raise AuthError("Hermes no devolvió el resultado de refresh; revisa su entorno Google.")
    fresh = json.loads(payload)
    grant.update(access_token=fresh["token"], expiry=fresh["expiry"])
    return grant


def state_path(root, value):
    path = Path(value)
    if not path.is_relative_to("/state") or ".." in path.parts:
        raise AuthError("El binding MCP debe estar bajo /state.")
    return root / path.relative_to("/state")


def targets(settings, root, home, main_client, workspace):
    result = []
    if workspace:
        path = home / "google_client_secret.json"
        client = client_info(load_json(path)) if path.exists() else main_client
        result.append({"name": "Workspace", "client": client, "scopes": WORKSPACE_SCOPES,
                       "files": {home / "google_token.json": "adc", path: "client"}})
    for name, server in settings.get("mcpServers", {}).items():
        if not server.get("enabled", True):
            continue
        env = server.get("env", {})
        if "GTM_MCP_TOKEN_FILE" in env:
            path = state_path(root, env["GTM_MCP_CREDENTIALS_FILE"])
            result.append({"name": name, "client": client_info(load_json(path)),
                           "scopes": EXTRA_SCOPES[3:10],
                           "files": {state_path(root, env["GTM_MCP_TOKEN_FILE"]): "gtm"}})
        elif "analytics" in name and "GOOGLE_APPLICATION_CREDENTIALS" in env:
            path = state_path(root, env["GOOGLE_APPLICATION_CREDENTIALS"])
            result.append({"name": name, "client": client_info(load_json(path)),
                           "scopes": [SCOPE + "analytics.readonly", SCOPE + "analytics.edit"],
                           "files": {path: "adc"}})
    return result


def publish_grant(grant, group):
    missing = set(scope for target in group for scope in target["scopes"]) - set(grant.get("scopes", []))
    if missing:
        raise AuthError("Faltan scopes concedidos: " + ", ".join(sorted(missing)))
    files = {}
    for target in group:
        for path, kind in target["files"].items():
            if kind == "client":
                files[path] = {"installed": {**target["client"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]}}
            elif kind == "gtm":
                files[path] = {"refresh_token": grant["refresh_token"], "scope": " ".join(grant["scopes"])}
            else:
                files[path] = native_credentials(grant, target["client"])
    publish_files(files)


def api_json(base, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["x-auth-token"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = response.read(LIMIT + 1)
        if len(result) > LIMIT:
            raise AuthError("Respuesta MCPHub excesiva.")
        return json.loads(result)
    except (urllib.error.URLError, ValueError):
        raise AuthError("MCPHub no aceptó la operación. Revisa su URL y el fichero de contraseña admin; los tokens guardados se conservan.") from None


def reload_backends(config, root, names):
    if not names:
        return
    base = config.get("GOOGLE_AUTH_MCPHUB_URL", "http://127.0.0.1:18793").rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise AuthError("La gestión MCPHub requiere su URL HTTP loopback; no se enviarán credenciales a otro host.")
    password_path = Path(config.get("GOOGLE_AUTH_MCPHUB_PASSWORD_FILE") or root / "bootstrap-password")
    password = password_path.read_text().strip()
    login = api_json(base, "/api/auth/login", {"username": "admin", "password": password})
    token = login.get("token") or login.get("data", {}).get("token")
    if not token:
        raise AuthError("MCPHub no devolvió sesión admin. Recarga manualmente solo los backends Google.")
    for name in names:
        response = api_json(base, "/api/servers/" + urllib.parse.quote(name, safe="") + "/reload", {}, token)
        if not response.get("success"):
            raise AuthError("No se pudo recargar " + name)
        print("  Recargado: " + name)


def verify_store_mapping(info, store):
    selected = next((Path(m["Source"]) for m in info["Mounts"]
                     if m["Destination"] == "/opt/data/.config/gogcli"), None)
    if selected is None or selected.resolve() != store.resolve():
        raise AuthError("SPARTAN_GOGCLI_DATA_PATH no coincide con el bind Gog de Hermes; corrige la ruta antes del login.")
    native = dict(item.split("=", 1) for item in info["Config"].get("Env", []) if "=" in item)
    expected = {"GOG_HOME": "", "GOG_DATA_DIR": "", "GOG_STATE_DIR": "", "GOG_CACHE_DIR": "",
                "GOG_CONFIG_DIR": "/opt/data/.config/gogcli", "XDG_CONFIG_HOME": "/opt/data/.config",
                "XDG_DATA_HOME": "/opt/data/.local/share", "XDG_STATE_HOME": "/opt/data/.local/state",
                "XDG_CACHE_HOME": "/opt/data/.cache"}
    if any(native.get(key) and native[key] != value for key, value in expected.items()):
        raise AuthError("Hermes usa rutas Gog/XDG personalizadas; alinea el layout persistente antes de compartir el login.")


def runtime(config):
    docker = os.environ.get("DOCKER_BIN", "docker")
    container = os.environ.get("SPARTAN_HERMES_CONTAINER", "spartan_gate_hermes")
    info = json.loads(command([docker, "inspect", container]))[0]
    if not info["State"]["Running"]:
        raise AuthError("Hermes debe estar arrancado.")
    home = next((Path(m["Source"]) for m in info["Mounts"] if m["Destination"] == "/opt/data"), None)
    if home is None:
        raise AuthError("No se pudo resolver el bind /opt/data de Hermes.")
    store = Path(config["SPARTAN_GOGCLI_DATA_PATH"])
    verify_store_mapping(info, store)
    gog = home / "tools/gogcli/0.48.0/gog"
    if not gog.is_file():
        raise AuthError("Falta el runtime Gog0.48.0 compartido; actualiza Gog antes del login.")
    native = command([docker, "exec", "-u", str(os.getuid()), "-e", "HOME=/opt/data", container, "gog", "--version"])
    version = command([str(gog), "--version"])
    if not version.startswith(VERSION.encode()) or native != version:
        raise AuthError("El Gog del asistente y el de Hermes deben ser la misma versión0.48.0.")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOG_", "XDG_"))}
    env.update(HOME=str(home), GOG_CONFIG_DIR=str(store), XDG_DATA_HOME=str(home / ".local/share"),
               XDG_STATE_HOME=str(home / ".local/state"), XDG_CACHE_HOME=str(home / ".cache"),
               GOG_KEYRING_BACKEND="file", GOG_KEYRING_PASSWORD=config["GOG_KEYRING_PASSWORD"],
               DBUS_SESSION_BUS_ADDRESS="disabled:")
    return str(gog), home, env


def choose_mode(args):
    if args.check:
        return True
    print("\n[Enter/1] Reautenticar todo  [2] Reutilizar/refrescar  [q] Salir")
    choice = input("> ").strip().lower()
    if choice == "q":
        return False
    if choice not in ("", "1", "2"):
        raise AuthError("Selecciona 1, 2 o q.")
    args.check = choice == "2"
    return True


def login_group(config, check, runtime_info, identity, group):
    gog, env = runtime_info
    name, client = identity
    scopes = list(dict.fromkeys(EXTRA_SCOPES + [s for t in group for s in t["scopes"]]))
    scopes += [s for s in config.get("GOGCLI_EXTRA_SCOPES", "").split(",") if s and s not in scopes]
    if not check:
        with tempfile.TemporaryDirectory(prefix="sg-google-client-") as directory:
            path = Path(directory) / "client.json"
            atomic_bytes(path, json.dumps({"installed": client}).encode())
            command([gog, "--client", name, "auth", "credentials", "set", str(path)], env)
        manual_login([gog, "--client", name, "login", config["GOGCLI_ACCOUNT"],
                      "--services", SERVICES, "--extra-scopes", ",".join(scopes), "--force-consent"], env)
    grant = refresh_grant(export_grant(gog, config["GOGCLI_ACCOUNT"], name, env), client)
    if set(scopes) - set(grant.get("scopes", [])):
        raise AuthError("Faltan permisos Google solicitados; ejecuta sg-gog-login y concede todos los permisos del paso.")
    publish_grant(grant, group)
    print("  Grant guardado y reutilizado: " + ", ".join(t["name"] for t in group))


def run(config, args):
    for key in ("SPARTAN_GOGCLI_DATA_PATH", "GOGCLI_ACCOUNT", "GOGCLI_CLIENT_SECRET_PATH", "GOG_KEYRING_PASSWORD"):
        if not config.get(key):
            raise AuthError(key + " no está configurado.")
    gog, home, env = runtime(config)
    main = client_info(load_json(config["GOGCLI_CLIENT_SECRET_PATH"]))
    root = Path(config["SPARTAN_MCPHUB_STATE_PATH"]) if config.get("SPARTAN_MCPHUB_STATE_PATH") else None
    settings = load_json(root / "mcp_settings.json", limit=2 * 1024 * 1024) if root else {}
    selected = targets(settings, root, home, main, not args.skip_workspace)
    for target in selected:
        for path in target["files"]:
            preflight_file(path)
    groups = {json.dumps(main, sort_keys=True): []}
    for target in selected:
        groups.setdefault(json.dumps(target["client"], sort_keys=True), []).append(target)
    print("\nGoogle · " + config["GOGCLI_ACCOUNT"])
    print("Destinos: GogCLI" + "".join(", " + target["name"] for target in selected))
    print(f"Consentimientos necesarios si reautenticas todo: {len(groups)}")
    if not choose_mode(args):
        return
    for identity, group in groups.items():
        client = json.loads(identity)
        name = "default" if client == main else "sg-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        login_group(config, args.check, (gog, env), (name, client), group)
    reload_backends(config, root, [t["name"] for t in selected if t["name"] != "Workspace"])
    print("\nCredenciales listas. Los clientes refrescan los access tokens bajo demanda.")
    print("Si aparece invalid_grant: revisa Testing (7 días), revocación o política de sesión; un temporizador no lo corrige.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Comprobar/refrescar y redistribuir sin nuevo consentimiento")
    parser.add_argument("--skip-workspace", action="store_true", help="No modificar el Workspace nativo")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    path = repo / "private/env/local.env"
    if not path.exists():
        path = repo / ".env"
    try:
        if not args.check and not os.isatty(0):
            raise AuthError("El login necesita terminal interactivo (SSH/Termius); usa --check para comprobar sin consentimiento.")
        run(read_env(path), args)
        return 0
    except (AuthError, OSError, ValueError, KeyError, EOFError) as exc:
        message = str(exc) if isinstance(exc, AuthError) else "Falta configuración o acceso a un fichero/runtime; revisa los destinos antes de reintentar."
        print("Error: " + message)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado; los grants anteriores no se han revocado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

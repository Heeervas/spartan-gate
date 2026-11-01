"""Static operator documents and the explicit authenticated asset allowlist.

Canonical and legacy document paths share handlers, auth and private revalidation.
No API or provider routes belong in this registry.
"""

DASHBOARD_DOCUMENTS = {
    "/": "dashboard-codex.html",
    "/accounts": "dashboard-codex.html",
    "/activity": "dashboard.html",
    "/settings": "dashboard-config.html",
    "/dashboard": "dashboard.html",
    "/dashboard-codex": "dashboard-codex.html",
    "/dashboard-config": "dashboard-config.html",
}

DASHBOARD_ASSETS = frozenset({
    "dashboard.css", "dashboard.js", "dashboard-codex.js",
    "dashboard-config.js", "dashboard-details.js", "accounts.css", "shell.css",
})

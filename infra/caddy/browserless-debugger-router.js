let path = location.pathname;

if (path.endsWith("index.html")) {
  path = path.substring(0, path.length - 10);
}

if (!path.endsWith("/")) {
  path += "/";
}

const newUrl = location.origin + path + location.search + location.hash;

if (newUrl !== window.location.href) {
  window.history.pushState({}, "", newUrl);
}

function setStorageValue(key, value) {
  const storageKey = `browserless-debugger:${window.location.origin}${window.location.pathname}`;
  let state = {};

  try {
    state = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
  } catch {
    state = {};
  }

  state[key] = value;
  window.localStorage.setItem(storageKey, JSON.stringify(state));
}

function applyStealthEndpoint(url, route) {
  const normalized = String(route || "chromium").trim().toLowerCase();
  if (normalized === "chromium") {
    url.pathname = '/chromium';
  } else if (normalized === "chrome") {
    url.pathname = '/chrome';
  } else if (normalized === "chromium-stealth" || normalized === "chromium/stealth") {
    url.pathname = '/chromium/stealth';
  } else if (normalized === "chrome-stealth" || normalized === "chrome/stealth") {
    url.pathname = '/chrome/stealth';
  } else {
    url.pathname = '/stealth';
  }
}

function applySpartanPreset() {
  const params = new URLSearchParams(window.location.search);
  const flags = params.get("sgFlags");

  if (!flags) {
    return;
  }

  const quality = Number.parseInt(params.get("sgQuality") || "90", 10);
  const code = params.get("sgCode") || "export default async ({ page }: { page: Page }) => {\n  await page.goto('about:blank');\n};";

  setStorageValue("apiSettings", {
    baseURL: `${window.location.origin}/`,
    headless: params.get("sgHeadless") !== "false",
    stealth: true,
    blockAds: false,
    ignoreHTTPSErrors: false,
    quality: Number.isFinite(quality) ? quality : 90,
  });
  setStorageValue("editorTabs", [{ tabName: "Spartan Profile", code, active: true }]);

  const autostart = params.get("sgAutostart") !== "false";
  let attempts = 0;
  const interval = window.setInterval(() => {
    attempts += 1;
    const flagsInput = document.querySelector("#chrome-flags");

    if (flagsInput && flagsInput.value !== flags) {
      flagsInput.value = flags;
      flagsInput.dispatchEvent(new Event("input", { bubbles: true }));
      flagsInput.dispatchEvent(new Event("change", { bubbles: true }));
    }

    const runButton = document.querySelector("#run-button");
    if (flagsInput && runButton) {
      window.clearInterval(interval);
      if (autostart) {
        window.setTimeout(() => runButton.click(), 500);
      }
    }

    if (attempts > 80) {
      window.clearInterval(interval);
    }
  }, 250);
}

applySpartanPreset();

const spartanParams = new URLSearchParams(window.location.search);
const spartanUserDataDir = spartanParams.get("sgUserDataDir");
const spartanHeadless = spartanParams.get("sgHeadless") || "true";
const spartanStealthEndpoint = spartanParams.get("sgStealthEndpoint") !== "false";
const spartanStealthRoute = spartanParams.get("sgRoute") || spartanParams.get("sgStealthRoute") || "chromium";
const spartanUserAgent = spartanParams.get("sgUserAgent") || "";
const NativeWebSocket = window.WebSocket;

if (spartanUserDataDir || spartanStealthEndpoint) {
  function patchedWebSocket(url, protocols) {
    let target = url;

    try {
      const parsed = new URL(String(url), window.location.href);
      const rawLaunch = parsed.searchParams.get("launch");

      if (spartanStealthEndpoint) {
        applyStealthEndpoint(parsed, spartanStealthRoute);
        parsed.searchParams.set("headless", spartanHeadless);
        parsed.searchParams.set("stealth", "true");
        if (spartanUserAgent) {
          parsed.searchParams.set("userAgent", spartanUserAgent);
        }
        target = parsed.toString();
      }

      if (rawLaunch && spartanUserDataDir) {
        const launch = JSON.parse(rawLaunch);
        launch.userDataDir = spartanUserDataDir;
        if (spartanParams.get("sgIgnoreDefaultArgs") === "true") {
          launch.ignoreDefaultArgs = true;
        } else {
          delete launch.ignoreDefaultArgs;
        }
        launch.args = Array.isArray(launch.args)
          ? launch.args.filter((arg) => !arg.startsWith("--user-data-dir="))
          : [];
        parsed.searchParams.set("launch", JSON.stringify(launch));
        target = parsed.toString();
      }
    } catch {
      target = url;
    }

    return protocols === undefined
      ? new NativeWebSocket(target)
      : new NativeWebSocket(target, protocols);
  }

  patchedWebSocket.prototype = NativeWebSocket.prototype;
  Object.setPrototypeOf(patchedWebSocket, NativeWebSocket);
  window.WebSocket = patchedWebSocket;
}

const originalFetch = window.fetch.bind(window);

function isPageSession(session) {
  return session?.type === "page" && typeof session.webSocketDebuggerUrl === "string";
}

window.fetch = async function patchedFetch(input, init) {
  const response = await originalFetch(input, init);
  const requestUrl = typeof input === "string"
    ? input
    : input instanceof URL
      ? input.href
      : input?.url;

  if (!requestUrl) {
    return response;
  }

  const resolvedUrl = new URL(requestUrl, window.location.href);

  if (!resolvedUrl.pathname.endsWith("/sessions")) {
    return response;
  }

  const sessions = await response.clone().json().catch(() => null);

  if (!Array.isArray(sessions)) {
    return response;
  }

  const pageSessions = sessions
    .filter(isPageSession)
    .map((session) => ({
      ...session,
      browserWSEndpoint: session.webSocketDebuggerUrl,
    }));

  const headers = new Headers(response.headers);
  headers.delete("content-length");

  return new Response(JSON.stringify(pageSessions), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
};

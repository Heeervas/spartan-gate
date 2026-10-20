import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("before_provider_request", (event, ctx) => {
    if (ctx.model?.provider !== "spartan-route" || ctx.model.api !== "openai-completions") return;
    const payload = event.payload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;
    const key = (payload as Record<string, unknown>).prompt_cache_key;
    if (key != null && (typeof key !== "string" || key.trim())) return;
    const sessionId = ctx.sessionManager.getSessionId();
    if (typeof sessionId !== "string" || !sessionId.trim() || sessionId.length > 61) return;
    return { ...payload, prompt_cache_key: `pi:${sessionId}` };
  });
}

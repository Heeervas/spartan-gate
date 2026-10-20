# Codex image host: GPT-6 Luna

Status: configuration prepared; service activation pending the operator disk-floor exception.

The steroids image plugin requests gpt-image-2 through Route. Route wraps
that request in a separate Codex Responses request. Its default outer model,
gpt-5.5, was rejected as unsupported by the operator account.

A single synthetic image generation with CODEX_IMAGE_HOST_MODEL=gpt-6-luna
returned HTTP 200 and one nonempty image. The private Route service override
now sets that variable persistently. No container build or image download is
needed; only Route must be recreated. The plugin image model stays gpt-image-2.

The probe confirms image-tool compatibility for the tested account. It does
not establish pricing or image edit behavior.

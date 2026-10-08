# Live AI connection settings

`GET /api/settings/ai` and `PATCH /api/settings/ai` require a configured login password
and an authenticated session. Mutations use the existing same-origin checks and are
blocked during update maintenance. These endpoints are outside the seven AI tools;
the provider cannot view or change settings.

The public settings object contains `api_base_url`, `model`, `transcription_model`,
`transcription_base_url`, `api_key_set`, `transcription_api_key_set`, `enabled`,
`voice_enabled`, and `source` (`environment` or `web`). No plaintext or masked key is
returned. `transcription_api_key_set` indicates a separate transcription key; voice
may still work by inheriting the chat key.

PATCH accepts only optional string fields `api_base_url`, `model`,
`transcription_model`, `transcription_base_url`, `api_key`, and
`transcription_api_key`. Unknown fields, non-string values, invalid URLs, oversized
values, and control characters are rejected without reflecting request input.
Nonsecret fields cannot be null. API bases require HTTPS, except loopback HTTP for
local providers, and cannot contain credentials, query strings, or fragments.

Missing or blank secret values preserve the previous key. Explicit null removes the
key. Removing the chat key disables chat; removing a separate voice key restores
inheritance from chat. A blank transcription URL inherits the chat URL. A blank
transcription model disables voice. A blank chat model disables chat and voice.
Saving performs no external API calls and validates configuration shape rather than
provider credentials or capabilities.

Before the first save, settings read legacy Compose environment defaults. The first
save captures a complete configuration in a fixed SQLite settings table. Subsequent
reads use the stored row, so removing keys never revives an old environment key.
Updates merge and persist transactionally. Chat and transcription take one fresh
configuration snapshot per request; a concurrent save affects the next request.

Settings are stored in the same NAS data volume/database as inventory, survive app
restarts and container replacements, and are included in whole-data update backups
and restore. Inventory JSON exports and provider tools exclude the settings table.
Keep whole-data backups private since they contain saved API credentials.

The webpage never prefills or persists credentials in browser storage. Key fields
remain blank, with explicit clear controls. Saving changes refreshes assistant status
without reloading the page or restarting a container.

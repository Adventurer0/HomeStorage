# v2 implementation contract

Keep existing API compatibility and migrate existing SQLite data without loss.

## Categories and archive (core backend)

- GET /api/categories -> array {id,name,sort_order,active,item_count}.
- POST /api/categories {name,sort_order?}; PATCH /api/categories/{id} same plus active.
- DELETE /api/categories/{id} rejects any linked item. Seed existing default categories
  and migrate existing items to stable category_id. Rename preserves identity/history.
- Existing item APIs accept category string for compatibility and category_id for new UI;
  returned item includes both. /api/meta.categories stays active names.
- GET /api/items adds category_id and archived=false(default)|true|all.
- POST /api/items/{id}/archive {confirmed:true,note?}; /restore same body. Keep quantity,
  status, location/photos/logs; archived_at nullable timestamp and audit archive/restore.
  No stock actions on archived items. Dashboard/expiry/location counts exclude archived.
- Export includes categories and all items, including archived.

## AI router (separate app/ai.py)

Expose build_ai_router(services) mounted by main. Services dictionary:
  connect(): request-independent SQLite connection; today(): date;
  list_categories(db), list_locations(db), find_items(db,query,include_archived=False),
  read_item(db,id), create_item(db,payload), item_action(db,id,payload),
  archive_item(db,id,note): all normal validation/audit, mutate atomically themselves.
  actor(request): stable hash of authenticated cookie; passwordless development gets an
  HttpOnly ai_client cookie in AI router; never expose/store actual auth cookies.
  timezone: string. AI router wraps callbacks' HTTP errors without leaking credentials.

GET /api/ai/status -> {enabled,voice_enabled,model,transcription_model,base_url,timezone}.
POST /api/ai/chat {message,history?:[{role:'user'|'assistant',content}]} ->
  {reply,matches:[],drafts:[{id,action,summary,items:[],payload?,expires_at}]}.
Only named tools: search_items, list_locations, list_categories, propose_create,
propose_restock, propose_move, propose_archive. Tools have strict schemas. No arbitrary
SQL/URLs/shell/files/code/HTTP tools. Cap model turns, input size, result counts, timeouts.
Reads return real item cards; writes only produce server-owned SQLite drafts with random
IDs, TTL15min and actor binding. Drafts include target updated_at snapshots and resolved
locations/categories; confirmation rejects changed/stale targets. No invented location.
POST /api/ai/drafts/{id}/confirm {confirmed:true} -> {items:[],message}; one use only,
atomically mutation+mark-used; retries/conflicts cannot duplicate writes; schema validated
again. POST /api/ai/drafts/{id}/cancel -> {ok:true}; binding enforced.
POST /api/ai/transcribe multipart file -> {text}; supported recorded audio <=10MB,
provider transcription API. No audio stored persistently. User reviews transcript.
Env AI_API_BASE_URL (default https://api.openai.com/v1), AI_API_KEY, AI_MODEL;
AI_TRANSCRIPTION_MODEL optional: voice enabled only when set; optional AI_TRANSCRIPTION_
BASE_URL and AI_TRANSCRIPTION_API_KEY fall back chat settings. HTTPS verified,
credentials stay backend. Provider HTTP errors return safe Chinese messages, no raw bodies.

## Frontend

Category management nav/modal with add/rename/order/disable/delete (linked rejection).
Home AI chat panel with status, message list, text entry, record/stop microphone MediaRecorder,
editable transcription. HTTPS required microphone helper and keyboard voice fallback.
Recording upload never auto-sends text; user sends reviewed transcript. Pending writes get
clear per-draft confirmation/cancel; archive cards show exact items and cannot be auto-confirmed.
Render model/user strings as plain text, no raw HTML. Query results open actual item details.
Archive filter and manual archive/restore with explicit confirmation dialogs. Preserve oldUI.

# HomeStorage API

Authenticated settings and controlled container updates are documented in
[UPDATE_CONTRACT.md](UPDATE_CONTRACT.md). Automatic installation is off by default;
checking and installing updates are separate actions, and manual installation requires
the exact checked version and explicit confirmation.

Category identities, archives and restricted AI endpoints are documented in
[FEATURES_V2.md](FEATURES_V2.md). The existing inventory endpoints below remain compatible with v1; v3 household
authorization now applies to all reads and writes.
Live AI connection settings are documented in [AI_SETTINGS_CONTRACT.md](AI_SETTINGS_CONTRACT.md).
Items additionally return `category_id`, `barcode` and nullable `archived_at`; default inventory
queries, location counts and expiry reminders omit archives. JSON export version 2
includes all items and categories.

Same-origin JSON API. Errors use HTTP status and a `detail` message; conflict
responses may use a structured `detail` with conflict information.
Dates are YYYY-MM-DD; timestamps ISO 8601 UTC. Expiry is calculated using
APP_TIMEZONE (default Asia/Shanghai), expiry on today = soon, before today = expired.
No inventory demo data is inserted. Initial location: 家 (type home).

- GET /api/auth → `{authenticated, auth_required, user?}`. `user` contains
  `{id,username,display_name,role,active}` when authenticated. `HOME_STORAGE_PASSWORD`
  bootstraps `admin` only if the household has no account. Existing accounts keep
  authentication enabled even if the environment variable is later removed.
  Passwordless mode applies only to a new local development database without a password.
- POST /api/login `{username?,password}` (default username `admin`); POST /api/logout.
  The HttpOnly cookie carries a random server-side session credential; only its HMAC
  digest is persisted. Legacy 2.x cookies require re-login. Password, role or account
  state changes revoke the account sessions. POST requests require same-origin.
- GET /api/meta → `{categories:[<active category names>], timezone}`.
  Names reflect user-defined categories; use /api/categories for stable IDs.
- GET /api/locations → flat array of `{id,name,code,type,parent_id,path,item_count}`.
  Types: home, room, shelf, drawer, box, other. `path` is names joined with ` / `;
  item_count counts active items in this location and descendants.
- POST /api/locations `{name,code?,type,parent_id?}` → location.
- PATCH /api/locations/{id} same fields optional; reject hierarchy cycles.
- DELETE /api/locations/{id}: reject if it has children or ANY linked item.
- GET /api/locations/{id}/qr?base_url=<browser-origin> → SVG encoding
  `<base_url>/?location=<id>`; validate http(s) origin, no path. Printable by browser.
- GET /api/items?q=&location_id=&category=&category_id=&status=&expiry=&archived=false → array of items.
  location_id includes descendants; expiry values soon,expired. Search logs record
  explicit queries via POST /api/search `{query}` (returns `{count}`), not GETs.
- POST /api/items → item. Fields: name(required), category(default 其他),
  location_id(required), quantity(default 1, nonnegative integer), unit(default 件),
  image_url(nullable), barcode(default empty, at most 128 characters, no control characters),
  brand, model, tags(comma-delimited string), notes,
  stored_date(default today), purchase_date(nullable), purchase_price(nullable),
  expires_on(nullable), warning_days(default 7, 0..3650).
- GET /api/items/{id} → item; PATCH /api/items/{id} → item, supports metadata fields
  above EXCEPT location_id and quantity (use actions for audited stock/location changes).
- POST /api/items/{id}/actions `{action,location_id?,quantity?,note?}` → item.
  Actions: move (in_stock only, destination required), take_out (in_stock only),
  return (in_use or lent only, optional new location), lend (in_stock or in_use),
  consume (in_stock only, positive quantity no larger than current),
  restock (positive quantity, in_stock or depleted), discard (active states only),
  lost (active states only).
  Statuses: in_stock, in_use, lent, depleted, discarded, lost.
  consume to zero sets depleted; discard sets quantity zero and discarded.
  No permanent item delete: keep audit records.
- Returned items contain all metadata, id, status, location_path, created_at,
  updated_at, expiry_status(none|safe|soon|expired), remaining_days(null|integer).
- GET /api/dashboard → `{total_items,total_quantity,location_count,in_use_count,
  soon_count,expired_count,recent_logs:[],expiring_items:[]}`; stock quantities and
  expiry alerts include active states (in_stock,in_use,lent), excluding depleted/lost/discarded.
- GET /api/logs?item_id=&limit= → array of `{id,action,item_id,item_name,from_location,
  to_location,quantity_delta,note,created_at}`; also location_create/location_update/
  location_delete and search events. Default 100, max 500. Logs are append-only,
  record location path snapshots and item name snapshots; mutation and log in one transaction.
- POST /api/uploads multipart `file` → `{image_url:"/uploads/<random-name>.jpg"}`.
  Validate JPEG/PNG/WebP actual content, 10 MB max. Authenticated file serving.
- GET /api/export → downloadable JSON `{version,exported_at,categories,locations,items,logs}`;
  images and secrets excluded. Use the v3 full ZIP backup for online database/photo
  snapshots, or stop the project before a whole data-volume copy.
- GET /health → `{status:"ok"}` checks SQLite, auth exempt.

DATA_DIR defaults to ./data. It holds SQLite, uploads, stable session key.
Use SQLite foreign keys, busy timeout and DELETE journal mode for local NAS disks.
NAS deployment bind mounts a local filesystem directory, not an SMB/NFS mount.


## v3 household, transfer, backup and integration endpoints

User-facing flows and limits are documented in [FEATURES_V3.md](FEATURES_V3.md).
All ordinary endpoints use household cookie authentication. `admin` manages settings,
users, backup, organization and tokens; `member` can write inventory; `viewer` can
read/search/export, but cannot confirm inventory writes. Bearer tokens never replace
cookie authentication on these endpoints.

| Method and path | Contract |
| --- | --- |
| GET `/api/account` | Current public user. |
| GET/POST `/api/users` | Admin list/create; create accepts `username`, `display_name?`, `password`, `role?`. |
| PATCH `/api/users/{id}` | Admin updates `display_name`, `password`, `role`, `active`; retain at least one active admin. |
| POST `/api/account/password` | `{current_password,password}`; successful change requires re-login. |
| GET `/api/scan/lookup?code=...` | Read-only match for barcode, item QR or location QR; returns `kind: items` with `items`, or `kind: location` with `location`. |
| POST `/api/scan/commit` | `{code,item_id,operation,quantity,request_id,confirmed:true}`. Operations `inbound`, `outbound`, `take_out`, `return`; actor + request ID gives idempotency, conflicting reuse returns 409. Take out/return require quantity 1. |
| GET `/api/items/{id}/qr?base_url=...` | SVG for `origin/?item=id&scan=1`; validated HTTP(S) origin only. |
| GET `/api/transfer/template.csv` | CSV import template, member/admin only. |
| GET `/api/transfer/export.csv?include_archived=true` | Read-only CSV export, UTF-8 BOM and formula escaping. |
| POST `/api/transfer/import/preview` | Multipart `file`, up to 10 MB / 5000 rows; actor-bound 15-minute preview, errors/warnings and at most 100 displayed rows. |
| POST `/api/transfer/import/{id}/confirm` | `{confirmed:true}`; atomic create-only import; existing category/location snapshot must still match. Repeated confirmation cannot reimport. |
| GET `/api/analytics/activity?days=90` | 7–366 days; `daily`, `totals`, `top_items`, `top_queries`, `search_sources`, timezone and definitions. |
| GET/PATCH `/api/backups/settings` | Admin. Settings: `auto_backup`, `auto_upload`, `webdav_url`, `username`, `password`, `time`, `keep_count`; password is not returned, only `password_set`. Empty password preserves, null clears; changing URL/user clears old credentials unless replaced. |
| GET `/api/backups` | Admin. `{backups,active_job,last_job}`. |
| POST `/api/backups` | Admin, `{confirmed:true,upload:false}`; 202 job. |
| GET `/api/backups/jobs/{id}` | Admin job status/progress. |
| GET `/api/backups/{id}/download` | Admin private full ZIP. |
| POST `/api/backups/{id}/upload` | Admin, `{confirmed:true}`; 202 WebDAV job. |
| DELETE `/api/backups/{id}` | Admin, JSON `{confirmed:true}`; deletes local backup only. |
| GET/POST `/api/organize/batches` | Admin list/start. Start `{confirmed:true,include_archived:false}`; 202, generates proposals only. |
| GET `/api/organize/batches/{id}` | Admin status, actual configured model and proposal details. |
| POST `/api/organize/batches/{id}/confirm` | Admin, `{confirmed:true}`; validate unchanged snapshots and atomically apply categories. |
| POST `/api/organize/batches/{id}/cancel` | Admin, `{confirmed:true}`; cancel uncommitted plan. |
| GET `/api/organize/batches/{id}/rollback-preview` | Admin conflict preview. |
| POST `/api/organize/batches/{id}/rollback` | Admin, `{confirmed:true}`; conflicts block the entire rollback with 409. |
| GET `/api/handover` | Authenticated versioned Markdown attachment, not arbitrary file access. |

Heatmap movements count committed operations, not quantity units. Search counts are
explicit nonempty queries from web, homepage AI and external read-only searches.
`top_items.search_count` counts appearances in the matching result set, not clicks;
plain listing without `q` does not count. Historical search logs cannot reconstruct
per-item results from before v3.

## External AI: strictly read-only Bearer scope

Admin cookie endpoints:

- GET `/api/integrations/connection` → API URLs, OpenAPI URL and copyable cURL example.
- GET `/api/integrations/openapi.json` → OpenAPI 3.1 description for the three GETs.
- GET `/api/integrations/tokens` → metadata, never stored plaintext credentials.
- POST `/api/integrations/tokens` `{name,expires_days?:90}` → one-time `token` plus
  metadata; expiry 1–366 days. Database persists only SHA256.
- DELETE `/api/integrations/tokens/{id}` → immediate revocation.

Only the following exact routes accept `Authorization: Bearer hs_ro_...`:

- GET `/api/external/v1/items?q=&limit=50&offset=0` → `{items,total,limit,offset}`;
  excludes archived inventory, maximum 100 items per page, strict query parameters.
- GET `/api/external/v1/locations` → whitelisted location fields.
- GET `/api/external/v1/categories` → whitelisted category fields.

They expose inventory metadata including notes and purchase price; they never expose
users, configuration secrets, SQL, raw database or photo files. Tokens are household
credentials, independent of their creator account; disabling that account does not
revoke them. Each token must be explicitly revoked when no longer needed. Clients
must already be able to reach the NAS; copying the endpoint does not expose it to
the public internet. No inventory write endpoint accepts this credential.

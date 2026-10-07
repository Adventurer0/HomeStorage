# HomeStorage v1 API

Same-origin JSON API. Errors use HTTP status and `{"detail":"中文消息"}`.
Dates are YYYY-MM-DD; timestamps ISO 8601 UTC. Expiry is calculated using
APP_TIMEZONE (default Asia/Shanghai), expiry on today = soon, before today = expired.
No inventory demo data is inserted. Initial location: 家 (type home).

- GET /api/auth → `{authenticated, auth_required}`. Cookie auth when
  HOME_STORAGE_PASSWORD is set; passwordless local development otherwise.
- POST /api/login `{password}`; POST /api/logout. POST requests require same-origin.
- GET /api/meta → `{categories:["日常用品","电子设备","摄影器材","工具耗材","骑行用品","健身用品","商品库存","食品","药品","其他"], timezone}`.
- GET /api/locations → flat array of `{id,name,code,type,parent_id,path,item_count}`.
  Types: home, room, shelf, drawer, box, other. `path` is names joined with ` / `;
  item_count counts active items in this location and descendants.
- POST /api/locations `{name,code?,type,parent_id?}` → location.
- PATCH /api/locations/{id} same fields optional; reject hierarchy cycles.
- DELETE /api/locations/{id}: reject if it has children or ANY linked item.
- GET /api/locations/{id}/qr?base_url=<browser-origin> → SVG encoding
  `<base_url>/?location=<id>`; validate http(s) origin, no path. Printable by browser.
- GET /api/items?q=&location_id=&category=&status=&expiry= → array of items.
  location_id includes descendants; expiry values soon,expired. Search logs record
  explicit queries via POST /api/search `{query}` (returns `{count}`), not GETs.
- POST /api/items → item. Fields: name(required), category(default 其他),
  location_id(required), quantity(default 1, nonnegative integer), unit(default 件),
  image_url(nullable), brand, model, tags(comma-delimited string), notes,
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
- GET /api/export → downloadable JSON `{version,exported_at,locations,items,logs}`;
  images excluded. Backup whole data folder after stopping container to include photos.
- GET /health → `{status:"ok"}` checks SQLite, auth exempt.

DATA_DIR defaults to ./data. It holds SQLite, uploads, stable session key.
Use SQLite foreign keys, busy timeout and DELETE journal mode for local NAS disks.
NAS deployment bind mounts a local filesystem directory, not an SMB/NFS mount.

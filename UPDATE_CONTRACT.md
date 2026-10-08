# HomeStorage controlled updates (protocol 1)

Application version: 2.1.0. Default automatic updates: OFF.
The web app has no Docker socket. A separate, internal updater owns the socket,
data backup/restore, and only replaces the HomeStorage app in its own Compose project.
AI has no update tools. A configured login password is required for update controls.

## Public web API (authenticated, existing same-origin middleware)

- GET /api/settings/updates
- POST /api/settings/updates/check {}
- PATCH /api/settings/updates {auto_update: boolean}
- POST /api/settings/updates/install {confirmed: true, version: "2.1.1"}

Return safe status, flat fields:
available, current_version, current_revision, auto_update, update_available,
latest_version (nullable), release_notes, release_url (nullable), last_checked_at,
last_backup_at, next_check_at, phase, message, backup_count,
progress: {downloaded_bytes, total_bytes}.
Phases: idle, checking, queued, downloading, loading, stopping, backing_up,
applying, verifying, completed, failed, rolled_back.
Install requires strict true and the currently checked stable version; no downgrade.
The app reads /run/homestorage-update/token, proxies ONLY fixed updater endpoints
at UPDATE_SERVICE_URL (default empty, configured by Compose), never user URLs/commands.
If updater is missing, GET shows unavailable with useful deployment instructions.
During /run/homestorage-update/maintenance.flag, app mutations (except update
controls and login/logout) return 503; reads and /health remain available.

## Internal updater API

GET /v1/status; POST /v1/check {}; POST /v1/settings {auto_update:boolean};
POST /v1/install {confirmed:true,version:string}. All require Bearer token from
/state/token, created once with random 32 bytes and owner APP_UID/APP_GID mode 640.
No host port exposed. State /state/state.json (atomic replace, mode 600), backups
/state/backups (mode 700), maintenance.flag shared readonly with the app.
Data is mounted read/write at /app-data for backup and failed-update restoration.

Engine interface (updater/engine.py, synchronous, called in a worker thread):
UpdateEngine(docker, store, releases, *, self_id, data_dir, state_dir,
target_service="homestorage"); status(); check(); install(version, automatic=False);
recover(). store supplies persisted get()/update(**fields), with private journal and
release cache keys never projected in status. releases.latest(arch) returns validated
release {version,revision,image,asset_url,sha256,size,notes,url}; releases.download
(release,destination,progress_callback) streams and verifies size/SHA256.
DockerClient (updater/docker.py) uses httpx Unix socket: inspect, list containers,
info, load_image, inspect_image, create, start, stop, rename, remove.
Engine discover target by own Compose project label, service homestorage, and
io.homestorage.role=app; require exactly one intended active target; never other apps.
Verify target /data mount is exactly the updater's /app-data mount.
Journal every destructive phase for restart recovery. Serialize jobs. Download and
load before downtime, verify architecture + image version/revision labels. Stop app,
backup whole data, rename old container, create replacement using inspected ports,
environment (except HOMESTORAGE_VERSION/REVISION), volumes, security/network config.
Health check new container before success. On failure stop/remove new, restore backup,
rename/start old; keep maintenance until recovery succeeds. Retain 3 successful backups.
Never report successful rollback if recovery failed. Never expose inspect/env/token.

Updater service (updater/main.py) serializes asynchronous check/install jobs and
persisted settings. Daily check/update at 03:00 APP_TIMEZONE, only automatically
installs when auto_update=true. Turning off prevents subsequent automatic installs;
an update already applying must safely complete/recover. Start with recover().

## Releases (GitHub releases, no registry login)

GitHub repo Adventurer0/HomeStorage, GET releases/latest via verified HTTPS.
Public repositories need no credentials. For a private repository, optional
UPDATE_GITHUB_TOKEN is configured only in the updater, with Contents: read permission
on this exact repository. Asset API requests receive the token; CDN redirects do not.
Ignore draft/prerelease; validate tag vMAJOR.MINOR.PATCH. Stable manifest asset
homestorage-update.json schema:
{schema_version:1,version:"2.1.0",revision:"<40 hex git SHA>",images:{
  amd64:{image:"homestorage:2.1.0-amd64",asset:"homestorage-2.1.0-linux-amd64.tar.gz",
         sha256:"<64 hex>",size:123}, arm64:{...}}}
Release assets must belong to this exact repo/version; strict fields/limits. Stream
archives <=1GiB. API metadata <=1MiB, manifest <=256KiB. No redirected credentials.
GitHub release archives are docker save tar.gz, loaded locally; no GHCR public-package
setup required. CI publishes on v* tags after tests, builds amd64 and arm64, creates
checksummed manifest and release inheriting repository visibility. Reject
version/tag/source mismatch.
Docker image labels org.opencontainers.image.version/revision match build args.

## Deployment

Dockerfile includes app + updater, HOMESTORAGE_VERSION/REVISION build metadata.
Existing Compose files keep local build for the ONE initial upgrade. Add updater
using same local image, command uvicorn updater.main:app :9080, user 0:0, readonly
root, tmpfs, Docker socket, same data at /app-data and update state at /state.
App keeps existing data/password/AI/ports, gains readonly update state at
/run/homestorage-update, UPDATE_SERVICE_URL=http://updater:9080 and app-role label.
Use named homestorage_update_state in fnos; do not change homestorage_data.
Source-built containers are subsequently replaced from release archives; ordinary
restart preserves installed version. Do not run build/up on old source after an
automatic update, since that rebuilds the old version. Future deployment config changes
must use current source or currently installed release image.

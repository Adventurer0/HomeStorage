"""Bounded public GitHub release downloads; no registry or shell execution."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urljoin, urlsplit

import httpx


REPOSITORY = "Adventurer0/HomeStorage"
API_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
RELEASE_PREFIX = "https://github.com/" + REPOSITORY + "/releases/"
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_API = 1024 * 1024
MAX_MANIFEST = 256 * 1024
MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_ATTEMPTS = 3
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "releases.githubusercontent.com"}
ASSET_API_RE = re.compile(r"https://api\.github\.com/repos/Adventurer0/HomeStorage/releases/assets/[1-9][0-9]{0,19}\Z")


class ReleaseError(ValueError):
    """Safe, user-readable failure, without URLs, credentials, or remote bodies."""


class _NetworkError(ReleaseError):
    """A classified transport failure; only the fixed reason and host are public."""

    def __init__(self, host, reason, *, retryable):
        self.host = host
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"{host} {reason}")


def _network_error(error, url):
    # Never expose exception strings: proxy URLs, signed CDN URLs and credentials
    # can appear in them. The URL has already passed our source validation.
    host = urlsplit(url).hostname
    if host not in ASSET_HOSTS | {"api.github.com"}:
        host = "GitHub"
    pending, visited = [error], set()
    tls_failure = False
    while pending and len(visited) < 24:
        cause = pending.pop()
        if not isinstance(cause, BaseException) or id(cause) in visited:
            continue
        visited.add(id(cause))
        if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cause).upper():
            tls_failure = True
            break
        pending.extend((cause.__cause__, cause.__context__))
        pending.extend(argument for argument in cause.args if isinstance(argument, BaseException))
    if tls_failure:
        return _NetworkError(host, "TLS证书或握手失败（SSLError）", retryable=False)
    reasons = (
        (httpx.ConnectTimeout, "连接超时（ConnectTimeout）"),
        (httpx.ReadTimeout, "读取超时（ReadTimeout）"),
        (httpx.WriteTimeout, "发送超时（WriteTimeout）"),
        (httpx.PoolTimeout, "连接等待超时（PoolTimeout）"),
        (httpx.ConnectError, "连接中断（ConnectError）"),
        (httpx.ReadError, "读取中断（ReadError）"),
        (httpx.WriteError, "发送中断（WriteError）"),
        (httpx.RemoteProtocolError, "远端连接异常（RemoteProtocolError）"),
    )
    for exception_type, reason in reasons:
        if isinstance(error, exception_type):
            return _NetworkError(host, reason, retryable=True)
    reason = "代理连接失败（ProxyError）" if isinstance(error, httpx.ProxyError) else "网络请求失败（HTTPError）"
    return _NetworkError(host, reason, retryable=False)


def _retry_or_raise(error, stage, attempt):
    if error.retryable and attempt < MAX_ATTEMPTS:
        time.sleep(attempt)
        return
    raise ReleaseError(f"{stage}失败：{error.host} {error.reason}（已尝试 {attempt} 次）") from error


def valid_version(value):
    return isinstance(value, str) and len(value) <= 48 and VERSION_RE.fullmatch(value) is not None


def _exact_url(value, expected):
    if not isinstance(value, str) or value != expected:
        raise ReleaseError("发布文件来源或版本不匹配")
    return value


def _safe_asset_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ReleaseError("发布文件下载地址无效")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in ASSET_HOSTS or parsed.port not in (None, 443):
            raise ValueError
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
        if parsed.hostname == "github.com" and not re.fullmatch(r"/Adventurer0/HomeStorage/releases/download/v[0-9]+\.[0-9]+\.[0-9]+/homestorage(?:-update\.json|-[0-9]+\.[0-9]+\.[0-9]+-linux-(?:amd64|arm64)\.tar\.gz)", parsed.path):
            raise ValueError
    except ValueError as exc:
        raise ReleaseError("发布文件下载地址无效") from exc
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReleaseError("发布文件大小无效")
    return value


def _chunks(response, chunk_size):
    # MockTransport may supply an already buffered response. Real network
    # responses stay streamed and never allocate the whole archive in memory.
    if response.is_stream_consumed:
        content = response.content
        for offset in range(0, len(content), chunk_size):
            yield content[offset:offset + chunk_size]
    else:
        yield from response.iter_raw(chunk_size=chunk_size)


class ReleaseClient:
    def __init__(self, *, client=None, github_token=None, proxy_configuration=None):
        self.github_token = os.environ.get("UPDATE_GITHUB_TOKEN", "") if github_token is None else github_token
        if not isinstance(self.github_token, str) or len(self.github_token) > 2048 or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in self.github_token):
            raise ReleaseError("GitHub 只读令牌格式无效")
        self._owns_client = client is None
        options = {}
        if proxy_configuration is not None:
            from .proxy import proxy_configuration as validate_proxy_configuration
            configuration = validate_proxy_configuration({"proxy_configuration": proxy_configuration})
            # A saved empty URL deliberately disables ambient HTTP(S)_PROXY.
            options["trust_env"] = False
            if configuration["url"]:
                credentials = (configuration["username"], configuration["password"]) if configuration["username"] or configuration["password"] else None
                options["proxy"] = httpx.Proxy(configuration["url"], auth=credentials)
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(90.0, connect=30.0),
            # NAS routers and intermediaries may discard an idle connection.
            # A check/download gets fresh TLS connections instead of inheriting
            # the long-running updater's pooled sockets from an earlier check.
            limits=httpx.Limits(max_keepalive_connections=0),
            follow_redirects=False,
            headers={"User-Agent": "HomeStorage-Updater/1", "Accept-Encoding": "identity"},
            **options,
        )

    def close(self):
        if self._owns_client:
            self.client.close()

    @contextmanager
    def _response(self, url, *, assets=False, limit):
        current = url
        for redirect_count in range(5):
            asset_api = isinstance(current, str) and ASSET_API_RE.fullmatch(current) is not None
            if assets:
                if not asset_api:
                    _safe_asset_url(current)
            elif current != API_URL:
                raise ReleaseError("发布信息来源无效")
            try:
                request = self.client.build_request("GET", current, headers={"Accept": "application/vnd.github+json" if not assets else "application/octet-stream", "Accept-Encoding": "identity"})
                request.headers.pop("authorization", None)
                if self.github_token and (current == API_URL or asset_api):
                    request.headers["Authorization"] = "Bearer " + self.github_token
                response = self.client.send(request, stream=True, follow_redirects=False, auth=None)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if not assets or redirect_count == 4:
                            raise ReleaseError("发布文件下载重定向无效")
                        location = response.headers.get("location")
                        if not location:
                            raise ReleaseError("发布文件下载重定向无效")
                        current = _safe_asset_url(urljoin(current, location))
                        continue
                    if response.status_code in (502, 503, 504):
                        raise _NetworkError(urlsplit(current).hostname, f"服务暂时不可用（HTTP {response.status_code}）", retryable=True)
                    if response.status_code == 404 and not assets:
                        raise ReleaseError("无法访问稳定版本；可能尚未发布，或私有仓库需要配置 GitHub 只读令牌")
                    if response.status_code == 404:
                        raise ReleaseError("无法访问发布文件；私有仓库请检查 GitHub 只读令牌及其权限")
                    if response.status_code == 401:
                        raise ReleaseError("GitHub 认证失败，请检查更新服务中的只读令牌")
                    if response.status_code == 403:
                        raise ReleaseError("GitHub 拒绝请求，可能只读令牌权限不足或请求额度已用尽")
                    if response.status_code == 429:
                        raise ReleaseError("GitHub 暂时限制请求，请稍后重试")
                    if response.status_code != 200:
                        raise ReleaseError("无法下载公开发布文件，请稍后重试")
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in ("", "identity"):
                        raise ReleaseError("发布文件传输编码不受支持")
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            if not 0 <= int(length) <= limit:
                                raise ValueError
                        except ValueError as exc:
                            raise ReleaseError("发布文件超出大小限制") from exc
                    yield response
                    return
                finally:
                    response.close()
            except httpx.HTTPError as exc:
                raise _network_error(exc, current) from exc
        raise ReleaseError("发布文件下载重定向过多")

    def _json(self, url, maximum, *, assets=False):
        # Retry the whole read from the original trusted URL, not a possibly
        # expired signed redirect. A partial JSON body must never survive a retry.
        for attempt in range(1, MAX_ATTEMPTS + 1):
            chunks = bytearray()
            try:
                with self._response(url, assets=assets, limit=maximum) as response:
                    for chunk in _chunks(response, 64 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > maximum:
                            raise ReleaseError("发布信息超出大小限制")
                break
            except _NetworkError as exc:
                _retry_or_raise(exc, "更新清单" if assets else "版本信息", attempt)
        try:
            payload = json.loads(chunks)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ReleaseError("发布信息格式无效") from exc
        if not isinstance(payload, dict):
            raise ReleaseError("发布信息格式无效")
        return payload

    def latest(self, arch):
        if arch not in ("amd64", "arm64"):
            raise ReleaseError("当前 NAS 架构暂不支持自动更新")
        metadata = self._json(API_URL, MAX_API)
        tag = metadata.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v") or not valid_version(tag[1:]):
            raise ReleaseError("最新发布版本编号无效")
        version = tag[1:]
        if metadata.get("draft") is not False or metadata.get("prerelease") is not False:
            raise ReleaseError("自动更新仅支持公开稳定版本")
        release_url = _exact_url(metadata.get("html_url"), RELEASE_PREFIX + "tag/" + tag)
        assets = metadata.get("assets")
        if not isinstance(assets, list) or len(assets) > 100 or any(not isinstance(asset, dict) for asset in assets):
            raise ReleaseError("发布文件列表无效")
        manifests = [asset for asset in assets if asset.get("name") == "homestorage-update.json"]
        if len(manifests) != 1:
            raise ReleaseError("该版本尚未提供自动更新文件")
        manifest_asset = manifests[0]
        _integer(manifest_asset.get("size"), 1, MAX_MANIFEST)
        manifest_url = _exact_url(manifest_asset.get("browser_download_url"), RELEASE_PREFIX + "download/" + tag + "/homestorage-update.json")
        if self.github_token:
            manifest_url = self._asset_api_url(manifest_asset)
        manifest = self._json(manifest_url, MAX_MANIFEST, assets=True)
        if set(manifest) != {"schema_version", "version", "revision", "images"} or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ReleaseError("自动更新文件格式不受支持")
        if manifest["version"] != version or not isinstance(manifest["revision"], str) or not REVISION_RE.fullmatch(manifest["revision"]):
            raise ReleaseError("发布版本与更新文件不匹配")
        images = manifest.get("images")
        if not isinstance(images, dict) or set(images) != {"amd64", "arm64"}:
            raise ReleaseError("自动更新文件缺少支持的 NAS 架构")
        for image_arch, image in images.items():
            if not isinstance(image, dict) or set(image) != {"image", "asset", "sha256", "size"}:
                raise ReleaseError("镜像更新信息格式无效")
            if image["image"] != f"homestorage:{version}-{image_arch}" or image["asset"] != f"homestorage-{version}-linux-{image_arch}.tar.gz":
                raise ReleaseError("镜像文件名与发布版本不匹配")
            if not isinstance(image["sha256"], str) or not SHA_RE.fullmatch(image["sha256"]):
                raise ReleaseError("镜像校验信息无效")
            _integer(image["size"], 1, MAX_ARCHIVE)
        image = images[arch]
        archive_assets = [asset for asset in assets if asset.get("name") == image["asset"]]
        if len(archive_assets) != 1 or archive_assets[0].get("size") != image["size"] or type(archive_assets[0].get("size")) is not int:
            raise ReleaseError("镜像文件大小与发布信息不匹配")
        archive_url = _exact_url(archive_assets[0].get("browser_download_url"), RELEASE_PREFIX + "download/" + tag + "/" + image["asset"])
        notes = metadata.get("body") or ""
        if not isinstance(notes, str):
            raise ReleaseError("发布说明格式无效")
        result = {
            "version": version, "revision": manifest["revision"], "image": image["image"],
            "asset_url": archive_url, "sha256": image["sha256"], "size": image["size"],
            "notes": notes[:12000], "url": release_url,
        }
        if self.github_token:
            result["asset_api_url"] = self._asset_api_url(archive_assets[0])
        return result

    def _asset_api_url(self, asset):
        identity = _integer(asset.get("id"), 1, 10 ** 20 - 1)
        return _exact_url(asset.get("url"), "https://api.github.com/repos/" + REPOSITORY + "/releases/assets/" + str(identity))

    def download(self, release, destination, progress_callback):
        base_keys = {"version", "revision", "image", "asset_url", "sha256", "size", "notes", "url"}
        if not isinstance(release, dict) or set(release) not in (base_keys, base_keys | {"asset_api_url"}):
            raise ReleaseError("缓存的发布信息无效，请重新检查更新")
        version = release.get("version")
        if not valid_version(version) or not isinstance(release["revision"], str) or not REVISION_RE.fullmatch(release["revision"]):
            raise ReleaseError("缓存的发布版本无效，请重新检查更新")
        image = release.get("image")
        arch = next((arch for arch in ("amd64", "arm64") if image == f"homestorage:{version}-{arch}"), None)
        if arch is None or not isinstance(release["sha256"], str) or not SHA_RE.fullmatch(release["sha256"]):
            raise ReleaseError("缓存的镜像信息无效，请重新检查更新")
        size = _integer(release["size"], 1, MAX_ARCHIVE)
        url = _exact_url(release["asset_url"], RELEASE_PREFIX + f"download/v{version}/homestorage-{version}-linux-{arch}.tar.gz")
        _exact_url(release["url"], RELEASE_PREFIX + f"tag/v{version}")
        if "asset_api_url" in release:
            if not isinstance(release["asset_api_url"], str) or not ASSET_API_RE.fullmatch(release["asset_api_url"]):
                raise ReleaseError("缓存的发布文件来源无效")
            if not self.github_token:
                raise ReleaseError("私有发布文件需要配置 GitHub 只读令牌")
            url = release["asset_api_url"]
        destination = Path(destination)
        created = False
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as output:
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    # Keep the exclusively created descriptor across attempts;
                    # reopening the pathname would allow a symlink replacement.
                    # Every retry starts over, including the digest and progress.
                    output.seek(0)
                    output.truncate(0)
                    received = 0
                    digest = hashlib.sha256()
                    prefix = bytearray()
                    progress_callback(0, size)
                    try:
                        with self._response(url, assets=True, limit=size) as response:
                            for chunk in _chunks(response, 256 * 1024):
                                received += len(chunk)
                                if received > size:
                                    raise ReleaseError("镜像下载大小超出发布信息")
                                if len(prefix) < 3:
                                    prefix.extend(chunk[:3 - len(prefix)])
                                digest.update(chunk)
                                output.write(chunk)
                                progress_callback(received, size)
                    except _NetworkError as exc:
                        _retry_or_raise(exc, "镜像下载", attempt)
                        continue
                    if received != size or digest.hexdigest() != release["sha256"]:
                        raise ReleaseError("镜像下载不完整或校验失败，请重试")
                    if bytes(prefix) != b"\x1f\x8b\x08":
                        raise ReleaseError("发布镜像压缩格式无效")
                    output.flush()
                    os.fsync(output.fileno())
                    break
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return destination

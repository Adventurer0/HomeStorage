"""Small Docker Engine client. The web application never imports this module."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import httpx


class DockerError(RuntimeError):
    """A deliberately generic error which does not leak daemon configuration."""


class DockerClient:
    def __init__(self, socket_path="/var/run/docker.sock", *, client=None):
        self.client = client or httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket_path)),
            base_url="http://docker/v1.41/",
            timeout=httpx.Timeout(120, connect=10),
            follow_redirects=False,
            trust_env=False,
        )

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, ValueError) as exc:
            raise DockerError("无法完成 Docker 操作，请检查更新服务与 Docker 的连接。") from exc

    def _json(self, method, path, **kwargs):
        try:
            return self._request(method, path, **kwargs).json()
        except (ValueError, TypeError) as exc:
            raise DockerError("Docker 返回了无法识别的数据。") from exc

    def info(self):
        return self._json("GET", "info")

    def inspect(self, container_id):
        return self._json("GET", f"containers/{quote(str(container_id), safe='')}/json")

    def list_containers(self, *, all=True, filters=None):
        return self._json("GET", "containers/json", params={
            "all": "true" if all else "false",
            "filters": json.dumps(filters or {}, separators=(",", ":")),
        })

    def inspect_image(self, name):
        return self._json("GET", f"images/{quote(str(name), safe='')}/json")

    def load_image(self, path):
        # Docker accepts a gzipped `docker save` archive directly. Do not expand
        # arbitrary archive contents onto the updater filesystem.
        try:
            with Path(path).open("rb") as source:
                with self.client.stream("POST", "images/load", params={"quiet": "true"},
                                        content=iter(lambda: source.read(1024 * 1024), b""),
                                        headers={"Content-Type": "application/x-tar"},
                                        timeout=httpx.Timeout(900, connect=10)) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if len(line) > 1024 * 1024:
                            raise DockerError("Docker 镜像加载响应过大。")
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError as exc:
                            raise DockerError("Docker 镜像加载响应无效。") from exc
                        if "error" in event or "errorDetail" in event:
                            raise DockerError("Docker 无法加载已下载的更新镜像。")
        except (httpx.HTTPError, OSError) as exc:
            raise DockerError("Docker 无法加载已下载的更新镜像。") from exc

    def create(self, config, *, name):
        result = self._json("POST", "containers/create", params={"name": name}, json=config)
        container_id = result.get("Id") if isinstance(result, dict) else None
        if not isinstance(container_id, str) or not container_id:
            raise DockerError("Docker 未返回新容器的标识。")
        return container_id

    def start(self, container_id):
        self._request("POST", f"containers/{quote(str(container_id), safe='')}/start")

    def stop(self, container_id, timeout=30):
        self._request("POST", f"containers/{quote(str(container_id), safe='')}/stop",
                      params={"t": int(timeout)}, timeout=max(60, int(timeout) + 15))

    def rename(self, container_id, name):
        self._request("POST", f"containers/{quote(str(container_id), safe='')}/rename",
                      params={"name": name})

    def remove(self, container_id, force=False):
        # Explicitly never remove anonymous or named volumes.
        self._request("DELETE", f"containers/{quote(str(container_id), safe='')}",
                      params={"force": str(bool(force)).lower(), "v": "false"})

    def close(self):
        self.client.close()

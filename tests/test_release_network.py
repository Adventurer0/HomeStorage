"""Release downloads recover from transient failures without weakening validation."""

import gzip
import hashlib
import json
from pathlib import Path
import random
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from updater.releases import API_URL, ReleaseClient, ReleaseError


VERSION = "2.4.1"
BASE_URL = "https://github.com/Adventurer0/HomeStorage/releases/"
ASSET_API = "https://api.github.com/repos/Adventurer0/HomeStorage/releases/assets/"
MANIFEST_URL = BASE_URL + f"download/v{VERSION}/homestorage-update.json"
ARCHIVE_URL = BASE_URL + f"download/v{VERSION}/homestorage-{VERSION}-linux-amd64.tar.gz"
CDN = "https://release-assets.githubusercontent.com/"
SECRET = "fixture-private-read-token"


class InterruptedStream(httpx.SyncByteStream):
    """Deliver enough bytes to reach the consumer, then fail the transfer."""

    def __init__(self, prefix, failure):
        self.prefix = prefix
        self.failure = failure
        self.closed = False

    def __iter__(self):
        yield self.prefix
        raise self.failure

    def close(self):
        self.closed = True


class ReleaseNetworkTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix="homestorage-release-network-"))
        self.root = Path(directory)
        self.archive = gzip.compress(random.Random(42).randbytes(600_000), mtime=0)
        self.manifest = {
            "schema_version": 1,
            "version": VERSION,
            "revision": "a" * 40,
            "images": {
                arch: {
                    "image": f"homestorage:{VERSION}-{arch}",
                    "asset": f"homestorage-{VERSION}-linux-{arch}.tar.gz",
                    "sha256": hashlib.sha256(self.archive).hexdigest(),
                    "size": len(self.archive),
                }
                for arch in ("amd64", "arm64")
            },
        }
        self.metadata = {
            "tag_name": "v" + VERSION,
            "draft": False,
            "prerelease": False,
            "html_url": BASE_URL + f"tag/v{VERSION}",
            "body": "网络恢复测试版本",
            "assets": [
                {
                    "id": identity,
                    "url": ASSET_API + str(identity),
                    "name": name,
                    "size": size,
                    "browser_download_url": BASE_URL + f"download/v{VERSION}/" + name,
                }
                for identity, name, size in (
                    (101, "homestorage-update.json", len(json.dumps(self.manifest).encode())),
                    (102, self.manifest["images"]["amd64"]["asset"], len(self.archive)),
                    (103, self.manifest["images"]["arm64"]["asset"], len(self.archive)),
                )
            ],
        }
        self.requests = []
        self.sleep = self.enterContext(patch("updater.releases.time.sleep"))

    def provider(self, request):
        url = str(request.url)
        if url == API_URL:
            body = json.dumps(self.metadata).encode()
        elif url in (MANIFEST_URL, ASSET_API + "101"):
            body = json.dumps(self.manifest).encode()
        elif url in (ARCHIVE_URL, ASSET_API + "102"):
            body = self.archive
        else:
            self.fail("Unexpected outbound request: " + request.url.host)
        return httpx.Response(200, stream=httpx.ByteStream(body))

    def releases(self, provider=None, *, token="", **options):
        def record(request):
            self.requests.append(request)
            return (provider or self.provider)(request)

        client = self.enterContext(httpx.Client(transport=httpx.MockTransport(record), **options))
        return ReleaseClient(client=client, github_token=token)

    def urls(self, url):
        return [request for request in self.requests if str(request.url) == url]

    def assert_safe_error(self, error):
        message = str(error)
        for private_value in (SECRET, "signed-private-value", "private-exception-body", "https://", "?signature="):
            self.assertNotIn(private_value, message)

    def test_metadata_connect_timeout_recovers_without_restarting_successful_manifest(self):
        attempts = 0

        def provider(request):
            nonlocal attempts
            if str(request.url) == API_URL:
                attempts += 1
                if attempts == 1:
                    raise httpx.ConnectTimeout("private-exception-body", request=request)
            return self.provider(request)

        release = self.releases(provider).latest("amd64")
        self.assertEqual(release["version"], VERSION)
        self.assertEqual(len(self.urls(API_URL)), 2)
        self.assertEqual(len(self.urls(MANIFEST_URL)), 1)
        self.assertEqual(self.sleep.call_count, 1)

    def test_interrupted_metadata_and_manifest_restart_with_empty_json_buffers(self):
        for target in (API_URL, MANIFEST_URL):
            with self.subTest(target=target):
                self.requests.clear()
                self.sleep.reset_mock()
                attempts = 0
                interrupted = []

                def provider(request):
                    nonlocal attempts
                    if str(request.url) == target:
                        attempts += 1
                        if attempts == 1:
                            data = self.metadata if target == API_URL else self.manifest
                            # If retries append to the old buffer, the second JSON
                            # object becomes trailing garbage and decoding fails.
                            prefix = json.dumps(data).encode() + b" " * (70 * 1024)
                            stream = InterruptedStream(prefix, httpx.ReadError("private-exception-body", request=request))
                            interrupted.append(stream)
                            return httpx.Response(200, stream=stream)
                    return self.provider(request)

                release = self.releases(provider).latest("amd64")
                self.assertEqual(release["sha256"], hashlib.sha256(self.archive).hexdigest())
                self.assertEqual(len(self.urls(target)), 2)
                self.assertEqual(self.sleep.call_count, 1)
                self.assertTrue(interrupted[0].closed)

    def test_private_manifest_cdn_failure_restarts_at_api_without_forwarding_auth(self):
        cdn_url = CDN + "manifest?signature=signed-private-value"
        attempts = 0

        def provider(request):
            nonlocal attempts
            url = str(request.url)
            if url == ASSET_API + "101":
                return httpx.Response(302, headers={"Location": cdn_url})
            if url == cdn_url:
                attempts += 1
                if attempts == 1:
                    raise httpx.ConnectTimeout("private-exception-body " + SECRET, request=request)
                return httpx.Response(200, stream=httpx.ByteStream(json.dumps(self.manifest).encode()))
            return self.provider(request)

        release = self.releases(
            provider, token=SECRET,
            headers={"Authorization": "Bearer client-default-secret"},
            auth=("private-user", "private-password"),
        ).latest("amd64")
        self.assertEqual(release["asset_api_url"], ASSET_API + "102")
        self.assertEqual(len(self.urls(ASSET_API + "101")), 2)
        self.assertEqual(len(self.urls(cdn_url)), 2)
        for request in self.requests:
            if request.url.host == "api.github.com":
                self.assertEqual(request.headers.get("Authorization"), "Bearer " + SECRET)
            else:
                self.assertNotIn("Authorization", request.headers)

    def test_interrupted_private_archive_refreshes_cdn_url_and_restarts_file_digest_and_progress(self):
        archive_attempts = 0
        interrupted = []

        def provider(request):
            nonlocal archive_attempts
            url = str(request.url)
            if url == ASSET_API + "102":
                archive_attempts += 1
                return httpx.Response(302, headers={"Location": CDN + f"archive?signature=attempt-{archive_attempts}"})
            if request.url.host == "release-assets.githubusercontent.com":
                if archive_attempts == 1:
                    stream = InterruptedStream(self.archive[:300_000], httpx.ReadError("private-exception-body", request=request))
                    interrupted.append(stream)
                    return httpx.Response(200, stream=stream)
                return httpx.Response(200, stream=httpx.ByteStream(self.archive))
            return self.provider(request)

        releases = self.releases(provider, token=SECRET)
        release = releases.latest("amd64")
        progress = []
        destination = self.root / "retry.tar.gz"
        releases.download(release, destination, lambda current, total: progress.append((current, total)))
        self.assertEqual(destination.read_bytes(), self.archive)
        self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), release["sha256"])
        self.assertEqual(archive_attempts, 2)
        self.assertEqual(progress.count((0, len(self.archive))), 2)
        self.assertIn((256 * 1024, len(self.archive)), progress)
        self.assertEqual(progress[-1], (len(self.archive), len(self.archive)))
        self.assertTrue(interrupted[0].closed)
        self.assertEqual(self.sleep.call_count, 1)
        cdn_requests = [request for request in self.requests if request.url.host == "release-assets.githubusercontent.com"]
        self.assertEqual(len({str(request.url) for request in cdn_requests}), 2)
        self.assertTrue(all("authorization" not in request.headers for request in cdn_requests))

    def test_transient_gateway_failures_have_bounded_retries_then_download_successfully(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                attempts = 0
                self.sleep.reset_mock()

                def provider(request):
                    nonlocal attempts
                    if str(request.url) == ARCHIVE_URL:
                        attempts += 1
                        if attempts < 3:
                            return httpx.Response(status, stream=httpx.ByteStream(b"private-exception-body"))
                    return self.provider(request)

                releases = self.releases(provider)
                destination = self.root / f"gateway-{status}.tar.gz"
                releases.download(releases.latest("amd64"), destination, lambda *_: None)
                self.assertEqual(destination.read_bytes(), self.archive)
                self.assertEqual(attempts, 3)
                self.assertEqual(self.sleep.call_count, 2)

    def test_exhausted_archive_stream_failures_remove_partial_file_and_redact_diagnostics(self):
        streams = []
        attempts = 0

        def provider(request):
            nonlocal attempts
            if str(request.url) == ASSET_API + "102":
                attempts += 1
                return httpx.Response(302, headers={"Location": CDN + "archive?signature=signed-private-value"})
            if request.url.host == "release-assets.githubusercontent.com":
                stream = InterruptedStream(
                    self.archive[:300_000],
                    httpx.ReadError("private-exception-body " + SECRET + " " + str(request.url), request=request),
                )
                streams.append(stream)
                return httpx.Response(200, stream=stream)
            return self.provider(request)

        releases = self.releases(provider, token=SECRET)
        release = releases.latest("amd64")
        destination = self.root / "failed.tar.gz"
        progress = []
        with self.assertRaises(ReleaseError) as caught:
            releases.download(release, destination, lambda current, total: progress.append((current, total)))
        self.assertFalse(destination.exists())
        self.assertEqual(attempts, 3)
        self.assertEqual(len(streams), 3)
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertEqual(progress.count((0, len(self.archive))), 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertIn("镜像", str(caught.exception))
        self.assertIn("release-assets.githubusercontent.com", str(caught.exception))
        self.assertIn("ReadError", str(caught.exception))
        self.assert_safe_error(caught.exception)

    def test_exhausted_metadata_connect_failures_are_bounded_and_report_safe_stage(self):
        def provider(request):
            raise httpx.ConnectTimeout("private-exception-body " + SECRET, request=request)

        with self.assertRaises(ReleaseError) as caught:
            self.releases(provider, token=SECRET).latest("amd64")
        self.assertEqual(len(self.urls(API_URL)), 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertIn("版本", str(caught.exception))
        self.assertIn("api.github.com", str(caught.exception))
        self.assertIn("ConnectTimeout", str(caught.exception))
        self.assert_safe_error(caught.exception)

    def test_authentication_permission_and_rate_limit_failures_are_not_retried(self):
        for status in (401, 403, 404, 429):
            with self.subTest(status=status):
                self.requests.clear()
                self.sleep.reset_mock()
                releases = self.releases(
                    lambda request: httpx.Response(status, stream=httpx.ByteStream(SECRET.encode())),
                    token=SECRET,
                )
                with self.assertRaises(ReleaseError) as caught:
                    releases.latest("amd64")
                self.assertEqual(len(self.requests), 1)
                self.sleep.assert_not_called()
                self.assert_safe_error(caught.exception)

    def test_untrusted_redirect_and_invalid_json_are_not_retried(self):
        for invalid_redirect in (False, True):
            with self.subTest(invalid_redirect=invalid_redirect):
                self.requests.clear()
                self.sleep.reset_mock()

                def provider(request):
                    if not invalid_redirect:
                        return httpx.Response(200, stream=httpx.ByteStream(b"private-exception-body: not JSON"))
                    if str(request.url) == MANIFEST_URL:
                        return httpx.Response(302, headers={"Location": "https://untrusted.example/" + SECRET})
                    return self.provider(request)

                with self.assertRaises(ReleaseError) as caught:
                    self.releases(provider).latest("amd64")
                self.assertEqual(len(self.requests), 2 if invalid_redirect else 1)
                self.sleep.assert_not_called()
                self.assert_safe_error(caught.exception)

    def test_checksum_mismatch_is_removed_without_retrying_the_download(self):
        releases = self.releases()
        release = releases.latest("amd64")
        release["sha256"] = "0" * 64
        destination = self.root / "wrong-sha.tar.gz"
        with self.assertRaises(ReleaseError):
            releases.download(release, destination, lambda *_: None)
        self.assertEqual(len(self.urls(ARCHIVE_URL)), 1)
        self.sleep.assert_not_called()
        self.assertFalse(destination.exists())

    def test_tls_certificate_failure_is_not_retried(self):
        def provider(request):
            try:
                raise ssl.SSLCertVerificationError("private-exception-body " + SECRET)
            except ssl.SSLCertVerificationError as certificate_error:
                raise httpx.ConnectError("private-exception-body " + SECRET, request=request) from certificate_error

        with self.assertRaises(ReleaseError) as caught:
            self.releases(provider).latest("amd64")
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_called()
        self.assert_safe_error(caught.exception)


if __name__ == "__main__":
    unittest.main()

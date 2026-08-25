from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.features import release_bundle


MANIFEST_URL = "https://github.com/manifest"
SIGNATURE_URL = "https://github.com/signature"
SIGNED_ASSETS = {
    "manifest": {"browser_download_url": MANIFEST_URL},
    "signature": {"browser_download_url": SIGNATURE_URL},
}


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return private, public


def _manifest(**overrides) -> dict:
    system, architecture = release_bundle.host_platform()
    version = "1.2.3"
    artifact_name = f"fake-{version}-{system}-{architecture}.tar.gz"
    value = {
        "schema_version": 1,
        "feature_id": "fake-addon",
        "version": version,
        "platform": system,
        "architecture": architecture,
        "artifact_name": artifact_name,
        "sha256": hashlib.sha256(b"artifact").hexdigest(),
        "size_bytes": len(b"artifact"),
    }
    value.update(overrides)
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _patch_signed_bytes(monkeypatch, message: bytes, signature: bytes) -> None:
    encoded = base64.b64encode(signature)

    def fake_get(url: str, *, allowed_hosts, limit):
        del allowed_hosts, limit
        if url == MANIFEST_URL:
            return message
        if url == SIGNATURE_URL:
            return encoded
        raise AssertionError(url)

    monkeypatch.setattr(release_bundle, "_bounded_get", fake_get)


def _verify(monkeypatch, value: dict, *, message: bytes | None = None):
    private, public = _keypair()
    signed_message = message if message is not None else _canonical(value)
    _patch_signed_bytes(monkeypatch, signed_message, private.sign(signed_message))
    return release_bundle._verify_signed_release(
        {"addon_id": "fake-addon", "publisher_keys": [public]},
        SIGNED_ASSETS,
        version="1.2.3",
        artifact_name=_manifest()["artifact_name"],
    )


def test_signed_manifest_binds_exact_identity_digest_and_size(monkeypatch):
    value = _manifest()
    result = _verify(monkeypatch, value)
    assert result.sha256 == value["sha256"]
    assert result.size_bytes == value["size_bytes"]
    # Keep the established digest-only helper compatible with existing tests.
    private, public = _keypair()
    message = _canonical(value)
    _patch_signed_bytes(monkeypatch, message, private.sign(message))
    assert release_bundle._verify_signed_manifest(
        {"addon_id": "fake-addon", "publisher_keys": [public]},
        SIGNED_ASSETS,
        version="1.2.3",
        artifact_name=value["artifact_name"],
    ) == value["sha256"]


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("schema_version", 2),
        ("feature_id", "other-addon"),
        ("version", "9.9.9"),
        ("platform", "windows"),
        ("architecture", "arm64"),
        ("artifact_name", "another.tar.gz"),
    ],
)
def test_signed_manifest_rejects_identity_mismatch(monkeypatch, field, wrong):
    with pytest.raises(release_bundle.ReleaseBundleError, match=field):
        _verify(monkeypatch, _manifest(**{field: wrong}))


def test_signed_manifest_rejects_extra_fields_even_when_signature_is_valid(monkeypatch):
    with pytest.raises(release_bundle.ReleaseBundleError, match="fields are invalid"):
        _verify(monkeypatch, _manifest(untrusted_extension="value"))


def test_signed_manifest_requires_mediaforge_canonical_json(monkeypatch):
    value = _manifest()
    noncanonical = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    with pytest.raises(release_bundle.ReleaseBundleError, match="not canonical JSON"):
        _verify(monkeypatch, value, message=noncanonical)


def test_signed_manifest_rejects_wrong_publisher_key(monkeypatch):
    value = _manifest()
    signer, _ = _keypair()
    _, wrong_public = _keypair()
    message = _canonical(value)
    _patch_signed_bytes(monkeypatch, message, signer.sign(message))
    with pytest.raises(release_bundle.ReleaseBundleError, match="trusted publisher key"):
        release_bundle._verify_signed_release(
            {"addon_id": "fake-addon", "publisher_keys": [wrong_public]},
            SIGNED_ASSETS,
            version="1.2.3",
            artifact_name=value["artifact_name"],
        )


def test_signed_manifest_rejects_malformed_signature(monkeypatch):
    value = _manifest()
    _, public = _keypair()
    message = _canonical(value)

    def fake_get(url: str, *, allowed_hosts, limit):
        del allowed_hosts, limit
        return message if url == MANIFEST_URL else b"not-base64***"

    monkeypatch.setattr(release_bundle, "_bounded_get", fake_get)
    with pytest.raises(release_bundle.ReleaseBundleError, match="not valid base64"):
        release_bundle._verify_signed_release(
            {"addon_id": "fake-addon", "publisher_keys": [public]},
            SIGNED_ASSETS,
            version="1.2.3",
            artifact_name=value["artifact_name"],
        )


def test_downloaded_artifact_checks_signed_size_before_digest(tmp_path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"artifact")
    digest = hashlib.sha256(b"artifact").hexdigest()
    release_bundle._verify_downloaded_artifact(
        artifact,
        expected_sha256=digest,
        expected_size=len(b"artifact"),
    )
    with pytest.raises(release_bundle.ReleaseBundleError, match="size"):
        release_bundle._verify_downloaded_artifact(
            artifact,
            expected_sha256=digest,
            expected_size=len(b"artifact") + 1,
        )


def test_downloaded_artifact_rejects_tamper(tmp_path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"tampered")
    with pytest.raises(release_bundle.ReleaseBundleError, match="SHA-256"):
        release_bundle._verify_downloaded_artifact(
            artifact,
            expected_sha256=hashlib.sha256(b"artifact").hexdigest(),
            expected_size=len(b"tampered"),
        )


def test_signed_metadata_is_authenticated_before_large_download(monkeypatch, tmp_path):
    root = tmp_path / "feature"
    root.mkdir()
    artifact = {"name": _manifest()["artifact_name"]}
    downloaded = False

    monkeypatch.setattr(release_bundle, "_feature_root", lambda _feature_id: root)
    monkeypatch.setattr(release_bundle, "_metadata", lambda _spec: {})
    monkeypatch.setattr(
        release_bundle,
        "_select_release",
        lambda _spec, _metadata: ("1.2.3", artifact, None),
    )
    monkeypatch.setattr(
        release_bundle,
        "_signed_assets",
        lambda _spec, _metadata, _name: SIGNED_ASSETS,
    )

    def reject_metadata(*_args, **_kwargs):
        raise release_bundle.ReleaseBundleError("signed metadata rejected")

    def download(*_args, **_kwargs):
        nonlocal downloaded
        downloaded = True

    monkeypatch.setattr(release_bundle, "_verify_signed_release", reject_metadata)
    monkeypatch.setattr(release_bundle, "_download", download)

    with pytest.raises(release_bundle.ReleaseBundleError, match="signed metadata rejected"):
        release_bundle.install(
            "fake-addon",
            {
                "addon_id": "fake-addon",
                "max_download_bytes": 1024,
                "max_expanded_bytes": 1024,
            },
        )
    assert downloaded is False


def _installed_root(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "feature"
    target = root / "versions" / version
    target.mkdir(parents=True)
    (root / "current").symlink_to(Path("versions") / version, target_is_directory=True)
    return root


def test_downgrade_uses_ordered_versions(monkeypatch, tmp_path):
    root = _installed_root(tmp_path, "1.10.0")
    monkeypatch.setattr(release_bundle, "_feature_root", lambda _feature_id: root)
    with pytest.raises(release_bundle.ReleaseBundleError, match="refusing to downgrade"):
        release_bundle._refuse_downgrade("fake-addon", "1.9.9")
    release_bundle._refuse_downgrade("fake-addon", "1.11.0")


def test_prerelease_cannot_replace_stable_release(monkeypatch, tmp_path):
    root = _installed_root(tmp_path, "1.0.0")
    monkeypatch.setattr(release_bundle, "_feature_root", lambda _feature_id: root)
    with pytest.raises(release_bundle.ReleaseBundleError, match="refusing to downgrade"):
        release_bundle._refuse_downgrade("fake-addon", "1.0.0rc1")


def test_unorderable_changed_version_fails_closed(monkeypatch, tmp_path):
    root = _installed_root(tmp_path, "release-A")
    monkeypatch.setattr(release_bundle, "_feature_root", lambda _feature_id: root)
    with pytest.raises(release_bundle.ReleaseBundleError, match="cannot be safely ordered"):
        release_bundle._refuse_downgrade("fake-addon", "release-B")

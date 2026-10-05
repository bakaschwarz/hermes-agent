"""Every ggml-org engine (llama.cpp backends, whisper.cpp) installs only reviewed PM artifacts."""

import pm
from pm import paths
from pm.lock import Lockfile
from pm.packages import _GgmlRelease
from pm.store import ALL_TARGETS


def test_llamacpp_backends_share_one_tag():
    lock = Lockfile(paths.lockfile_path())
    backends = ("cpu", "cuda", "vulkan", "metal", "hip")
    # One engine build everywhere: the catalog and presets are calibrated against a single tag.
    assert len({lock.version(f"llamacpp-{backend}") for backend in backends}) == 1


def test_ggml_releases_pin_every_supported_target():
    """llama.cpp and whisper.cpp install only reviewed PM artifacts."""
    lock = Lockfile(paths.lockfile_path())
    names = [name for name in pm.all_packages() if isinstance(pm.get_package(name), _GgmlRelease)]
    assert "whispercpp-cpu" in names and "llamacpp-cpu" in names
    for name in names:
        package = pm.get_package(name)
        version = lock.version(name)
        assert version
        for target in ALL_TARGETS:
            artifacts = lock.artifacts(name, target)
            if package.missing_reason(target):
                assert not artifacts
                continue
            assert artifacts
            assert [a["url"] for a in artifacts] == package.fetch_urls(version, target)
            assert all(len(bytes.fromhex(a["sha256"])) == 32 for a in artifacts)
            if name == "llamacpp-cuda":
                assert any("cudart-" in a["url"] for a in artifacts)
            # Upstream's Linux builds link the system OpenMP runtime, which minimal hosts lack.
            if target in ("linux-x64", "linux-arm64"):
                assert any("/libgomp1_" in a["url"] and a["url"].endswith(".deb") for a in artifacts)

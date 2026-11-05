"""Repository and Home Assistant manifest contracts."""

import json
import re
import struct
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from runtime import DEFAULTS, validate_options

ROOT = Path(__file__).parents[1]
ADDON = ROOT / "voxl_chromium"


def test_control_runtime_is_packaged_without_host_port_publication():
    config = yaml.safe_load((ADDON / "config.yaml").read_text())
    assert config["version"] == "0.3.0"
    assert config["options"]["automation_enabled"] is False
    assert config["schema"]["automation_enabled"] == "bool"
    assert config["discovery"] == ["voxl_chromium"]
    assert config["options"]["automation_token"] == ""
    assert config["schema"]["automation_token"] == "password"
    assert "ports" not in config
    dockerfile = (ADDON / "Dockerfile").read_text()
    copies = "\n".join(
        line for line in dockerfile.splitlines() if line.startswith("COPY ")
    )
    allowed = (ADDON / ".dockerignore").read_text().splitlines()
    for required in (
        "runtime.py",
        "control.py",
        "supervisor_discovery.py",
        "nginx.conf",
    ):
        assert required in copies
        assert f"!{required}" in allowed
    assert "wmctrl" in dockerfile and "xdotool" in dockerfile


def test_supported_browser_policy_only_suppresses_the_flag_banner():
    policy = json.loads((ADDON / "chromium-policy.json").read_text())
    assert policy == {"CommandLineFlagSecurityWarningsEnabled": False}
    dockerfile = (ADDON / "Dockerfile").read_text()
    assert (
        "COPY --chmod=0644 chromium-policy.json "
        "/etc/chromium/policies/managed/voxl.json" in dockerfile
    )
    assert "!chromium-policy.json" in (ADDON / ".dockerignore").read_text()
    # Presentation change only: don't add privileges or broad test-mode flags.
    from runtime import commands

    argv = commands(validate_options({}))["chromium"]
    assert "--no-sandbox" in argv
    assert "--test-type" not in argv and "--disable-web-security" not in argv


def test_manifest_matches_runtime_options():
    config = yaml.safe_load((ADDON / "config.yaml").read_text())
    assert config["options"] == DEFAULTS
    assert config["schema"].keys() == DEFAULTS.keys()
    assert validate_options(config["options"]) == DEFAULTS
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["stage"] == "experimental"
    assert config["ingress"] is True
    assert config.get("ingress_port", 8099) == 8099
    assert config["init"] is False
    assert not config.get("ports")
    for key in ("hassio_api", "homeassistant_api", "auth_api", "host_network"):
        assert config.get(key, False) is False
    for key in ("privileged", "full_access", "docker_api", "map", "devices", "image"):
        assert not config.get(key), key


def test_upstream_pin_is_immutable_and_checksum_verified_by_build():
    pin = json.loads((ADDON / "upstream.json").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin["version"])
    assert re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
    dockerfile = (ADDON / "Dockerfile").read_text()
    assert "cargo build --release --locked" in dockerfile
    assert 'p["sha256"]' in dockerfile
    assert "hashlib.sha256" in dockerfile


def test_documentation_and_license_are_included():
    for file in ("README.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert (ROOT / file).stat().st_size > 100
    for file in ("DOCS.md", "README.md", "CHANGELOG.md"):
        assert (ADDON / file).stat().st_size > 100
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text()
    assert (ADDON / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_nginx_temp_paths_are_unprivileged():
    config = (ADDON / "nginx.conf").read_text()
    assert "error_log stderr warn;" in config
    for module in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
        assert f"{module}_temp_path /tmp/" in config


def test_option_translations_cover_all_settings():
    translations = yaml.safe_load((ADDON / "translations/en.yaml").read_text())
    assert translations["configuration"].keys() == DEFAULTS.keys()
    for value in translations["configuration"].values():
        assert value["name"] and value["description"]


def test_home_assistant_docs_use_absolute_https_links():
    for name in ("README.md", "DOCS.md", "CHANGELOG.md"):
        for target in re.findall(r"\]\(([^)]+)\)", (ADDON / name).read_text()):
            url = urlsplit(target)
            assert url.scheme == "https" and url.netloc, (name, target)


def test_sidebar_uses_the_browser_icon():
    config = yaml.safe_load((ADDON / "config.yaml").read_text())
    assert config["panel_icon"] == "mdi:google-chrome"


def test_listing_images_and_their_license_are_present():
    for name, size in (("icon.png", 128), ("logo.png", 256)):
        image = (ADDON / name).read_bytes()
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        assert image[12:16] == b"IHDR"
        assert struct.unpack(">II", image[16:24]) == (size, size)
        assert b"IEND" in image[-12:]
    license_text = (ADDON / "CHROMIUM-LICENSE").read_text()
    assert "The Chromium Authors" in license_text
    assert "Redistribution and use" in license_text
    assert "icon.png" in (ROOT / "NOTICE.md").read_text()


def test_current_version_has_matching_package_metadata_and_changelog():
    config = yaml.safe_load((ADDON / "config.yaml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == config["version"]
    assert re.search(
        rf"^## {re.escape(config['version'])}(?:\s|$)",
        (ADDON / "CHANGELOG.md").read_text(),
        re.MULTILINE,
    )

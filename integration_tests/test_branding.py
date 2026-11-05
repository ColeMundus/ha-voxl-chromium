"""Exercise bundled branding through Home Assistant's real authenticated API."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.loader import async_get_custom_components, async_get_integration
from homeassistant.setup import async_setup_component
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "voxl_chromium" / "brand"
SOURCE = ROOT / "voxl_chromium"


@pytest.fixture
async def brand_client(hass, aiohttp_client, socket_enabled):
    """Discover real integration files and register HA's brands proxy views."""
    integration = await async_get_integration(hass, "voxl_chromium")
    assert (
        await hass.async_add_executor_job(integration.file_path.resolve) == BRAND.parent
    )
    assert integration.has_branding
    # The proxy uses this cached discovery, not a manually registered static route.
    assert (await async_get_custom_components(hass))["voxl_chromium"] is integration
    assert await async_setup_component(hass, "brands", {})
    return await aiohttp_client(hass.http.app)


@pytest.mark.parametrize(
    "image,size", [("icon.png", (128, 128)), ("logo.png", (256, 256))]
)
async def test_brand_images(brand_client, hass_access_token, hass, image, size):
    """Authenticated requests return original PNGs, never a CDN/fallback image."""
    source, bundled = await hass.async_add_executor_job(
        lambda: ((SOURCE / image).read_bytes(), (BRAND / image).read_bytes())
    )
    assert bundled == source
    with Image.open(BytesIO(source)) as png:
        assert png.format == "PNG"
        assert png.size == size

    # Any cache/CDN/placeholder path is a regression, even if it returns HTTP 200.
    with patch(
        "homeassistant.components.brands._BrandsBaseView._get_image_data",
        new_callable=AsyncMock,
        side_effect=AssertionError("Bundled branding must not use the CDN/fallback"),
    ) as fallback:
        response = await brand_client.get(
            f"/api/brands/integration/voxl_chromium/{image}",
            headers={"Authorization": f"Bearer {hass_access_token}"},
        )
        assert response.status == 200
        assert response.content_type == "image/png"
        body = await response.read()
        assert body == source
        assert sha256(body).digest() == sha256(source).digest()
        with Image.open(BytesIO(body)) as png:
            assert png.format == "PNG"
            assert png.size == size
        fallback.assert_not_called()


@pytest.mark.parametrize("image", ["icon.png", "logo.png"])
@pytest.mark.parametrize(
    "headers,query,status",
    [
        ({}, "", 403),
        ({}, "?token=invalid", 403),
        ({"Authorization": "Bearer invalid"}, "", 401),
    ],
)
async def test_brand_images_require_authentication(
    brand_client, image, headers, query, status
):
    """Neither an anonymous request nor an invalid credential exposes assets."""
    response = await brand_client.get(
        f"/api/brands/integration/voxl_chromium/{image}{query}", headers=headers
    )
    assert response.status == status


async def test_brand_images_query_token(hass, brand_client):
    """The frontend's brands access token also authenticates both image URLs."""
    token = hass.data["brands"][-1]
    for image in ("icon.png", "logo.png"):
        response = await brand_client.get(
            f"/api/brands/integration/voxl_chromium/{image}", params={"token": token}
        )
        assert response.status == 200
        source = await hass.async_add_executor_job((SOURCE / image).read_bytes)
        assert await response.read() == source


async def test_brand_license_is_self_contained(hass):
    """Installing only the custom component retains the upstream asset license."""
    original, bundled = await hass.async_add_executor_job(
        lambda: (
            (SOURCE / "CHROMIUM-LICENSE").read_bytes(),
            (BRAND / "CHROMIUM-LICENSE").read_bytes(),
        )
    )
    assert bundled == original
    assert b"Chromium" in bundled

"""Constrained, no-overwrite media exports using directory descriptors."""

import os
import secrets
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError


def write_media(hass, filename, content, extension):
    """Only existing explicitly allowlisted external directories; never config/www."""
    fd = None
    temp = None
    try:
        path = Path(filename)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.suffix.lower() != extension
            or not hass.config.is_allowed_path(str(path))
        ):
            raise ValueError
        config = Path(hass.config.config_dir).resolve()
        if path.is_relative_to(config):
            raise ValueError
        roots = [Path(p) for p in hass.config.allowlist_external_dirs]
        if not any(
            root.is_absolute()
            and root != Path("/")
            and path.parent.is_relative_to(root)
            for root in roots
        ):
            raise ValueError
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in path.parent.parts[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        # Descriptor walk rejects symlinks in *all* parent components. Hard-link
        # publication is atomic and refuses every preexisting destination.
        temp = ".voxl-" + secrets.token_hex(16)
        out = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        with os.fdopen(out, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    except (OSError, ValueError, TypeError):
        raise HomeAssistantError(
            "Media export refused; use a new filename in an allowed "
            "external media directory"
        ) from None
    finally:
        if fd is not None:
            if temp:
                try:
                    os.unlink(temp, dir_fd=fd)
                except OSError:
                    pass
            os.close(fd)

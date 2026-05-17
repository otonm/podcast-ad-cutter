"""Settings routes — GET /api/v1/settings and PATCH /api/v1/settings."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from pydantic import ValidationError

from config.config_loader import PROVIDER_KEY_MAP, AppConfig, Credentials

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base, returning a new dict.

    Dicts are merged recursively; all other types (including lists) are replaced.
    Neither input dict is mutated.
    """
    result = dict(base)
    for key, patch_val in patch.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(patch_val, dict):
            result[key] = _deep_merge(base_val, patch_val)
        else:
            result[key] = patch_val
    return result


def _write_config_sync(config_path: Path, cfg: AppConfig) -> None:
    """Write cfg to config_path atomically using a temp file on the same filesystem.

    Uses tempfile.NamedTemporaryFile with dir=config_path.parent so that the temp
    file and target are on the same filesystem, guaranteeing POSIX atomicity via
    os.replace.
    """
    data = cfg.model_dump(mode="json")
    tmp_name: str
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=config_path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_name = f.name
    os.replace(tmp_name, config_path)  # noqa: PTH105 — plan mandates os.replace for POSIX atomicity


def create_settings_router(config_path: Path) -> web.RouteTableDef:
    """Build and return a RouteTableDef with GET and PATCH /api/v1/settings registered.

    Args:
        config_path: Path to the config.yaml file on disk.

    Returns:
        RouteTableDef with settings handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/settings")
    async def get_settings(_request: web.Request) -> web.Response:
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        creds = Credentials()
        body = cfg.model_dump(mode="json")
        body["credentials"] = {
            field: ("set" if getattr(creds, field) else "not set")
            for field in PROVIDER_KEY_MAP.values()
        }
        return web.json_response(body)

    @routes.patch("/api/v1/settings")
    async def patch_settings(request: web.Request) -> web.Response:
        payload = await request.json()
        payload.pop("feeds", None)
        with config_path.open() as f:
            base_raw = yaml.safe_load(f)
        merged = _deep_merge(base_raw, payload)
        try:
            cfg = AppConfig.model_validate(merged)
        except ValidationError as exc:
            raise web.HTTPUnprocessableEntity(
                text=exc.json(), content_type="application/json"
            ) from exc
        await asyncio.to_thread(_write_config_sync, config_path, cfg)
        changed_keys = list(payload.keys())
        logger.info(f"Settings updated — changed top-level keys: {changed_keys}")
        return web.json_response(cfg.model_dump(mode="json"))

    return routes

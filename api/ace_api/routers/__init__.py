"""Discover public modules in this package that export an APIRouter as router."""
from importlib import import_module
import logging
from pkgutil import iter_modules

from fastapi import APIRouter

log = logging.getLogger(__name__)

def discover_routers() -> list[APIRouter]:
    routers = []
    for module_info in sorted(iter_modules(__path__), key=lambda item: item.name):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue
        module_name = f"{__name__}.{module_info.name}"
        try:
            module = import_module(module_name)
            if not hasattr(module, "router"):
                continue
            if not isinstance(module.router, APIRouter):
                raise TypeError(f"{module_name}.router must be an APIRouter")
            routers.append(module.router)
        except Exception:
            # Teammate plugins must not take down service probes or other pages.
            log.exception("Skipping broken router module %s", module_name)
    return routers

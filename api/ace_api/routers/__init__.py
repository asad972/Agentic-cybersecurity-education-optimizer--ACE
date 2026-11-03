"""Discover public modules in this package that export an APIRouter as router."""
from importlib import import_module
from pkgutil import iter_modules

from fastapi import APIRouter


def discover_routers() -> list[APIRouter]:
    routers = []
    for module_info in sorted(iter_modules(__path__), key=lambda item: item.name):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue
        module = import_module(f"{__name__}.{module_info.name}")
        if not hasattr(module, "router"):
            continue
        if not isinstance(module.router, APIRouter):
            raise TypeError(f"{module.__name__}.router must be an APIRouter")
        routers.append(module.router)
    return routers

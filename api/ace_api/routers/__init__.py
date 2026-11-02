"""Discover domain routers placed in this package."""

from importlib import import_module
from pkgutil import iter_modules

from fastapi import APIRouter


def discover_routers() -> tuple[APIRouter, ...]:
    """Import each domain module and return its declared FastAPI router."""
    routers = []
    for module_info in iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue
        module = import_module(f"{__name__}.{module_info.name}")
        candidate = getattr(module, "router", None)
        if candidate is None:
            continue
        if not isinstance(candidate, APIRouter):
            raise TypeError(
                f"{module.__name__}.router must be a FastAPI APIRouter"
            )
        routers.append(candidate)
    return tuple(routers)

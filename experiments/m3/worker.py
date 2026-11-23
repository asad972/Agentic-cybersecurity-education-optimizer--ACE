"""Internal worker entry point for isolated experiment dependencies."""

from __future__ import annotations

import argparse
from importlib.metadata import version
import platform

from .common import SEED, canonical_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("stream", "forecast"))
    parser.add_argument("--seed", type=int, default=SEED)
    arguments = parser.parse_args()
    if arguments.kind == "stream":
        from .stream_experiments import run_adwin, run_dgim

        packages = {name: version(name) for name in ("numpy", "river")}
        experiments = {
            "s5_dgim": run_dgim(arguments.seed),
            "s6_adwin": run_adwin(arguments.seed),
        }
    else:
        from .forecast_experiments import run_forecast

        packages = {
            name: version(name)
            for name in (
                "numpy",
                "pandas",
                "scipy",
                "statsmodels",
                "lightgbm",
                "scikit-learn",
            )
        }
        experiments = {"forecast": run_forecast(arguments.seed)}
    print(
        canonical_json(
            {
                "python": platform.python_version(),
                "packages": packages,
                "experiments": experiments,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

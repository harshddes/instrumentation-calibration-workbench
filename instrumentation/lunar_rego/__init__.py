"""LunarRego plasma diagnostics: LP / EP / RPA acquisition GUI and IV analysis."""

__all__ = [
    "estimate_plasma_potential",
    "load_ep_sheet",
    "load_lp",
    "load_rpa",
]


def __getattr__(name: str):
    if name in __all__:
        from . import analyze_iv_curves as _m

        return getattr(_m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

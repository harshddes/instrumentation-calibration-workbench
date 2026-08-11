"""
IV curve review for approved LunarRego LP / EP / RPA CSVs.

Pseudocode
----------
1. Load LP (Measured_V, Measured_I), RPA (Sweep_V, Picoammeter_I), EP sheet blocks.
2. Sort by voltage; optionally smooth current for noisy RPA traces.
3. Compute dI/dV with a central finite difference (numpy.gradient).
4. Estimate plasma / characteristic voltage at the extremum of |dI/dV|
   (Langmuir: peak of dI/dV near the electron-transition knee).
5. Write side-by-side I-V and dI/dV SVG figures plus a JSON summary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
DEFAULT_DATA = REPO_ROOT / "examples" / "plasma_diagnostics"
DEFAULT_OUT = REPO_ROOT / "docs" / "assets" / "readme"


def _finite_pairs(v: np.ndarray, i: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(v) & np.isfinite(i)
    v2, i2 = np.asarray(v, dtype=float)[mask], np.asarray(i, dtype=float)[mask]
    order = np.argsort(v2)
    v2, i2 = v2[order], i2[order]
    # Average duplicate voltages so numpy.gradient does not divide by zero.
    uniq_v, inv = np.unique(v2, return_inverse=True)
    uniq_i = np.zeros_like(uniq_v)
    counts = np.zeros_like(uniq_v)
    np.add.at(uniq_i, inv, i2)
    np.add.at(counts, inv, 1.0)
    return uniq_v, uniq_i / counts


def estimate_plasma_potential(
    v: np.ndarray,
    i: np.ndarray,
    *,
    smooth_points: int = 1,
    prefer_positive_peak: bool = True,
    edge_trim_frac: float = 0.05,
) -> Dict[str, float]:
    """
    Return voltage at the dominant |dI/dV| feature.

    For Langmuir probes, prefer_positive_peak=True selects the largest positive
    derivative (electron-transition peak). Edge samples are trimmed so a single
    end-point spike cannot steal the estimate. Floating potential V_f is also
    reported from the I≈0 crossing when available.
    """
    v, i = _finite_pairs(v, i)
    if len(v) < 3:
        raise ValueError("Need at least 3 finite (V, I) points")

    i_work = (
        uniform_filter1d(i.astype(float), size=max(1, int(smooth_points)))
        if smooth_points and smooth_points > 1
        else i.astype(float)
    )
    didv = np.gradient(i_work, v)

    n = len(v)
    trim = int(max(1, round(edge_trim_frac * n))) if n >= 20 else 0
    core = np.ones(n, dtype=bool)
    if trim:
        core[:trim] = False
        core[-trim:] = False

    search = didv[core]
    v_core = v[core]
    i_core = i_work[core]
    if prefer_positive_peak and np.any(search > 0):
        idx_core = int(np.nanargmax(search))
    else:
        idx_core = int(np.nanargmax(np.abs(search)))

    # Floating potential: first zero crossing of current (I from neg -> pos).
    vf = float("nan")
    sign = np.sign(i_work)
    crosses = np.where(np.diff(sign) > 0)[0]
    if len(crosses):
        k = int(crosses[0])
        if i_work[k + 1] != i_work[k]:
            vf = float(
                v[k]
                - i_work[k] * (v[k + 1] - v[k]) / (i_work[k + 1] - i_work[k])
            )
        else:
            vf = float(v[k])

    return {
        "V_p_est": float(v_core[idx_core]),
        "I_at_Vp": float(i_core[idx_core]),
        "dIdV_at_Vp": float(search[idx_core]),
        "dIdV_max": float(np.nanmax(didv)),
        "dIdV_min": float(np.nanmin(didv)),
        "V_f_est": vf,
        "n_points": float(len(v)),
    }


def load_lp(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"Measured_V", "Measured_I"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
    return df


def load_rpa(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"Sweep_V", "Picoammeter_I"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
    return df


def load_ep_sheet(path: Path) -> Dict[str, pd.DataFrame]:
    """
    Parse the hand-entered EP spreadsheet layout:
      - GROUNDED block: columns B/C after a 'V','I' header
      - FLOATING block: columns B/C (bias) and M/N (floating potential vs heater I)
    """
    raw = pd.read_csv(path, header=None)
    col_b = raw.iloc[:, 1]
    col_c = raw.iloc[:, 2]
    # Spreadsheet places the floating-potential table in columns N/O (13/14).
    col_m = raw.iloc[:, 13] if raw.shape[1] > 13 else pd.Series(dtype=object)
    col_n = raw.iloc[:, 14] if raw.shape[1] > 14 else pd.Series(dtype=object)

    def _block_after(label: str, v_series, i_series) -> pd.DataFrame:
        start = None
        for idx, val in enumerate(v_series.astype(str)):
            if str(val).strip().upper() == label:
                start = idx
                break
        if start is None:
            return pd.DataFrame(columns=["V", "I"])

        # Find the local V,I header under this label.
        header = None
        for idx in range(start, min(start + 6, len(v_series))):
            if str(v_series.iloc[idx]).strip().upper() == "V" and str(
                i_series.iloc[idx]
            ).strip().upper() == "I":
                header = idx
                break
        if header is None:
            return pd.DataFrame(columns=["V", "I"])

        rows = []
        for idx in range(header + 1, len(v_series)):
            vs = str(v_series.iloc[idx]).strip()
            if vs == "" or vs.lower() == "nan":
                # allow incomplete trailing heater currents with blank V
                is_ = pd.to_numeric(i_series.iloc[idx], errors="coerce")
                if np.isnan(is_):
                    break
                continue
            if vs.upper() in {"FLOATING", "GROUNDED", "EP", "V"}:
                break
            v = pd.to_numeric(v_series.iloc[idx], errors="coerce")
            i = pd.to_numeric(i_series.iloc[idx], errors="coerce")
            if np.isfinite(v) and np.isfinite(i):
                rows.append({"V": float(v), "I": float(i)})
        return pd.DataFrame(rows)

    grounded = _block_after("GROUNDED", col_b, col_c)
    floating_bias = _block_after("FLOATING", col_b, col_c)

    # Right-side "Floating" V,I pair (columns M/N in the sheet).
    floating_right = []
    header = None
    for idx in range(len(col_m)):
        if str(col_m.iloc[idx]).strip().upper() == "V" and str(
            col_n.iloc[idx]
        ).strip().upper() == "I":
            header = idx
            break
    if header is not None:
        for idx in range(header + 1, len(col_m)):
            v = pd.to_numeric(col_m.iloc[idx], errors="coerce")
            i = pd.to_numeric(col_n.iloc[idx], errors="coerce")
            if np.isfinite(v) and np.isfinite(i):
                floating_right.append({"V": float(v), "I": float(i)})
            elif len(floating_right) and (np.isnan(v) and np.isnan(i)):
                break

    return {
        "grounded": grounded,
        "floating_bias": floating_bias,
        "floating_potential": pd.DataFrame(floating_right),
    }


def _plot_iv_didv(
    v: np.ndarray,
    i: np.ndarray,
    *,
    title: str,
    out_path: Path,
    ylabel_i: str,
    smooth_points: int = 1,
    prefer_positive_peak: bool = True,
    vp_override: Optional[float] = None,
) -> Dict[str, float]:
    v, i = _finite_pairs(v, i)
    i_plot = (
        uniform_filter1d(i.astype(float), size=max(1, int(smooth_points)))
        if smooth_points and smooth_points > 1
        else i.astype(float)
    )
    didv = np.gradient(i_plot, v)
    stats = estimate_plasma_potential(
        v,
        i,
        smooth_points=smooth_points,
        prefer_positive_peak=prefer_positive_peak,
    )
    vp = float(vp_override) if vp_override is not None else stats["V_p_est"]

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), constrained_layout=True)
    axes[0].plot(v, i, "o-", ms=3, lw=1.2, color="#1f4e79", label="I(V)")
    if smooth_points and smooth_points > 1:
        axes[0].plot(
            v,
            i_plot,
            "-",
            lw=1.6,
            color="#c45c26",
            label=f"smoothed (n={smooth_points})",
        )
    axes[0].axvline(vp, color="#b91c1c", ls="--", lw=1.2, label=f"V* (dI/dV) = {vp:.3g} V")
    vf = stats.get("V_f_est")
    if vf is not None and np.isfinite(vf):
        axes[0].axvline(vf, color="#7c3aed", ls=":", lw=1.2, label=f"V_f (I=0) = {vf:.3g} V")
    axes[0].set_xlabel("Voltage (V)")
    axes[0].set_ylabel(ylabel_i)
    axes[0].set_title(f"{title} — I–V")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=8)

    axes[1].plot(v, didv, "-", lw=1.4, color="#0f766e", label="dI/dV")
    axes[1].axvline(vp, color="#b91c1c", ls="--", lw=1.2, label=f"V* (dI/dV) = {vp:.3g} V")
    if vf is not None and np.isfinite(vf):
        axes[1].axvline(vf, color="#7c3aed", ls=":", lw=1.2, label=f"V_f (I=0) = {vf:.3g} V")
    axes[1].set_xlabel("Voltage (V)")
    axes[1].set_ylabel("dI/dV")
    axes[1].set_title(f"{title} — dI/dV")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg")
    plt.close(fig)
    stats["V_marked"] = vp
    return stats


def run_analysis(
    data_dir: Path = DEFAULT_DATA,
    out_dir: Path = DEFAULT_OUT,
) -> Dict[str, object]:
    lp_path = data_dir / "LP_07302026_140808.csv"
    rpa_path = data_dir / "RPA_combined_07302026_170652.csv"
    ep_path = data_dir / "EP_PlasmaDiagnostics_exp.csv"

    lp = load_lp(lp_path)
    rpa = load_rpa(rpa_path)
    ep = load_ep_sheet(ep_path)

    summary: Dict[str, object] = {"sources": {}, "estimates": {}}

    lp_stats = _plot_iv_didv(
        lp["Measured_V"].to_numpy(),
        lp["Measured_I"].to_numpy(),
        title="Langmuir Probe (LP)",
        out_path=out_dir / "lp-iv-didv.svg",
        ylabel_i="Current (A)",
        smooth_points=1,
        prefer_positive_peak=True,
    )
    summary["sources"]["LP"] = str(lp_path.name)
    summary["estimates"]["LP"] = lp_stats

    # RPA picoammeter trace is near the noise floor; light smoothing before dI/dV.
    rpa_stats = _plot_iv_didv(
        rpa["Sweep_V"].to_numpy(),
        rpa["Picoammeter_I"].to_numpy(),
        title="RPA collector (rudimentary; not Vp)",
        out_path=out_dir / "rpa-iv-didv.svg",
        ylabel_i="Collector current (A)",
        smooth_points=11,
        prefer_positive_peak=False,
    )
    summary["sources"]["RPA"] = str(rpa_path.name)
    summary["estimates"]["RPA"] = rpa_stats

    ep_plots = {}
    if len(ep["grounded"]):
        ep_plots["grounded"] = _plot_iv_didv(
            ep["grounded"]["V"].to_numpy(),
            ep["grounded"]["I"].to_numpy(),
            title="Emissive Probe grounded",
            out_path=out_dir / "ep-grounded-iv-didv.svg",
            ylabel_i="I (sheet units)",
            smooth_points=1,
            prefer_positive_peak=True,
        )
    if len(ep["floating_bias"]):
        ep_plots["floating_bias"] = _plot_iv_didv(
            ep["floating_bias"]["V"].to_numpy(),
            ep["floating_bias"]["I"].to_numpy(),
            title="Emissive Probe floating bias",
            out_path=out_dir / "ep-floating-bias-iv-didv.svg",
            ylabel_i="I (sheet units)",
            smooth_points=1,
            prefer_positive_peak=True,
        )
    if len(ep["floating_potential"]):
        # For strongly emitting EP, floating potential vs heater current approaches Vp.
        vf = ep["floating_potential"]["V"].to_numpy()
        ih = ep["floating_potential"]["I"].to_numpy()
        # Plot V_f(I) and mark the high-emission asymptote (last finite points mean).
        order = np.argsort(ih)
        ih, vf = ih[order], vf[order]
        vp_ep = float(np.mean(vf[-3:])) if len(vf) >= 3 else float(vf[-1])

        fig, ax = plt.subplots(figsize=(6.2, 4.0), constrained_layout=True)
        ax.plot(ih, vf, "o-", ms=4, lw=1.3, color="#1f4e79", label="Floating potential")
        ax.axhline(vp_ep, color="#b91c1c", ls="--", lw=1.2, label=f"Vp≈{vp_ep:.3g} V (high-I asymptote)")
        ax.set_xlabel("Heater / emission index I (sheet units)")
        ax.set_ylabel("Floating potential (V)")
        ax.set_title("Emissive Probe — floating potential vs emission")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        out = out_dir / "ep-floating-potential.svg"
        fig.savefig(out, format="svg")
        plt.close(fig)
        ep_plots["floating_potential"] = {
            "V_p_est": vp_ep,
            "n_points": float(len(vf)),
            "method": "high-emission floating-potential asymptote",
        }

    summary["sources"]["EP"] = str(ep_path.name)
    summary["estimates"]["EP"] = ep_plots

    summary_path = out_dir / "plasma-potential-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary = run_analysis(args.data_dir, args.out_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

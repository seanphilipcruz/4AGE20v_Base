"""Conservative offline VE diagnostics; writes reports only, never a tune."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Diagnostics/NON-EGO-VE"
OUT.mkdir(exist_ok=True)
FILES = ["NON EGO-1.msl", "NON-EGO-2.msl"]
root = ET.parse(ROOT / "CurrentTune.msq").getroot()
constants = {e.attrib["name"]: (e.text or "").strip() for e in root.iter() if e.tag.endswith("constant")}
rpm_axis = np.array(constants["rpmBins"].split(), float)
tps_axis = np.array(constants["fuelLoadBins"].split(), float)
table = np.array(constants["veTable"].split(), float).reshape(len(tps_axis), len(rpm_axis))
TPS_EDGES = [-.01, 0, 1, 5, 10, 15, 20, 25, 30, 40, 60, 80, 100]
TPS_LABELS = ["0", ">0–1", ">1–5", ">5–10", ">10–15", ">15–20", ">20–25", ">25–30", ">30–40", ">40–60", ">60–80", ">80–100"]
frames, summaries, regions, idle = {}, {}, [], []

def weighted_quantile(values, weights, probability):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    good = w > 0
    v, w = v[good], w[good]
    return float(np.interp(probability * w.sum(), np.cumsum(w) - .5 * w, v))

def summarize_region(z):
    duration = float(z.dt.sum())
    if duration <= 0:
        return None
    average = lambda c: float(np.average(z[c], weights=z.dt))
    corrections = [float(np.average(z[f"ratio_{d}"] - 1, weights=z.dt) * 100) for d in [0, .2, .4, .6]]
    indices = z.index.to_numpy()
    breaks = np.r_[True, np.diff(indices) > 1]
    visits = pd.Series(z.dt.to_numpy()).groupby(np.cumsum(breaks)).sum()
    return dict(seconds=duration, samples=len(z), windows_at_least_half_second=int((visits >= .5).sum()),
                RPM_min=float(z.RPM.min()), RPM_max=float(z.RPM.max()), TPS_min=float(z.TPS.min()), TPS_max=float(z.TPS.max()),
                AFR=average("AFR_delay_0.4"), target=average("AFR Target"), logged_VE=average("VE1"),
                calculated_effective_VE=average("VE_estimate"), full_ratio_change_percent=corrections[2],
                change_p10=100 * (weighted_quantile(z["ratio_0.4"], z.dt, .1) - 1),
                change_p90=100 * (weighted_quantile(z["ratio_0.4"], z.dt, .9) - 1),
                delay_sensitivity_percentage_points=max(corrections) - min(corrections),
                IAT_min=float(z.IAT.min()), IAT_max=float(z.IAT.max()), CLT_min=float(z.CLT.min()), CLT_max=float(z.CLT.max()))

for file in FILES:
    source = ROOT / "DataLogs" / file
    lines = source.read_text().splitlines()
    columns = lines[2].split("\t")
    assert all(len(row.split("\t")) == len(columns) for row in lines[4:])
    x = pd.read_csv(source, sep="\t", skiprows=[0, 1, 3])
    for c in x.select_dtypes(exclude="number"):
        assert x[c].isin(["On", "Off"]).all()
        x[c] = x[c].map({"On": 1, "Off": 0})
    stale = None
    if x.Time.iloc[0] > x.Time.iloc[1]:
        stale = x.iloc[0].to_dict()
        x = x.iloc[1:]
    x = x.reset_index(drop=True)
    assert not x.isna().any().any() and (x.Time.diff().dropna() >= 0).all()
    assert (x.Gego == 100).all()
    assert np.allclose(x.TPS, x.FuelLoad)
    x["dt"] = (x.Time.shift(-1) - x.Time).fillna(0)
    t = x.Time.to_numpy()
    ix = np.clip(np.searchsorted(rpm_axis, x.RPM, side="right") - 1, 0, len(rpm_axis) - 2)
    iy = np.clip(np.searchsorted(tps_axis, x.TPS, side="right") - 1, 0, len(tps_axis) - 2)
    fx = np.clip((x.RPM - rpm_axis[ix]) / (rpm_axis[ix + 1] - rpm_axis[ix]), 0, 1)
    fy = np.clip((x.TPS - tps_axis[iy]) / (tps_axis[iy + 1] - tps_axis[iy]), 0, 1)
    x["saved_interpolated_VE"] = (table[iy, ix] * (1-fx) * (1-fy) + table[iy, ix+1] * fx * (1-fy) +
                                  table[iy+1, ix] * (1-fx) * fy + table[iy+1, ix+1] * fx * fy)
    x["saved_VE_residual"] = x.VE1 - x.saved_interpolated_VE
    continuous = x.drop_duplicates("Time", keep="last")
    for delay in [0, .2, .4, .6]:
        x[f"AFR_delay_{delay}"] = np.interp(t + delay, continuous.Time, continuous.AFR)
        x[f"ratio_{delay}"] = x[f"AFR_delay_{delay}"] / x["AFR Target"]
    future = np.minimum(np.searchsorted(t, t + .6, side="left"), len(x) - 1)
    channels = ["TPS", "MAP", "RPM", "VE1", "Accel Enrich", "DFCO", "Hard Launch", "Hard Limiter", "Gammae", "PW", "Sync status", "VVT1 Duty"]
    rolling = x[channels].copy()
    rolling.index = pd.to_timedelta(t, unit="s")
    hi = rolling.rolling("2.6s").max(); lo = rolling.rolling("2.6s").min()
    high = lambda c: hi[c].to_numpy()[future]
    low = lambda c: lo[c].to_numpy()[future]
    conditions = {
        "complete_window": (t >= t[0] + 2) & (t + .6 <= t[-1]),
        "no_enrichment": (high("Accel Enrich") == 100) & (high("Gammae") == 100) & (low("Gammae") == 100),
        "no_cuts": (high("DFCO") == 0) & (high("Hard Launch") == 0) & (high("Hard Limiter") == 0) & (low("PW") > 0),
        "steady_TPS": high("TPS") - low("TPS") <= .5,
        "steady_MAP": high("MAP") - low("MAP") <= 3,
        "steady_RPM": high("RPM") - low("RPM") <= 150,
        "constant_logged_VE": high("VE1") == low("VE1"),
        "constant_VVT": high("VVT1 Duty") == low("VVT1 Duty"),
        "sync": (high("Sync status") == 2) & (low("Sync status") == 2),
        "not_closed_throttle_overrun": (x.TPS > 1) | (x.RPM <= 1300),
        "AFR_not_ceiling": x["AFR_delay_0.4"].between(9, 17.4),
    }
    x["accepted"] = np.logical_and.reduce([np.asarray(v) for v in conditions.values()])
    x["VE_estimate"] = x.VE1 * x["ratio_0.4"]
    x["rpm_band"] = (x.RPM // 500 * 500).astype(int)
    x["tps_band"] = pd.cut(x.TPS, TPS_EDGES, labels=TPS_LABELS)
    run = source.stem
    for (rpm, band), z in x[x.accepted].groupby(["rpm_band", "tps_band"], observed=True):
        # Keep changing table versions in the first capture separate.
        subsets = z.groupby("VE1") if file == FILES[0] else [(None, z)]
        for level, a in subsets:
            result = summarize_region(a)
            if result:
                result.update(run=run, rpm_lower=int(rpm), rpm_upper_exclusive=int(rpm + 500), TPS_band=str(band),
                              VE_level=float(level) if level is not None else None,
                              support="mixed" if result["change_p10"] < 0 < result["change_p90"] else
                              ("priority review" if result["seconds"] >= 5 and result["windows_at_least_half_second"] >= 2
                               and result["change_p90"] - result["change_p10"] <= 5 else "limited"))
                regions.append(result)
    idle_mask = x.accepted & (x.TPS == 0) & x.RPM.between(950, 1050) & x.MAP.between(37, 40)
    for level, z in x[idle_mask].groupby("VE1"):
        result = summarize_region(z)
        if result:
            result.update(run=run, VE_level=int(level), start_s=float(z.Time.min()), end_s=float(z.Time.max()))
            idle.append(result)
    summaries[run] = {"header": lines[:2], "rows": len(x), "removed_stale_initial_row": stale,
                      "duration_s": float(x.dt.sum()), "duplicate_timestamps": int(x.Time.duplicated().sum()),
                      "max_gap_s": float(x.dt.max()), "accepted_rows": int(x.accepted.sum()),
                      "accepted_seconds": float(x.loc[x.accepted, "dt"].sum()),
                      "Gego_unique": x.Gego.unique().tolist(), "sync_loss_start": int(x["Sync Loss #"].iloc[0]),
                      "sync_loss_end": int(x["Sync Loss #"].iloc[-1]),
                      "saved_VE_within_1_1_percent_of_rows": float((x.saved_VE_residual.abs() <= 1.1).mean() * 100),
                      "failed_filter_seconds_nonexclusive": {k: float(x.loc[~np.asarray(v), "dt"].sum()) for k,v in conditions.items()},
                      "range": {k: [float(x[k].min()), float(x[k].max())] for k in ["RPM", "TPS", "MAP", "AFR", "IAT", "CLT"]}}
    x.to_csv(OUT / (run + "-screened.csv"), index=False)
    frames[run] = x

regional = pd.DataFrame(regions)
regional.to_csv(OUT / "regional-VE-estimates.csv", index=False)
pd.DataFrame(idle).to_csv(OUT / "idle-VE-steps.csv", index=False)
sources = [ROOT / "DataLogs" / f for f in FILES] + [ROOT / "CurrentTune.msq", ROOT / "projectCfg/mainController.ini"]
metadata = {"runs": summaries, "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
            "formula": "VE_effective_estimate = logged_VE * measured_AFR_at_t_plus_delay / logged_target_at_t",
            "delay_seconds": .4, "sensitivity_seconds": [0, .2, .4, .6],
            "stability_window": "approximately t-2 to t+0.6, ending at first sample >= t+0.6",
            "quantization_tolerance": "Saved table agreement uses 1.1 VE percentage points, not percent relative change",
            "current_config": {k: constants.get(k) for k in ["algorithm", "multiplyMAP", "includeAFR", "incorporateAFR", "reqFuel", "injOpen"]},
            "no_tune_changes": True}
(OUT / "validation-and-method.json").write_text(json.dumps(metadata, indent=2))

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": .16, "figure.facecolor": "white"})
latest = regional[regional.run == "NON-EGO-2"]
matrix = np.full((len(TPS_LABELS), 11), np.nan); coverage = matrix.copy()
for _, row in latest.iterrows():
    col = int((row.rpm_lower - 500) / 500); ridx = TPS_LABELS.index(row.TPS_band)
    if 0 <= col < 11 and row.seconds >= 1:
        matrix[ridx, col] = row.full_ratio_change_percent; coverage[ridx, col] = row.seconds
fig, ax = plt.subplots(1, 2, figsize=(15, 7), constrained_layout=True)
fig.suptitle("NON-EGO-2 | Screened regional VE estimates and usable dwell", fontsize=16, weight="bold")
cmap = plt.get_cmap("RdBu_r").copy(); cmap.set_bad("#e6e8eb")
im = ax[0].imshow(matrix, origin="lower", aspect="auto", cmap=cmap, norm=TwoSlopeNorm(0, -20, 20))
covmap = plt.get_cmap("YlGnBu").copy(); covmap.set_bad("#e6e8eb")
im2 = ax[1].imshow(coverage, origin="lower", aspect="auto", cmap=covmap, vmin=0, vmax=16)
for axis, data, fmt, title in [(ax[0], matrix, "+.1f", "Calculated full correction (%) — not applied"),
                               (ax[1], coverage, ".1f", "Usable dwell (seconds)")]:
    axis.set_title(title, fontsize=11)
    axis.set_xticks(range(11), [f"{500+i*500}–\n{999+i*500}" for i in range(11)], fontsize=8)
    axis.set_yticks(range(len(TPS_LABELS)), TPS_LABELS)
    axis.set(xlabel="Observed RPM interval", ylabel="Observed TPS band (%)")
    axis.grid(False)
    for (ri, ci), value in np.ndenumerate(data):
        if np.isfinite(value):
            ink = "white" if (axis is ax[1] and value > 10) or (axis is ax[0] and abs(value) > 12) else "black"
            axis.text(ci, ri, format(value, fmt), ha="center", va="center", fontsize=9, color=ink)
fig.colorbar(im, ax=ax[0], label="Negative: less VE; positive: more VE")
fig.colorbar(im2, ax=ax[1], label="Seconds")
fig.supxlabel("Gray: less than 1 second retained. Regional estimates affect shared interpolated nodes; no full-load VE changes supported.", fontsize=9)
fig.savefig(OUT / "regional-VE-map.png", dpi=150, bbox_inches="tight")
fig.savefig(OUT / "regional-VE-map.svg", bbox_inches="tight"); plt.close(fig)

first = frames["NON EGO-1"]
y = first[first.Time.between(380, 429.55)]
fig, ax = plt.subplots(3, 1, figsize=(12, 8), sharex=True, constrained_layout=True)
fig.suptitle("NON EGO-1 | Logged idle VE changes during the recording", fontsize=16, weight="bold")
ax[0].plot(y.Time, y.RPM, color="#276b9d", label="RPM"); ax[0].axhline(1000, color="gray", ls="--")
ax[0].set_ylabel("RPM")
ax[1].plot(y.Time, y.AFR, color="#cf4b40", label="Measured AFR")
ax[1].plot(y.Time, y["AFR Target"], color="#2b3d55", label="Target")
ax[1].set_ylabel("AFR"); ax[1].legend(loc="upper left", ncol=2)
ax[2].step(y.Time, y.VE1, color="#008477", where="post", label="Logged VE")
ax[2].axhline(float(table[0, 1]), color="gray", ls="--", label="Later saved 1000 RPM / 0% TPS cell")
ax[2].set(ylabel="VE (%)", xlabel="Elapsed time (seconds)"); ax[2].legend(loc="upper right")
for axis in ax: axis.set_xlim(380, 429.55)
fig.savefig(OUT / "idle-VE-trial.png", dpi=150, bbox_inches="tight")
fig.savefig(OUT / "idle-VE-trial.svg", bbox_inches="tight"); plt.close(fig)
print(json.dumps({k:{a:v for a,v in s.items() if a in ["rows", "duration_s", "accepted_seconds", "saved_VE_within_1_1_percent_of_rows"]} for k,s in summaries.items()},indent=2))
print(latest[latest.seconds>=1][["rpm_lower", "TPS_band", "seconds", "windows_at_least_half_second", "AFR", "target", "logged_VE", "calculated_effective_VE", "full_ratio_change_percent", "change_p10", "change_p90", "support"]].round(2).to_string(index=False))
print(pd.DataFrame(idle)[["run", "VE_level", "seconds", "AFR", "target", "calculated_effective_VE"]].round(3).to_string(index=False))

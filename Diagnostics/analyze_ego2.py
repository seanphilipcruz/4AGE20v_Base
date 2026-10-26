"""Validate EGO-2 text log, separate feedback phases, and compare EGO-1."""
from pathlib import Path
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "DataLogs/EGO-2.msl"
OUT = ROOT / "Diagnostics/EGO-2"
OUT.mkdir(exist_ok=True)
lines = SOURCE.read_text().splitlines()
header = lines[2].split("\t")
units = lines[3].split("\t")
assert len(header) == len(set(header)) == len(units)
assert all(len(row.split("\t")) == len(header) for row in lines[4:])
x = pd.read_csv(SOURCE, sep="\t", skiprows=[0, 1, 3])
for column in x.columns:
    if column.startswith("Engine Prot."):
        assert x[column].isin(["Off", "On"]).all()
        x[column] = x[column].map({"Off": 0, "On": 1})
assert not x.isna().any().any()
assert (x.Time.diff().dropna() >= 0).all()
x["dt"] = (x.Time.shift(-1) - x.Time).fillna(0)
x["error"] = x.AFR - x["AFR Target"]
last_adjustment = int(x.index[x.Gego != 100][-1])
boundary = last_adjustment + 1
switch_time = float(x.Time.iloc[boundary])
x["phase"] = np.where(x.index < boundary, "varying EGO", "neutral EGO")

def settled(frame):
    r = frame[["TPS DOT", "rpm/s", "Accel Enrich", "DFCO"]].abs().copy()
    r.index = pd.to_timedelta(frame.Time, unit="s")
    q = r.rolling("2s").max()
    return ((q["TPS DOT"].to_numpy() <= 5) & (q["rpm/s"].to_numpy() <= 200) &
            (q["Accel Enrich"].to_numpy() == 100) & (q.DFCO.to_numpy() == 0) &
            (frame.Time >= frame.Time.iloc[0] + 2))

x["settled"] = settled(x)
x.loc[(x.index >= boundary) & (x.Time < switch_time + 2), "settled"] = False

def describe(frame):
    result = {"samples": len(frame), "seconds": float(frame.dt.sum())}
    if frame.empty or frame.dt.sum() <= 0:
        return result
    for channel in ["RPM", "TPS", "MAP", "AFR", "AFR Target", "Gego", "PW", "IAC value"]:
        result[channel] = {"min": float(frame[channel].min()), "max": float(frame[channel].max()),
                           "median": float(frame[channel].median()),
                           "mean": float(np.average(frame[channel], weights=frame.dt))}
    result["within_0.5_percent"] = float(np.average(frame.error.abs() <= .50001, weights=frame.dt) * 100)
    for limit in [84, 116]:
        result[f"EGO_{limit}_seconds"] = float(frame.loc[frame.Gego == limit, "dt"].sum())
    return result

metrics = {}
for name, mask in [("whole", x.RPM > 0), ("varying EGO", x.index < boundary), ("neutral EGO", x.index >= boundary)]:
    y = x[mask]
    groups = {"all": y.RPM > 0, "fueled": (y.PW > 0) & (y.DFCO == 0),
              "settled_idle": y.settled & (y.TPS <= 1) & y.RPM.between(700, 1300),
              "centered_idle": y.settled & (y.TPS == 0) & y.RPM.between(900, 1100),
              "settled_above_idle": y.settled & (y.TPS > 1),
              "high_MAP": (y.MAP >= 80) & (y.PW > 0),
              "high_TPS": (y.TPS >= 80) & (y.PW > 0), "DFCO": y.DFCO == 1}
    metrics[name] = {group: describe(y[condition]) for group, condition in groups.items()}

event_mask = (x.MAP >= 80) & (x.TPS > 15) & (x.PW > 0) & (x.error > 1.00001) & (x["Hard Launch"] == 0)
groups = (event_mask != event_mask.shift()).cumsum()
events = []
for _, y in x[event_mask].groupby(groups[event_mask]):
    if y.dt.sum() <= .2:
        continue
    item = {"start_s": float(y.Time.iloc[0]), "end_s": float(y.Time.iloc[-1]),
            "seconds": float(y.dt.sum()), "phase": str(y.phase.iloc[0])}
    for c in ["RPM", "TPS", "MAP", "AFR", "AFR Target", "Gego", "Accel Enrich"]:
        item[c + " min"] = float(y[c].min()); item[c + " max"] = float(y[c].max())
    events.append(item)
pd.DataFrame(events).to_csv(OUT / "high-load-excursions.csv", index=False)
x.to_csv(OUT / "EGO-2-analysis.csv", index=False)

bands = []
y = x[(x.index >= boundary) & x.settled & (x.TPS > 1)].copy()
y["rpm_band"] = (y.RPM // 500 * 500).astype(int)
y["tps_band"] = pd.cut(y.TPS, [1, 5, 10, 20, 40, 100])
for (rpm, tps), z in y.groupby(["rpm_band", "tps_band"], observed=True):
    if z.dt.sum() >= 2:
        bands.append({"rpm_lower": int(rpm), "rpm_upper_exclusive": int(rpm + 500),
                      "TPS_band": str(tps), "seconds": float(z.dt.sum()),
                      "mean_AFR": float(np.average(z.AFR, weights=z.dt)),
                      "mean_target": float(np.average(z["AFR Target"], weights=z.dt))})
pd.DataFrame(bands).to_csv(OUT / "neutral-EGO-steady-bands.csv", index=False)

baseline = pd.read_csv(ROOT / "Diagnostics/EGO-1/EGO-1-analysis.csv")
baseline["error"] = baseline.AFR - baseline["AFR Target"]
metrics["EGO-1_comparison"] = {
    "settled_idle": describe(baseline[baseline.settled & (baseline.TPS <= 1) & baseline.RPM.between(700, 1300)]),
    "high_MAP": describe(baseline[(baseline.MAP >= 80) & (baseline.PW > 0)])}
validation = {"source": str(SOURCE), "header": lines[:2], "units": dict(zip(header, units)),
              "rows": len(x), "columns": len(header), "units_row_skipped": True,
              "initial_clock_reset_removed": False, "duplicate_timestamps": int(x.Time.duplicated().sum()),
              "largest_gap_s": float(x.dt.max()), "negative_intervals": int((x.dt < 0).sum()),
              "neutral_EGO_start_row_zero_based": boundary, "neutral_EGO_start_s": switch_time,
              "neutral_EGO_values": sorted(x.loc[boundary:, "Gego"].unique().tolist()),
              "checksums": "Text MSL export has no per-record checksum available for verification"}
(OUT / "validation.json").write_text(json.dumps(validation, indent=2))
metrics["method"] = {"settled_filter": "preceding 2s: abs(TPS DOT)<=5, abs(rpm/s)<=200, AE=100%, DFCO=0; excludes first 2s after neutral phase begins",
                     "time_weighting": "sample held until next timestamp; duplicate and final samples have zero duration",
                     "transport_delay": "No exhaust transport-delay correction applied",
                     "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                       [SOURCE, ROOT / "CurrentTune.msq", ROOT / "projectCfg/mainController.ini"]}}
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
root = ET.parse(ROOT / "CurrentTune.msq").getroot()
constants = {e.attrib["name"]: (e.text or "").strip() for e in root.iter() if e.tag.endswith("constant")}
selected = ["egoAlgorithm", "egoType", "egoLimit", "egoRPM", "egoTPSMax", "egoTemp", "egoKP", "egoKI", "egoKD",
            "algorithm", "includeAFR", "incorporateAFR", "iacTPSlimit", "idleAdvTPS", "idleFreq", "aeTime", "taeRates",
            "launchEnable", "launchPin", "launchHiLo", "lnchHardLim", "lnchSoftLim", "afrProtectEnabled"]
(OUT / "later-saved-tune-context.json").write_text(json.dumps({k: constants.get(k) for k in selected}, indent=2))

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": .18, "figure.facecolor": "white"})
clock = FuncFormatter(lambda v, _: f"{int(v)//60}:{int(v)%60:02d}")

def finish(fig, name):
    fig.savefig(OUT / (name + ".png"), dpi=150, bbox_inches="tight")
    fig.savefig(OUT / (name + ".svg"), bbox_inches="tight")
    plt.close(fig)

fig, ax = plt.subplots(4, 1, figsize=(13, 11), sharex=True, constrained_layout=True)
fig.suptitle("EGO-2 | 24 Oct 2026, 11:35 PHT | 12 min 31 s", fontsize=17, weight="bold")
ax[0].plot(x.Time, x.RPM, lw=.9, color="#246d9c", label="RPM")
sec = ax[0].twinx(); sec.plot(x.Time, x.TPS, lw=.7, color="#bd841d", alpha=.7, label="TPS")
sec.set(ylabel="Throttle (%)", ylim=(0, 110)); sec.legend(loc="upper right")
ax[0].set_ylabel("RPM"); ax[0].legend(loc="upper left")
ax[1].plot(x.Time, x.AFR, lw=.7, color="#cc4b40", label="Logged AFR")
ax[1].plot(x.Time, x["AFR Target"], lw=1.1, color="#263e55", label="Logged target")
ax[1].fill_between(x.Time, 9, 18.2, where=x.DFCO == 1, color="gray", alpha=.24, label="Fuel cut")
ax[1].set(ylabel="AFR, gasoline scale", ylim=(9, 18.2)); ax[1].legend(loc="lower right", ncol=3)
ax[2].plot(x.Time, x.Gego, color="#7242a5", lw=.9)
for n in [84, 100, 116]: ax[2].axhline(n, ls="--", color="gray", lw=.7)
ax[2].set(ylabel="EGO multiplier (%)", ylim=(80, 120))
ax[2].text(320, 109, "No applied EGO adjustment from 5:04.544", fontsize=10)
ax[3].plot(x.Time, x.CLT, color="#cc4b40", label="Coolant")
ax[3].plot(x.Time, x.IAT, color="#bd841d", label="Intake air")
ax[3].set(ylabel="Temperature (°C)", xlabel="Elapsed log time (m:ss)"); ax[3].legend(loc="center right")
for a in ax:
    a.axvline(switch_time, color="#222", ls="--", lw=1)
    a.set_xlim(0, x.Time.iloc[-1]); a.xaxis.set_major_formatter(clock)
finish(fig, "overview")

fig, ax = plt.subplots(4, 2, figsize=(15, 11), sharex="col", constrained_layout=True)
fig.suptitle("EGO-2 | Rich small-throttle running and lean load transitions", fontsize=16, weight="bold")
for col, (start, stop, title) in enumerate([(305, 342, "Neutral EGO: rich idle / small-throttle running"),
                                          (602, 622, "Neutral EGO: lean after acceleration enrichment ends")]):
    y = x[x.Time.between(start, stop)]
    ax[0, col].set_title(title, fontsize=11)
    ax[0, col].plot(y.Time, y.RPM, color="#246d9c", lw=1.1)
    sec = ax[0, col].twinx(); sec.plot(y.Time, y.TPS, color="#bd841d", lw=1)
    sec.set_ylabel("TPS (%)")
    ax[1, col].plot(y.Time, y.AFR, color="#cc4b40", lw=1.2, label="AFR")
    ax[1, col].plot(y.Time, y["AFR Target"], color="#263e55", lw=1.2, label="Target")
    ax[1, col].fill_between(y.Time, 9.5, 18.2, where=y.DFCO == 1, color="gray", alpha=.24)
    ax[1, col].set_ylim(9.5, 18.2); ax[1, col].legend(loc="lower left", ncol=2)
    ax[2, col].plot(y.Time, y["Accel Enrich"], color="#bd841d", label="Accel enrichment")
    ax[2, col].plot(y.Time, y.Gego, color="#7242a5", label="EGO")
    ax[2, col].legend(loc="upper left", ncol=2, fontsize=8)
    ax[3, col].plot(y.Time, y.PW, color="#008877", lw=1.2)
    for row, label in enumerate(["RPM", "AFR", "Fuel multipliers (%)", "Injector pulse (ms)"]):
        ax[row, col].set_ylabel(label); ax[row, col].set_xlim(start, stop)
        ax[row, col].xaxis.set_major_formatter(clock)
    ax[3, col].set_xlabel("Elapsed log time (m:ss)")
    if col == 1:
        for a in ax[:, col]:
            a.axvspan(606.84, 607.45, color="#e4ad17", alpha=.16)
            a.axvspan(617.11, 617.85, color="#e4ad17", alpha=.16)
fig.supxlabel("No exhaust delay correction applied. Gray shading: DFCO. Yellow: lean excursions with nonzero injector pulse.", fontsize=9)
finish(fig, "event-details")
print(json.dumps({phase: {g: metrics[phase][g] for g in ["settled_idle", "centered_idle", "settled_above_idle"]}
                  for phase in ["varying EGO", "neutral EGO"]}, indent=2))

"""Produce time-weighted diagnostics and figures from the validated MLG export."""
from pathlib import Path
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
OUT = ROOT / "Diagnostics" / "EGO-1"
x = pd.read_csv(OUT / "EGO-1-decoded.csv")
assert x.Time.iloc[1] < x.Time.iloc[0] and (x.Time.iloc[1:].diff().dropna() >= 0).all()
stale = x.iloc[0].to_dict()
x = x.iloc[1:].reset_index(drop=True)
x["dt"] = (x.Time.shift(-1) - x.Time).fillna(0)
x["error"] = x.AFR - x["AFR Target"]
r = x[["TPS DOT", "rpm/s", "Accel Enrich", "DFCO"]].abs()
r.index = pd.to_timedelta(x.Time, unit="s")
q = r.rolling("2s").max()
x["settled"] = ((q["TPS DOT"].to_numpy() <= 5) &
                (q["rpm/s"].to_numpy() <= 200) &
                (q["Accel Enrich"].to_numpy() == 100) &
                (q.DFCO.to_numpy() == 0) & (x.Time >= 2))
groups = {
    "all": x.RPM > 0,
    "fueled": x.PW > 0,
    "settled_idle": x.settled & (x.TPS <= 1) & x.RPM.between(700, 1300),
    "settled_above_idle": x.settled & (x.TPS > 1),
    "final_idle": x.Time >= 840,
    "high_load": (x.MAP >= 80) & (x.PW > 0),
    "high_throttle": (x.TPS >= 80) & (x.PW > 0),
    "fuel_cut": x.DFCO == 1,
}
metrics = {}
for name, mask in groups.items():
    y = x[mask]
    metrics[name] = {"samples": len(y), "seconds": float(y.dt.sum())}
    for channel in ["RPM", "TPS", "MAP", "AFR", "AFR Target", "Gego", "IAC value", "PW"]:
        metrics[name][channel] = dict(min=float(y[channel].min()),
                                     median=float(y[channel].median()),
                                     mean=float(np.average(y[channel], weights=y.dt)),
                                     max=float(y[channel].max()))
    metrics[name]["within_0.5_AFR_percent"] = float(100 * np.average(
        y.error.abs() <= 0.50001, weights=y.dt))
    for limit in [84, 116]:
        metrics[name][f"EGO_{limit}_seconds"] = float(y.loc[y.Gego == limit, "dt"].sum())

mask = ((x.MAP >= 80) & (x.error > 1.00001) & (x.PW > 0) & (x.TPS > 15))
event_groups = (mask != mask.shift()).cumsum()
events = []
for _, y in x[mask].groupby(event_groups[mask]):
    if y.dt.sum() <= 0.2:
        continue
    event = dict(start_s=float(y.Time.iloc[0]), end_s=float(y.Time.iloc[-1]),
                 duration_s=float(y.dt.sum()))
    for channel in ["RPM", "TPS", "MAP", "AFR", "AFR Target", "Gego"]:
        event[channel + " min"] = float(y[channel].min())
        event[channel + " max"] = float(y[channel].max())
    events.append(event)
pd.DataFrame(events).to_csv(OUT / "high-load-excursions.csv", index=False)
x.to_csv(OUT / "EGO-1-analysis.csv", index=False)
metrics["method"] = dict(
    excluded_initial_record=stale,
    settled_window_seconds=2,
    settled_conditions="abs(TPS DOT)<=5 %/s, abs(rpm/s)<=200, AE=100%, DFCO=0 throughout preceding 2s",
    idle_conditions="settled, TPS<=1%, RPM 700-1300",
    time_weighting="Hold each sample until next timestamp; duplicate timestamps have zero duration; final sample has zero duration",
    transport_delay="AFR and commanded target are compared at logged timestamps; no exhaust delay correction applied",
    no_steady_high_rpm_samples=int((x.settled & (x.RPM > 1800)).sum()),
)
metrics["sources"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [ROOT / "CurrentTune.msq", ROOT / "DataLogs/EGO-1.mlg", ROOT / "projectCfg/mainController.ini"]}
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.18, "figure.facecolor": "white"})
colors = {"afr": "#d1493f", "target": "#263b53", "rpm": "#23699a", "tps": "#b5680a",
          "ego": "#6d43a6", "iac": "#07806a"}
clock = FuncFormatter(lambda value, _: f"{int(value)//60}:{int(value)%60:02d}")

def finish(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=150, bbox_inches="tight")
    fig.savefig(OUT / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)

fig, ax = plt.subplots(4, 1, figsize=(13, 11), sharex=True, constrained_layout=True)
fig.suptitle("EGO-1 | 24 Oct 2026, 09:10 PHT | 15 min 11 s", fontsize=17, weight="bold")
ax[0].plot(x.Time, x.RPM, color=colors["rpm"], lw=1, label="RPM")
ax[0].axhline(1000, color="gray", lw=.8, ls="--")
ax[0].set(ylabel="Engine speed (RPM)", ylim=(500, 5200))
second = ax[0].twinx()
second.plot(x.Time, x.TPS, color=colors["tps"], lw=.8, alpha=.65, label="TPS")
second.set(ylabel="Throttle (%)", ylim=(0, 110))
ax[0].legend(loc="upper left"); second.legend(loc="upper right")
ax[1].plot(x.Time, x.AFR, color=colors["afr"], lw=.65, label="Logged AFR")
ax[1].plot(x.Time, x["AFR Target"], color=colors["target"], lw=1.2, label="Logged target")
ax[1].fill_between(x.Time, 9.5, 18.1, where=x.DFCO == 1, color="gray", alpha=.25, label="Fuel cut (PW=0)")
ax[1].set(ylabel="AFR, gasoline scale", ylim=(9.5, 18.1))
ax[1].legend(loc="lower right", ncol=3)
ax[2].plot(x.Time, x.Gego, color=colors["ego"], lw=.8)
for val in [84, 100, 116]:
    ax[2].axhline(val, color="gray", ls="--", lw=.8)
ax[2].set(ylabel="EGO correction (%)", ylim=(80, 120))
ax[2].text(12, 117, "116% = +16% fuel", fontsize=9)
ax[2].text(12, 81, "84% = -16% fuel", fontsize=9)
ax[3].plot(x.Time, x.CLT, color="#c75040", lw=1.2, label="Coolant")
ax[3].plot(x.Time, x.IAT, color="#cb902e", lw=1.2, label="Intake air")
ax[3].set(ylabel="Temperature (°C)", xlabel="Elapsed log time (m:ss)", ylim=(43, 74))
ax[3].legend(loc="center right")
for a in ax:
    a.set_xlim(0, x.Time.iloc[-1]); a.xaxis.set_major_formatter(clock)
    a.axvspan(410.8, 413.95, color="#e4a800", alpha=.12)
    a.axvspan(424.6, 426.95, color="#e4a800", alpha=.12)
fig.supxlabel("All 13,657 data records passed checksum; first pre-clock-reset record excluded from analysis.", fontsize=9)
finish(fig, "overview")

fig, ax = plt.subplots(4, 2, figsize=(15, 11), sharex="col", constrained_layout=True)
fig.suptitle("High-load AFR excursions and unstable idle | EGO-1", fontsize=17, weight="bold")
for col, (start, stop, title) in enumerate([(408, 438, "High load: lean spikes while fuel is being injected"),
                                          (620, 724, "Idle recovery: low RPM despite high valve command")]):
    y = x[x.Time.between(start, stop)]
    ax[0, col].set_title(title, fontsize=12)
    ax[0, col].plot(y.Time, y.RPM, color=colors["rpm"], lw=1.2, label="RPM")
    ax[0, col].axhline(1000, ls="--", color="gray", lw=.8)
    sec = ax[0, col].twinx()
    sec.plot(y.Time, y.TPS, color=colors["tps"], lw=1, label="TPS (%)")
    sec.set_ylim(0, 110); sec.set_ylabel("TPS (%)")
    ax[1, col].plot(y.Time, y.AFR, color=colors["afr"], lw=1.2, label="Measured")
    ax[1, col].plot(y.Time, y["AFR Target"], color=colors["target"], lw=1.4, label="Target")
    ax[1, col].fill_between(y.Time, 9.5, 18.2, where=y.DFCO == 1, color="gray", alpha=.25)
    ax[1, col].set_ylim(9.5, 18.2)
    ax[1, col].legend(loc="lower right", ncol=2)
    ax[2, col].plot(y.Time, y.Gego, color=colors["ego"], lw=1, label="EGO")
    ax[2, col].plot(y.Time, y["Accel Enrich"], color="#b5680a", lw=.9, alpha=.7, label="Accel enrichment")
    ax[2, col].axhline(100, color="gray", ls="--", lw=.8)
    ax[2, col].legend(loc="upper right", ncol=2, fontsize=8)
    if col == 0:
        ax[3, col].plot(y.Time, y.PW, color=colors["iac"], lw=1.2)
        ax[3, col].set_ylabel("Injector pulse (ms)")
        for a in ax[:, col]:
            a.axvspan(411.05, 411.92, color="#e4a800", alpha=.16)
            a.axvspan(425.05, 425.72, color="#e4a800", alpha=.16)
    else:
        ax[3, col].plot(y.Time, y["IAC value"], color=colors["iac"], lw=1.2)
        ax[3, col].set(ylabel="Idle command (%)", ylim=(45, 105))
        for a in ax[:, col]:
            a.axvspan(636.4, 638.2, color="#e4a800", alpha=.16)
    for a in ax[:, col]:
        a.set_xlim(start, stop); a.xaxis.set_major_formatter(clock)
    ax[3, col].set_xlabel("Elapsed time (m:ss)")
    ax[0, col].set_ylabel("RPM"); ax[1, col].set_ylabel("AFR")
    ax[2, col].set_ylabel("Fuel multipliers (%)")
fig.supxlabel("Targets are the logged targets. Exhaust transport delay is uncorrected; gray shading marks fuel cut.", fontsize=9)
finish(fig, "event-details")

root = ET.parse(ROOT / "CurrentTune.msq").getroot()
constants = {e.attrib["name"]: (e.text or "").strip() for e in root.iter() if e.tag.endswith("constant")}
snapshot = {key: constants.get(key) for key in ["algorithm", "multiplyMAP", "includeAFR", "incorporateAFR",
    "egoType", "egoAlgorithm", "egoKP", "egoKI", "egoKD", "egoLimit", "egoTemp", "egoRPM", "egoTPSMax",
    "egoMAPMin", "egoMAPMax", "ego_sdelay", "iacAlgorithm", "idleFreq", "iacChannels", "iacPWMdir",
    "iacCLminValue", "iacCLmaxValue", "afrProtectEnabled", "stoich"]}
(OUT / "saved-tune-context.json").write_text(json.dumps(snapshot, indent=2))
print(json.dumps({key: {k: v for k, v in value.items() if k in ["seconds", "within_0.5_AFR_percent", "EGO_84_seconds", "EGO_116_seconds"]}
                  for key, value in metrics.items() if key in groups}, indent=2))

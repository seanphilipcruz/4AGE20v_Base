"""Offline pull VE analysis and a review-only seven-node proposal."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Diagnostics/GEAR-PULL-VE"
OUT.mkdir(exist_ok=True)
FILES = ["1st gear.msl", "1st gear pull.msl", "3rd gear pull.msl", "4th gear pull.msl"]
root = ET.parse(ROOT / "CurrentTune.msq").getroot()
constants = {e.attrib["name"]: (e.text or "").strip() for e in root.iter() if e.tag.endswith("constant")}
rpms = np.array(constants["rpmBins"].split(), float)
loads = np.array(constants["fuelLoadBins"].split(), float)
original = np.array(constants["veTable"].split(), float).reshape(len(loads), len(rpms))
proposed = original.copy()
# These additions are a conservative first-stage proposal, not a solved full map.
REQUESTED = [(2500, 30, 57), (3000, 30, 57), (3500, 60, 69),
             (4000, 60, 71), (4000, 70, 72), (4500, 70, 74), (4500, 80, 75)]
changes = []
for rpm, tps, value in REQUESTED:
    col = int(np.flatnonzero(rpms == rpm)[0]); row = int(np.flatnonzero(loads == tps)[0])
    old = int(original[row, col])
    proposed[row, col] = value
    changes.append(dict(RPM=rpm, TPS=tps, current_VE=old, proposed_VE=value,
                        change_percent=100 * (value / old - 1), evidence="4th gear pull",
                        status="proposal only; not applied"))
assert len(changes) == 7
assert np.all(proposed >= original)
# Do not introduce a new decrease between adjacent TPS nodes.
assert not (((np.diff(original, axis=0) >= 0) & (np.diff(proposed, axis=0) < 0)).any())
pd.DataFrame(changes).to_csv(OUT / "proposed-cell-changes.csv", index=False)
pd.DataFrame(proposed.astype(int), index=loads, columns=rpms).rename_axis("TPS percent / RPM").to_csv(OUT / "proposed-VE-table-review-only.csv")

def interpolation(frame, table):
    ix = np.clip(np.searchsorted(rpms, frame.RPM, side="right") - 1, 0, len(rpms)-2)
    iy = np.clip(np.searchsorted(loads, frame.TPS, side="right") - 1, 0, len(loads)-2)
    fx = np.clip((frame.RPM.to_numpy()-rpms[ix])/(rpms[ix+1]-rpms[ix]), 0, 1)
    fy = np.clip((frame.TPS.to_numpy()-loads[iy])/(loads[iy+1]-loads[iy]), 0, 1)
    weights = [(iy, ix, (1-fx)*(1-fy)), (iy, ix+1, fx*(1-fy)),
               (iy+1, ix, (1-fx)*fy), (iy+1, ix+1, fx*fy)]
    value = sum(table[row, col] * weight for row, col, weight in weights)
    return value, weights

def average(frame, column):
    return float(np.average(frame[column], weights=frame.dt))

frames, summaries, regions, nodes = {}, {}, [], []
for file in FILES:
    source = ROOT / "DataLogs" / file
    lines = source.read_text().splitlines()
    assert all(len(row.split("\t")) == len(lines[2].split("\t")) for row in lines[4:])
    x = pd.read_csv(source, sep="\t", skiprows=[0, 1, 3])
    raw_rows = len(x)
    stale = None
    if x.Time.iloc[0] > x.Time.iloc[1]:
        stale = x.iloc[0].to_dict(); x = x.iloc[1:]
    x = x.reset_index(drop=True)
    for column in x.select_dtypes(exclude="number"):
        assert x[column].isin(["On", "Off"]).all()
        x[column] = x[column].map({"On": 1, "Off": 0})
    assert not x.isna().any().any() and (x.Time.diff().dropna() >= 0).all()
    assert (x.Gego == 100).all() and np.allclose(x.FuelLoad, x.TPS)
    x["dt"] = (x.Time.shift(-1)-x.Time).fillna(0)
    t = x.Time.to_numpy()
    current, weights = interpolation(x, original)
    revised, _ = interpolation(x, proposed)
    x["saved_interpolated_VE"] = current
    x["VE_residual"] = x.VE1-current
    assert (x.VE_residual.abs() <= 1.1).all()
    x["proposed_fuel_multiplier"] = revised/current
    continuous = x.drop_duplicates("Time", keep="last")
    for delay in [0, .2, .4, .6]:
        x[f"AFR_delay_{delay}"] = np.interp(t+delay, continuous.Time, continuous.AFR)
        x[f"ratio_{delay}"] = x[f"AFR_delay_{delay}"]/x["AFR Target"]
    x["modeled_proposed_AFR"] = x["AFR_delay_0.4"]/x.proposed_fuel_multiplier
    selected = ["TPS", "MAP", "Accel Enrich", "DFCO", "Hard Launch", "Hard Limiter", "PW", "VVT1 Duty", "Gammae", "Sync status"]
    rolling = x[selected].copy(); rolling.index = pd.to_timedelta(t, unit="s")
    hi = rolling.rolling("1.1s").max(); lo = rolling.rolling("1.1s").min()
    future = np.minimum(np.searchsorted(t, t+.6, side="left"), len(x)-1)
    high = lambda c: hi[c].to_numpy()[future]
    low = lambda c: lo[c].to_numpy()[future]
    # Longer launch guard: at least 1 s before the engine sample through 0.6 s after it.
    launch_hi = rolling["Hard Launch"].rolling("1.6s").max().to_numpy()[future]
    conditions = {
        "window_available": (t >= .5) & (t+.6 <= t[-1]),
        "no_enrichment": (high("Accel Enrich") == 100) & (high("Gammae") == 100) & (low("Gammae") == 100),
        "no_cut_or_launch": (high("DFCO") == 0) & (launch_hi == 0) & (high("Hard Limiter") == 0) & (low("PW") > 0),
        "settled_throttle": high("TPS")-low("TPS") <= 5,
        "settled_MAP": high("MAP")-low("MAP") <= 3,
        "constant_VVT_command": high("VVT1 Duty") == low("VVT1 Duty"),
        "synchronized": (high("Sync status") == 2) & (low("Sync status") == 2),
        "loaded": (x.TPS >= 30) & (x.MAP >= 80),
        "AFR_not_clipped": x["AFR_delay_0.4"].between(9, 17.4),
    }
    x["accepted"] = np.logical_and.reduce([np.asarray(v) for v in conditions.values()])
    x["rpm_band"] = (x.RPM//500*500).astype(int)
    x["TPS_band"] = pd.cut(x.TPS, [0,30,40,50,60,70,80,90,100])
    for (rpm, band), y in x[x.accepted].groupby(["rpm_band", "TPS_band"], observed=True):
        if y.dt.sum() <= .1: continue
        differences = [100*(average(y,f"ratio_{d}")-1) for d in [0,.2,.4,.6]]
        regions.append(dict(run=source.stem, RPM_lower=int(rpm), RPM_upper_exclusive=int(rpm+500), TPS_band=str(band),
                            seconds=float(y.dt.sum()), samples=len(y), measured_AFR=average(y,"AFR_delay_0.4"),
                            target=average(y,"AFR Target"), logged_VE=average(y,"VE1"),
                            calculated_full_change_percent=differences[2], delay_sensitivity_pp=max(differences)-min(differences),
                            modeled_proposed_AFR=average(y,"modeled_proposed_AFR"),
                            modeled_fuel_increase_percent=100*(average(y,"proposed_fuel_multiplier")-1)))
    for row, col, weight in weights:
        for pos in np.flatnonzero(x.accepted.to_numpy() & (weight > 0) & (x.dt.to_numpy() > 0)):
            nodes.append(dict(run=source.stem, RPM=int(rpms[col[pos]]), TPS=int(loads[row[pos]]),
                              weighted_seconds=float(x.dt.iloc[pos]*weight[pos]),
                              correction=100*float(x["ratio_0.4"].iloc[pos]-1)))
    summaries[source.stem] = dict(header=lines[:2], raw_rows=raw_rows, rows=len(x), excluded_initial_record=stale,
                                 duration_s=float(x.dt.sum()), duplicate_times=int(x.Time.duplicated().sum()), largest_gap_s=float(x.dt.max()),
                                 accepted_seconds=float(x.loc[x.accepted,"dt"].sum()),
                                 saved_table_within_1_1_VE_points=True,
                                 sync_loss_start=int(x["Sync Loss #"].iloc[0]), sync_loss_end=int(x["Sync Loss #"].iloc[-1]),
                                 launch_records=x.loc[x["Hard Launch"]!=0,["Time","RPM","TPS","AFR","AFR Target"]].to_dict("records"),
                                 ranges={k:[float(x[k].min()),float(x[k].max())] for k in ["RPM","TPS","MAP","AFR","AFR Target","CLT","IAT","Duty Cycle"]},
                                 filter_failed_seconds_nonexclusive={k:float(x.loc[~np.asarray(v),"dt"].sum()) for k,v in conditions.items()})
    x.to_csv(OUT/(source.stem+"-analysis.csv"),index=False); frames[source.stem]=x

region_table=pd.DataFrame(regions)
region_table.to_csv(OUT/"regional-results-and-proposal-replay.csv",index=False)
node_rows=[]
for (rpm,tps), y in pd.DataFrame(nodes).groupby(["RPM","TPS"]):
    node_rows.append(dict(RPM=int(rpm),TPS=int(tps),interpolation_weighted_seconds=float(y.weighted_seconds.sum()),
                          weighted_full_change_percent=float(np.average(y.correction,weights=y.weighted_seconds)),
                          contributing_logs=", ".join(sorted(y.run.unique()))))
pd.DataFrame(node_rows).to_csv(OUT/"node-support.csv",index=False)
source_paths=[ROOT/"DataLogs"/f for f in FILES]+[ROOT/"CurrentTune.msq",ROOT/"projectCfg/mainController.ini"]
metadata={"runs":summaries,"source_hashes":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
          "current_table_axes":{"RPM":rpms.tolist(),"TPS":loads.tolist()},"changes":changes,
          "method":{"nominal_delay_s":.4,"sensitivity_delay_s":[0,.2,.4,.6],
                    "normal_window":"approximately t-0.5 through t+0.6", "launch_guard":"approximately t-1 through t+0.6",
                    "steady_RPM_required":False,"model":"proposed_AFR = measured_delayed_AFR / (proposed_interpolated_VE / saved_interpolated_VE)",
                    "model_status":"Unvalidated prediction assuming same airflow and fuel delivery; not a measurement",
                    "checksums":"No per-record checksum available in text exports", "ECU_or_tune_modified":False}}
(OUT/"validation-and-method.json").write_text(json.dumps(metadata,indent=2))

plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,"axes.grid":True,"grid.alpha":.18})
fig,ax=plt.subplots(3,3,figsize=(16,11),constrained_layout=True)
fig.suptitle("Gear pull VE analysis | delayed AFR, screened samples, and proposal",fontsize=16,weight="bold")
for col,run in enumerate(["1st gear pull","3rd gear pull","4th gear pull"]):
    x=frames[run]; loaded=x[x.TPS>=30]
    lower=max(0,float(loaded.Time.min())-1); upper=min(float(x.Time.max()),float(loaded.Time.max())+1)
    y=x[x.Time.between(lower,upper)]
    ax[0,col].set_title(run)
    ax[0,col].plot(y.Time,y.RPM,color="#286e9e",lw=1.2)
    sec=ax[0,col].twinx();sec.plot(y.Time,y.TPS,color="#be8b24",lw=1,alpha=.7);sec.set_ylabel("TPS (%)")
    ax[0,col].set_ylabel("RPM")
    ax[1,col].plot(y.Time,y["AFR_delay_0.4"],color="#d24e42",lw=1,label="AFR aligned +0.4s")
    ax[1,col].plot(y.Time,y["AFR Target"],color="#283c53",lw=1.3,label="Logged target")
    chosen=y[y.accepted]
    ax[1,col].scatter(chosen.Time,chosen["AFR_delay_0.4"],color="#00836b",s=10,label="Accepted samples",zorder=3)
    if col==2:ax[1,col].plot(y.Time,y.modeled_proposed_AFR,color="#744ca3",ls="--",lw=1,label="Proposal model")
    ax[1,col].set(ylabel="AFR",ylim=(11.5,18));ax[1,col].legend(loc="upper left",fontsize=7)
    ax[2,col].plot(y.Time,y["Accel Enrich"],color="#be8b24",label="Accel enrichment")
    ax[2,col].plot(y.Time,100+y["Hard Launch"]*30,color="#bd3153",label="Launch flag: 100/130")
    ax[2,col].plot(y.Time,100+y["VVT1 Duty"]*.2,color="#666",alpha=.6,label="VVT cmd: 100/120")
    ax[2,col].set(ylabel="Multiplier / status display",xlabel="Elapsed log time (s)")
    ax[2,col].legend(loc="upper left",fontsize=7)
    for a in ax[:,col]:a.set_xlim(lower,upper)
fig.supxlabel("Green points passed screening. Dashed proposal line is a calculation, not a tested AFR result. No tune changed.",fontsize=9)
fig.savefig(OUT/"pull-comparison.png",dpi=150,bbox_inches="tight")
fig.savefig(OUT/"pull-comparison.svg",bbox_inches="tight");plt.close(fig)
print(pd.DataFrame(changes).round(2).to_string(index=False))
print(region_table.round(3).to_string(index=False))
print(json.dumps({k:{s:v[s] for s in ["rows","duration_s","accepted_seconds"]} for k,v in summaries.items()},indent=2))

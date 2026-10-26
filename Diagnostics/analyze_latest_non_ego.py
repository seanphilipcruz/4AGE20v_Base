"""Analyze the 22:47 NON-EGO log; quarantine VE estimates on AFR disagreement."""
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
SOURCE = ROOT / "DataLogs/NON-EGO.msl"
OUT = ROOT / "Diagnostics/NON-EGO-20261024-224734"
OUT.mkdir(exist_ok=True)
lines = SOURCE.read_text().splitlines()
columns = lines[2].split("\t")
assert all(len(line.split("\t")) == len(columns) for line in lines[4:])
x = pd.read_csv(SOURCE, sep="\t", skiprows=[0,1,3])
for column in x.select_dtypes(exclude="number"):
    assert x[column].isin(["On","Off"]).all()
    x[column] = x[column].map({"On":1,"Off":0})
assert not x.isna().any().any()
x["Time_original"] = x.Time
backsteps = x.loc[x.Time.diff() < 0, ["Time"]].to_dict("records")
assert x.Time.diff().min() >= -.00100001
x.Time = np.maximum.accumulate(x.Time)
x["dt"] = (x.Time.shift(-1)-x.Time).fillna(0)
t = x.Time.to_numpy()
tune_bytes = (ROOT/"CurrentTune.msq").read_bytes()
root = ET.fromstring(tune_bytes)
constants = {e.attrib["name"]:(e.text or "").strip() for e in root.iter() if e.tag.endswith("constant")}
tune_write_date = next(e.attrib.get("writeDate") for e in root.iter() if e.tag.endswith("bibliography"))
rpms = np.array(constants["rpmBins"].split(),float)
loads = np.array(constants["fuelLoadBins"].split(),float)
table = np.array(constants["veTable"].split(),float).reshape(len(loads),len(rpms))
ix = np.clip(np.searchsorted(rpms,x.RPM,side="right")-1,0,len(rpms)-2)
iy = np.clip(np.searchsorted(loads,x.TPS,side="right")-1,0,len(loads)-2)
fx = np.clip((x.RPM.to_numpy()-rpms[ix])/(rpms[ix+1]-rpms[ix]),0,1)
fy = np.clip((x.TPS.to_numpy()-loads[iy])/(loads[iy+1]-loads[iy]),0,1)
x["saved_interpolated_VE"] = (table[iy,ix]*(1-fx)*(1-fy)+table[iy,ix+1]*fx*(1-fy)+
                               table[iy+1,ix]*(1-fx)*fy+table[iy+1,ix+1]*fx*fy)
x["saved_VE_residual"] = x.VE1-x.saved_interpolated_VE
assert (x.Gego == 100).all() and np.allclose(x.TPS,x.FuelLoad)
continuous = x.drop_duplicates("Time",keep="last")
for delay in [0,.2,.4,.6]:
    x[f"AFR_delay_{delay}"] = np.interp(t+delay,continuous.Time,continuous.AFR)
    x[f"ratio_{delay}"] = x[f"AFR_delay_{delay}"]/x["AFR Target"]
channels = ["TPS","MAP","RPM","VE1","Accel Enrich","Gammae","DFCO","Hard Launch","Hard Limiter","PW","VVT1 Duty","Sync status","AFR"]
roll = x[channels].copy(); roll.index = pd.to_timedelta(t,unit="s")
hi = roll.rolling("2.6s").max(); lo = roll.rolling("2.6s").min()
future = np.minimum(np.searchsorted(t,t+.6),len(x)-1)
high = lambda c:hi[c].to_numpy()[future]
low = lambda c:lo[c].to_numpy()[future]
conditions = {
    "complete_window": (t>=2)&(t+.6<=t[-1]),
    "no_enrichment": (high("Accel Enrich")==100)&(high("Gammae")==100)&(low("Gammae")==100),
    "no_cuts": (high("DFCO")==0)&(high("Hard Launch")==0)&(high("Hard Limiter")==0)&(low("PW")>0),
    "steady_TPS": high("TPS")-low("TPS")<=.5,
    "steady_MAP": high("MAP")-low("MAP")<=3,
    "steady_RPM": high("RPM")-low("RPM")<=150,
    "constant_VE": high("VE1")==low("VE1"),
    "constant_VVT": high("VVT1 Duty")==low("VVT1 Duty"),
    "sync": (high("Sync status")==2)&(low("Sync status")==2),
    "not_closed_throttle_overrun": (x.TPS>1)|(x.RPM<=1300),
}
x["physically_settled"] = np.logical_and.reduce([np.asarray(v) for v in conditions.values()])
x["AFR_window_span"] = high("AFR")-low("AFR")
x["AFR_stability_screen"] = (x.AFR_window_span<=1)&x["AFR_delay_0.4"].between(9,17.4)
x["passes_both_screens"] = x.physically_settled & x.AFR_stability_screen
# Owner reports steady gauge versus fluctuating TunerStudio: no region is released for tuning.
x["released_for_VE_changes"] = False
x["rpm_band"] = (x.RPM//500*500).astype(int)
x["TPS_band"] = pd.cut(x.TPS,[-.01,0,1,5,10,15,20,30,50,80,100])
regions = []
for (rpm,band), y in x[x.physically_settled].groupby(["rpm_band","TPS_band"],observed=True):
    if y.dt.sum()<1: continue
    avg = lambda c:float(np.average(y[c],weights=y.dt))
    regions.append(dict(RPM_lower=int(rpm),RPM_upper_exclusive=int(rpm+500),TPS_band=str(band),
                        seconds=float(y.dt.sum()),mean_AFR=avg("AFR_delay_0.4"),mean_target=avg("AFR Target"),
                        logged_VE_min=int(y.VE1.min()),logged_VE_max=int(y.VE1.max()),
                        nominal_math_change_percent=100*(avg("ratio_0.4")-1),
                        AFR_raw_p05=float(y.AFR.quantile(.05)),AFR_raw_median=float(y.AFR.median()),AFR_raw_p95=float(y.AFR.quantile(.95)),
                        median_AFR_window_span=float(y.AFR_window_span.median()),
                        disposition="DO NOT APPLY: owner reports steady gauge but fluctuating ECU AFR"))
pd.DataFrame(regions).to_csv(OUT/"quarantined-regional-calculations.csv",index=False)
x.to_csv(OUT/"NON-EGO-screened.csv",index=False)
rapid = (x.AFR.diff().abs()>2)&x.Time.diff().between(.02,.15)
sample = x[x.Time.between(86.53,99.07)]
summary = dict(source=str(SOURCE),header=lines[:2],rows=len(x),columns=len(columns),duration_s=float(x.dt.sum()),
               timing_backsteps=backsteps,timing_repair="Clamp the one 0.001-second initial backstep to previous timestamp; original times preserved",
               duplicate_times=int(x.Time.duplicated().sum()),max_gap_s=float(x.dt.max()),
               physically_settled_seconds=float(x.loc[x.physically_settled,"dt"].sum()),
               both_screens_seconds=float(x.loc[x.passes_both_screens,"dt"].sum()),released_seconds=0,
               rapid_AFR_changes_over_2=int(rapid.sum()),Gego_values=x.Gego.unique().tolist(),
               sync_loss_start=int(x["Sync Loss #"].iloc[0]),sync_loss_end=int(x["Sync Loss #"].iloc[-1]),
               saved_table_within_1_1_points_percent=float((x.saved_VE_residual.abs()<=1.1).mean()*100),
               range={k:[float(x[k].min()),float(x[k].max())] for k in ["RPM","TPS","MAP","AFR","AFR Target","CLT","IAT","Battery V","IAC value"]},
               representative_window={k:[float(sample[k].min()),float(sample[k].max())] for k in ["Time","RPM","MAP","VE1","PW","AFR","AFR Target"]},
               owner_report="AEM gauge steady; TunerStudio fluctuated", disposition="Hold all VE changes pending signal-path verification",
               analyzed_tune_write_date=tune_write_date,
               analyzed_VE_axes={"RPM":rpms.tolist(),"TPS":loads.tolist()},analyzed_VE_table=table.tolist(),
               saved_context={k:constants.get(k) for k in ["algorithm","multiplyMAP","includeAFR","incorporateAFR","egoAlgorithm","egoType","ADCFILTER_O2","reqFuel","injOpen","launchEnable"]},
               source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [SOURCE,ROOT/"CurrentTune.msq",ROOT/"projectCfg/mainController.ini",ROOT/"std_ms2geno2.log"]})
summary["source_hashes"]["CurrentTune.msq"] = hashlib.sha256(tune_bytes).hexdigest()
(OUT/"validation-and-method.json").write_text(json.dumps(summary,indent=2))
plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,"axes.grid":True,"grid.alpha":.18})
fig,ax = plt.subplots(4,1,figsize=(13,10),sharex=True,constrained_layout=True)
fig.suptitle("NON-EGO | 24 Oct 2026, 22:47 PHT | VE changes held",fontsize=17,weight="bold")
ax[0].plot(x.Time,x.RPM,color="#286e9e",lw=.8);ax[0].set_ylabel("RPM")
ax[1].plot(x.Time,x.AFR,color="#cf4b40",lw=.65,label="Logged AFR")
ax[1].plot(x.Time,x["AFR Target"],color="#263e55",lw=1.2,label="Target")
ax[1].set_ylabel("AFR");ax[1].legend(loc="lower right",ncol=2)
ax[2].plot(x.Time,x.VE1,color="#008773",lw=.8);ax[2].set_ylabel("Logged VE")
ax[3].plot(x.Time,x["AFR_window_span"],color="#7646a7",lw=.8)
ax[3].axhline(1,color="gray",ls="--",label="Custom signal stability screen")
ax[3].set(ylabel="AFR span in 2.6s window",xlabel="Elapsed log time (s)");ax[3].legend(loc="upper right")
for a in ax:a.set_xlim(0,x.Time.max());a.axvspan(86.53,99.07,color="#e1b01b",alpha=.15)
fig.savefig(OUT/"overview.png",dpi=150,bbox_inches="tight");fig.savefig(OUT/"overview.svg",bbox_inches="tight");plt.close(fig)
fig,ax = plt.subplots(3,1,figsize=(12,8),sharex=True,constrained_layout=True)
fig.suptitle("Stable engine conditions, unstable ECU AFR | AEM gauge reported steady",fontsize=15,weight="bold")
ax[0].plot(sample.Time,sample.RPM,color="#286e9e");ax[0].set_ylabel("RPM")
ax[1].plot(sample.Time,sample.AFR,color="#cf4b40",label="Logged AFR")
ax[1].plot(sample.Time,sample["AFR Target"],color="#263e55",label="Target")
ax[1].set_ylabel("AFR");ax[1].legend(loc="upper right",ncol=2)
ax[2].plot(sample.Time,sample.PW,color="#008773",label="Injector pulse")
ax[2].set(ylabel="Pulse width (ms)",xlabel="Elapsed time (s)")
sec=ax[2].twinx();sec.plot(sample.Time,sample.MAP,color="#b27b16",ls="--",alpha=.7);sec.set_ylabel("MAP (kPa)")
for a in ax:a.set_xlim(sample.Time.min(),sample.Time.max())
fig.savefig(OUT/"AFR-disagreement-evidence.png",dpi=150,bbox_inches="tight")
fig.savefig(OUT/"AFR-disagreement-evidence.svg",bbox_inches="tight");plt.close(fig)
print(json.dumps({k:summary[k] for k in ["rows","duration_s","physically_settled_seconds","both_screens_seconds","rapid_AFR_changes_over_2","saved_table_within_1_1_points_percent"]},indent=2))
print(pd.DataFrame(regions).round(3).to_string(index=False))

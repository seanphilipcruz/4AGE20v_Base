import pathlib
import pandas as pd
root=pathlib.Path(__file__).resolve().parent
cols=['Time','RPM','TPS','IAC value','Idle Target RPM','MAP','PW','Advance _Current','Battery V','DFCO','Sync Loss #','Sync status','CLT']
for path in sorted(root.glob('*.csv')):
 d=pd.read_csv(path); print('\nFILE',path.name)
 print('Missing time rows',d.Time.isna().sum())
 d=d.dropna(subset=['Time']).reset_index(drop=True)
 idle=d[(d.TPS<1)&(d.RPM.between(500,1500))]
 print('idle stats',idle[cols].quantile([0,.1,.5,.9,1]).round(2).to_string())
 print('idle saturated %',100*(idle['IAC value']>=99).mean())
 stops=d.index[(d.RPM==0)&(d.RPM.shift()>0)]
 print('STOP EVENTS',len(stops))
 for i in stops:
  t=d.Time[i]; s=d[(d.Time>=t-4)&(d.Time<=t+1)]
  print('STOP',t); print(s[cols].iloc[::max(1,len(s)//10)].round(2).to_string(index=False))
 sync=d.index[(d['Sync Loss #'].diff()>0)]
 print('SYNC INCREASES',len(sync)); print(d.loc[sync,cols].round(2).to_string(index=False))
 print('10 SEC CLOSED THROTTLE WINDOWS')
 for k,s in d.groupby((d.Time//10).astype(int)):
  if len(s)>50 and s.TPS.max()<1 and s.RPM.min()>500 and s.RPM.max()<1700 and s.RPM.max()-s.RPM.min()>400:
   print(k*10, 'rpm',s.RPM.min(),s.RPM.max(),'iac',s['IAC value'].min(),s['IAC value'].max(),'volt',s['Battery V'].min(),s['Battery V'].max(),'pw',round(s.PW.min(),2),round(s.PW.max(),2))

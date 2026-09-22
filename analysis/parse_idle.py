import struct, pathlib, json
import pandas as pd

root = pathlib.Path(__file__).resolve().parents[1]
for path in sorted((root/'DataLogs').glob('*.mlg')):
    b = path.read_bytes()
    version, stamp, info, start, length, count = struct.unpack_from('>HIIIHH', b, 6)
    assert b[:6] == b'MLVLG\0' and version == 2
    fields=[]; offset=0
    types={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'q',7:'f',16:'B',17:'H',18:'I'}
    for i in range(count):
        p=24+i*89; typ=b[p]; name=b[p+1:p+35].split(b'\0')[0].decode()
        fmt='>'+types[typ]; size=struct.calcsize(fmt)
        scale,shift=struct.unpack_from('>ff',b,p+46) if typ<8 else (1,0)
        fields.append((name,fmt,offset,scale,shift)); offset+=size
    assert offset==length
    rows=[]; markers=[]; p=start; bad=0
    while p<len(b):
        if b[p]==1:
            markers.append(b[p+4:p+54].split(b'\0')[0].decode(errors='replace'));p+=54;continue
        assert b[p]==0,(p,b[p])
        assert p+length+5<=len(b)
        payload=b[p+4:p+4+length]
        if sum(payload)%256!=b[p+4+length]:bad+=1
        rows.append([(struct.unpack_from(fmt,payload,off)[0]+shift)*scale for name,fmt,off,scale,shift in fields])
        p+=length+5
    df=pd.DataFrame(rows,columns=[f[0] for f in fields])
    df.to_csv(root/'analysis'/f'{path.stem}.csv',index=False)
    print(path.name, 'records',len(df),'bad checksums',bad,'markers',markers,'info',b[info:start].decode(errors='replace'))
    print(df.agg(['min','max']).T.to_string())

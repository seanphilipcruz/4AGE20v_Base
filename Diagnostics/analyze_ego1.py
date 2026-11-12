"""Decode EGO-1 using EFI Analytics MLVLG v2 and retain validation evidence."""
from pathlib import Path
import csv
import datetime
import hashlib
import json
import struct

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Diagnostics" / "EGO-1"
OUT.mkdir(exist_ok=True)
source = ROOT / "DataLogs" / "EGO-1.mlg"
blob = source.read_bytes()
assert blob[:6] == b"MLVLG\0"
version, stamp, info, start, length, count = struct.unpack_from(">HIIIHH", blob, 6)
assert version == 2
types = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "q", 7: "f",
         16: "B", 17: "H", 18: "I"}
fields = []
offset = 0
for index in range(count):
    block = blob[24 + index * 89:24 + (index + 1) * 89]
    kind = block[0]
    name = block[1:35].split(b"\0")[0].decode("ascii")
    units = block[35:45].split(b"\0")[0].decode("ascii")
    scale, shift = struct.unpack_from(">ff", block, 46) if kind < 16 else (1, 0)
    field = dict(name=name or "Protection bits", units=units, kind=kind,
                 format=types[kind], offset=offset, scale=scale, shift=shift)
    if kind >= 16:
        pointer = struct.unpack_from(">I", block, 47)[0]
        field["bits"] = blob[pointer:].split(b"\0")[:block[51]]
        field["bits"] = [item.decode("ascii") for item in field["bits"]]
    fields.append(field)
    offset += struct.calcsize(">" + types[kind])
assert offset == length
rows, markers, errors = [], [], []
pos = start
previous_counter = None
while pos < len(blob):
    kind, counter, tick = struct.unpack_from(">BBH", blob, pos)
    if previous_counter is not None and counter != (previous_counter + 1) % 256:
        errors.append(dict(offset=pos, reason="counter", previous=previous_counter, counter=counter))
    previous_counter = counter
    if kind == 0:
        data = blob[pos + 4:pos + 4 + length]
        assert len(data) == length and pos + 4 + length < len(blob)
        checksum = blob[pos + 4 + length]
        assert sum(data) % 256 == checksum, f"Invalid checksum at {pos}"
        row = {}
        for field in fields:
            raw = struct.unpack_from(">" + field["format"], data, field["offset"])[0]
            row[field["name"]] = (raw + field["shift"]) * field["scale"]
            for bit, label in enumerate(field.get("bits", [])):
                row[label] = (raw >> bit) & 1
        rows.append(row)
        pos += length + 5
    elif kind == 1:
        markers.append(dict(offset=pos, tick=tick, after_row=len(rows),
                            message=blob[pos + 4:pos + 54].split(b"\0")[0].decode("ascii")))
        pos += 54
    else:
        raise ValueError(f"Unexpected block type {kind} at {pos}")
assert pos == len(blob)
with (OUT / "EGO-1-decoded.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
metadata = dict(source=str(source), sha256=hashlib.sha256(blob).hexdigest(),
                version=version, timestamp_utc=datetime.datetime.fromtimestamp(
                    stamp, datetime.timezone.utc).isoformat(),
                info=blob[info:start].decode("ascii").strip("\0"),
                records=len(rows), fields=fields, markers=markers,
                counter_errors=errors, checksums_passed=len(rows), bytes_consumed=pos)
(OUT / "decode-validation.json").write_text(json.dumps(metadata, indent=2))
print(json.dumps({key: metadata[key] for key in ["records", "timestamp_utc", "markers", "counter_errors", "checksums_passed"]}, indent=2))
for name in rows[0]:
    values = [row[name] for row in rows]
    print(f"{name}: first={values[0]:.4f} last={values[-1]:.4f} min={min(values):.4f} max={max(values):.4f}")

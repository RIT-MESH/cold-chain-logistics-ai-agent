"""Export the synthetic cold-chain dataset into browser-readable format for
the Hugging Face Static Space offline assistant (Phase 8 directive).

Produces hf-space/dataset.js containing:
- ALL shipments (compact arrays)
- Per-shipment telemetry summaries (min/max/avg, excursion counts)
- All alerts (compact)
- All custody handoffs (compact)
- 50 shipments with detailed telemetry for chart rendering

Input: data/unified/*.csv (the canonical synthetic dataset)
Output: hf-space/dataset.js (single file, ~600KB)
"""
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "unified"
OUT = ROOT / "hf-space" / "dataset.js"


def read_csv(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    shipments = read_csv("shipments.csv")
    telemetry = read_csv("telemetry.csv")
    alerts = read_csv("alerts.csv")
    custody = read_csv("custody_handoffs.csv")

    print(f"Loaded: {len(shipments)} shipments, {len(telemetry)} telemetry, "
          f"{len(alerts)} alerts, {len(custody)} custody")

    # Index shipments
    sid_to_idx = {}
    ship_arr = []
    for s in shipments:
        sid = s["shipment_id"]
        sid_to_idx[sid] = len(ship_arr)
        ship_arr.append([
            sid,
            s.get("cargo_type", ""),
            s.get("vehicle_id", ""),
            s.get("departed_at", "")[:16],
            s.get("arrived_at", "")[:16],
            s.get("receiver_verdict", ""),
            float(s.get("temperature_min_c", 0)),
            float(s.get("temperature_max_c", 0)),
            int(s.get("pallets", 0)),
        ])

    # Telemetry summaries per shipment
    tel_by_sid = defaultdict(list)
    for t in telemetry:
        tel_by_sid[t["shipment_id"]].append(t)

    tel_summary = {}
    for sid, rows in tel_by_sid.items():
        temps = [float(r["temperature_c"]) for r in rows if r.get("temperature_c")]
        hums = [float(r["humidity_pct"]) for r in rows if r.get("humidity_pct")]
        if not temps:
            continue
        ship = shipments[sid_to_idx[sid]] if sid in sid_to_idx else None
        tmin = float(ship["temperature_min_c"]) if ship and ship.get("temperature_min_c") else 0
        tmax = float(ship["temperature_max_c"]) if ship and ship.get("temperature_max_c") else 0
        excursions = sum(1 for t in temps if t < tmin or t > tmax)
        tel_summary[sid] = [
            round(min(temps), 2), round(max(temps), 2), round(sum(temps)/len(temps), 2),
            round(min(hums), 1) if hums else None, round(max(hums), 1) if hums else None,
            round(sum(hums)/len(hums), 1) if hums else None,
            len(temps), excursions,
        ]

    # Detailed telemetry for first 50 shipments with telemetry
    detailed_sids = list(tel_by_sid.keys())[:50]
    detailed = {}
    for sid in detailed_sids:
        rows = tel_by_sid[sid]
        detailed[sid] = [
            [r["recorded_at"][:16], float(r["temperature_c"]), float(r["humidity_pct"]),
             float(r.get("latitude", 0) or 0), float(r.get("longitude", 0) or 0),
             float(r.get("speed_kmh", 0) or 0), float(r.get("fuel_level_pct", 0) or 0),
             r.get("risk_level", "")]
            for r in rows[:20]  # cap at 20 readings per shipment
        ]

    # Alerts (compact)
    alert_arr = defaultdict(list)
    for a in alerts:
        alert_arr[a["shipment_id"]].append([
            a.get("alert_at", "")[:16],
            a.get("alert_type", ""),
            a.get("severity", ""),
            a.get("message", "")[:120],
        ])

    # Custody (compact)
    cust_arr = defaultdict(list)
    for c in custody:
        cust_arr[c["shipment_id"]].append([
            int(c.get("handoff_no", 0)),
            c.get("received_by", ""),
            c.get("handed_at", "")[:16],
            c.get("seal_intact", ""),
        ])

    # SOP content (from the synthetic SOP documents)
    sops = []
    sop_dir = ROOT / "data" / "policy"
    for f in sorted(sop_dir.glob("SYNTHETIC_SOP_*.md")):
        text = f.read_text(encoding="utf-8")
        # extract header info
        lines = text.split("\n")
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("## ")), f.stem)
        category = ""
        version = ""
        for l in lines:
            if "**Category:**" in l: category = l.split("**Category:**")[1].split("|")[0].strip()
            if "**Version:**" in l: version = l.split("**Version:**")[1].split("|")[0].strip()
        # extract sections
        sections = []
        current = None
        for l in lines:
            if l.startswith("### "):
                if current: sections.append(current)
                current = {"id": l.lstrip("# ").strip()[:40], "title": l.lstrip("# ").strip(), "body": []}
            elif current:
                current["body"].append(l)
        if current: sections.append(current)
        for s in sections:
            s["body"] = "\n".join(s["body"]).strip()[:500]
        sops.append({"file": f.name, "title": title, "category": category,
                      "version": version, "sections": sections})

    # Write output
    out = f"const CC_DATA={{\n"
    out += f"meta:{{shipments:{len(ship_arr)},telemetry:{len(telemetry)},"
    out += f"alerts:{len(alerts)},custody:{len(custody)},sops:{len(sops)},"
    out += f"detailed_shipments:{len(detailed_sids)}}},\n"

    out += "shipments:" + json.dumps(ship_arr, separators=(",", ":")) + ",\n"
    out += "telSummary:" + json.dumps(tel_summary, separators=(",", ":")) + ",\n"
    out += "detailed:" + json.dumps(detailed, separators=(",", ":")) + ",\n"
    out += "alerts:" + json.dumps(dict(alert_arr), separators=(",", ":")) + ",\n"
    out += "custody:" + json.dumps(dict(cust_arr), separators=(",", ":")) + ",\n"
    out += "sops:" + json.dumps(sops, separators=(",", ":")) + "\n};\n"

    OUT.write_text(out, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"Written {OUT} ({size_kb:.0f} KB)")
    print(f"  shipments: {len(ship_arr)}")
    print(f"  telemetry summaries: {len(tel_summary)}")
    print(f"  detailed telemetry: {len(detailed)} shipments × ≤20 readings")
    print(f"  alerts: {sum(len(v) for v in alert_arr.values())} across {len(alert_arr)} shipments")
    print(f"  custody: {sum(len(v) for v in cust_arr.values())} across {len(cust_arr)} shipments")
    print(f"  SOP sections: {sum(len(s['sections']) for s in sops)} across {len(sops)} documents")


if __name__ == "__main__":
    main()

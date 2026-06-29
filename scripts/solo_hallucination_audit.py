"""Final v3: correct threshold. Hallucination = no canonical within 0.5x–2x
(OR the closest is in either direction outside that band)."""
import json
import re
import sqlite3
import sys
import yfinance as yf

import json
import re
import sqlite3
import sys
from pathlib import Path

import yfinance as yf

sys.path.insert(0, '/tmp')
try:
    from solo_accuracy_audit import load_solo_memos, load_all_evidence, extract_numbers
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).parent))
    from solo_accuracy_audit import load_solo_memos, load_all_evidence, extract_numbers


def get_yf(ticker):
    try:
        info = yf.Ticker(ticker).info
        return {k: float(v) for k, v in info.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    except Exception:
        return {}


def get_evidence_canon(evidence):
    canon = {}
    for ev in evidence:
        eid = ev.get("_evidence_id", "ev")
        d = ev.get("data", ev)
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                canon[f"{eid}:{k}"] = float(v)
            elif isinstance(v, str):
                cleaned = re.sub(r"[\$,]", "", v)
                m = re.match(r"^(-?\d+\.?\d*)\s*(%|[xX]|[mMbB])?$", cleaned)
                if m:
                    val = float(m.group(1))
                    suffix = m.group(2)
                    if suffix == "%":
                        val /= 100
                    elif suffix and suffix.lower() == "m":
                        val *= 1e6
                    elif suffix and suffix.lower() == "b":
                        val *= 1e9
                    canon[f"{eid}:{k}"] = val
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for k2, v2 in item.items():
                            if isinstance(v2, (int, float)) and not isinstance(v2, bool):
                                canon[f"{eid}:{k}.{k2}"] = float(v2)
    return canon


def find_hallucinations(memo, yf_canon, ev_canon):
    text = " ".join([
        str(memo.get("verdict_line", "")),
        str(memo.get("thesis", "")),
        str(memo.get("antithesis", "")),
        " ".join(str(x) for x in memo.get("key_evidence", []) if isinstance(x, str)),
    ])
    claims = extract_numbers(text)
    skip_indicators = ["would", "stable FCF", "needs", "requires", "at maturity",
                       "scenario", "implying", "implies", "guidance",
                       "implied", "would need", "10x", "15-25%", "10-15pp",
                       "stable ", "FCF yield at", "to reach", "at 35%",
                       "would take", "multi-year", "to justify", "accelerating"]
    combined = {**yf_canon, **ev_canon}
    hallucinations = []
    for val, ctx in claims:
        n_val = re.sub(r"[^\d.\-]", "", val)
        if not n_val:
            continue
        try:
            n = float(n_val)
        except ValueError:
            continue
        if n.is_integer() and 1900 <= n <= 2100:
            continue
        if any(ind in ctx for ind in skip_indicators):
            continue
        # Find closest canon
        closest = None
        for k, v in combined.items():
            if v == 0 or n == 0:
                continue
            ratio = abs(v / n)
            if closest is None or abs(ratio - 1) < abs(closest[2] - 1):
                closest = (k, v, ratio)
        if closest is None:
            continue
        # Hallucination if closest is more than 2x off (in EITHER direction)
        if closest[2] < 0.5 or closest[2] > 2.0:
            hallucinations.append({
                'claim': val,
                'numeric': n,
                'context': ctx,
                'closest_key': closest[0],
                'closest_val': closest[1],
                'closest_ratio': closest[2],
            })
    return hallucinations


def main():
    memos = load_solo_memos()
    print(f"=== FINAL v3 HALLUCINATION AUDIT (no threshold bug) ===\n")
    total_h = 0
    confirmed_h = []
    for entry in memos:
        t = entry['ticker']
        cid = entry['cycle_id']
        yf_c = get_yf(t)
        ev = load_all_evidence(t, cid)
        ev_c = get_evidence_canon(ev)
        h = find_hallucinations(entry['memo'], yf_c, ev_c)
        if not h:
            print(f"✅ {t:6s} grade={entry['grade']} score={entry['score']} — no hallucinations")
            continue
        print(f"\n❌ {t:6s} grade={entry['grade']} score={entry['score']} — {len(h)} potential hallucinations:")
        seen_claims = set()
        for x in h:
            if x['claim'] in seen_claims:
                continue
            seen_claims.add(x['claim'])
            print(f"   claim='{x['claim']}' n={x['numeric']:.2f} | closest: {x['closest_key']}={x['closest_val']:.2f} (ratio={x['closest_ratio']:.2f}x)")
            print(f"   context: ...{x['context'][-100:]}...")
            confirmed_h.append({'ticker': t, 'cycle_id': cid, **x})
        total_h += len(seen_claims)
    print(f"\n=== TOTAL: {total_h} hallucination candidates ===")
    if confirmed_h:
        print("\n--- DETAIL ---")
        for h in confirmed_h:
            print(f"  {h['ticker']}: claim='{h['claim']}' | closest={h['closest_key']}={h['closest_val']:.2f} ({h['closest_ratio']:.2f}x) | context: {h['context'][-100:]}")

if __name__ == "__main__":
    main()

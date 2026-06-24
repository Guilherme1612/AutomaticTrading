"""SOLO cycle accuracy audit: load SOLO memo, extract numeric claims, cross-check
against ALL evidence packets in evidence_cache for that cycle.

Usage:
    uv run python /tmp/solo_accuracy_audit.py              # all SOLO memos
    uv run python /tmp/solo_accuracy_audit.py OUST          # one ticker
    uv run python /tmp/solo_accuracy_audit.py --verbose    # show all claims
"""
import json
import re
import sqlite3
import sys

DB = "file:data/pmacs.db?mode=ro"


def load_solo_memos(ticker: str | None = None) -> list[dict]:
    db = sqlite3.connect(DB, uri=True)
    db.row_factory = sqlite3.Row
    if ticker:
        rows = db.execute(
            "SELECT * FROM memos WHERE cycle_id LIKE 'SOLO-%' AND ticker=? "
            "ORDER BY decided_at DESC",
            (ticker,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM memos WHERE cycle_id LIKE 'SOLO-%' "
            "ORDER BY decided_at DESC",
        ).fetchall()
    out = []
    for r in rows:
        try:
            mj = json.loads(r["memo_json"])
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "ticker": r["ticker"],
                "cycle_id": r["cycle_id"],
                "memo": mj,
                "grade": r["memo_grade"],
                "score": r["memo_score"],
            }
        )
    return out


def load_all_evidence(ticker: str, cycle_id: str) -> list[dict]:
    db = sqlite3.connect(DB, uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT evidence_id, evidence_json FROM evidence_cache WHERE ticker=? AND cycle_id=?",
        (ticker, cycle_id),
    ).fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["evidence_json"])
            data["_evidence_id"] = r["evidence_id"]
            out.append(data)
        except json.JSONDecodeError:
            continue
    return out


def extract_numbers(text: str) -> list[tuple[str, str]]:
    out = []
    pattern = re.compile(
        r"([\$]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|x|X|M|B|K|m|b|k)?)"
    )
    for m in pattern.finditer(text):
        val = m.group(1).strip()
        if not re.search(r"\d", val):
            continue
        start = max(0, m.start() - 50)
        end = min(len(text), m.end() + 30)
        ctx = text[start:end].replace("\n", " ").strip()
        out.append((val, ctx))
    return out


def collect_canon(d: dict) -> dict[str, float]:
    if not d:
        return {}
    result = {}
    eid = d.get("_evidence_id", "ev")
    prefix = f"{eid}:"
    data = d.get("data", d) if isinstance(d, dict) else {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            result[f"{prefix}{k}"] = float(v)
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
                result[f"{prefix}{k}"] = val
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for k2, v2 in item.items():
                        if isinstance(v2, (int, float)) and not isinstance(v2, bool):
                            result[f"{prefix}{k}.{k2}"] = float(v2)
    return result


def cross_check(ticker: str, cycle_id: str, memo: dict, all_evidence: list[dict], verbose: bool = False) -> dict:
    text = " ".join(
        [
            str(memo.get("verdict_line", "")),
            str(memo.get("thesis", "")),
            str(memo.get("antithesis", "")),
            " ".join(str(x) for x in memo.get("key_evidence", []) if isinstance(x, str)),
        ]
    )
    claims = extract_numbers(text)

    canon: dict[str, float] = {}
    for ev in all_evidence:
        canon.update(collect_canon(ev))

    matched = []
    suspicious = []

    for val, ctx in claims:
        n_val = re.sub(r"[^\d.\-]", "", val)
        if not n_val:
            continue
        try:
            n = float(n_val)
        except ValueError:
            continue
        # Skip pure year-like numbers (1900-2100) unless they actually match a canon field
        if n.is_integer() and 1900 <= n <= 2100:
            # Could be a year. Try matching canon first.
            year_hit = any(
                (1900 <= v <= 2100 and abs(v - n) < 1.0) for v in canon.values()
            )
            if not year_hit:
                continue

        hit = None
        hit_val = None
        for k, v in canon.items():
            if abs(v - n) < 0.05:
                hit = k
                hit_val = v
                break
            # Decimal percent storage (0.489) vs claim (48.9)
            if abs(v) < 2.0 and abs(v * 100 - n) < 0.5:
                hit = k
                hit_val = v
                break
            # Large value vs M-suffix: canon 194400000 vs claim 194M
            if abs(v) > 1e5 and abs(v / 1e6 - n) < 0.5:
                hit = k
                hit_val = v
                break
            # K-suffix
            if abs(v) > 1e3 and abs(v / 1e3 - n) < 0.5:
                hit = k
                hit_val = v
                break
            # x-suffix: canon 15.51 vs claim 15.5x
            if abs(v) < 1000 and abs(v - n) < 0.5:
                hit = k
                hit_val = v
                break
            # Sign-flip: canon -25.26 (raw, bad unit) vs claim 25.3 (signed %)
            if abs(abs(v) - n) < 0.5 and (v != 0) and (n != 0):
                hit = k
                hit_val = v
                break

        if hit:
            matched.append((val, hit, hit_val, ctx))
        else:
            suspicious.append((val, ctx))

    return {
        "ticker": ticker,
        "cycle_id": cycle_id,
        "claim_count": len(claims),
        "matched_count": len(matched),
        "unmatched_count": len(suspicious),
        "matched": matched,
        "suspicious": suspicious,
        "canon_size": len(canon),
    }


def main():
    verbose = "--verbose" in sys.argv
    ticker = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            ticker = a.upper()

    memos = load_solo_memos(ticker)
    if not memos:
        print(f"No SOLO memos found{' for ' + ticker if ticker else ''}.")
        return

    print(f"=== SOLO ACCURACY AUDIT === ({len(memos)} memos)\n")
    total_claims = 0
    total_matched = 0
    total_suspicious = 0

    for entry in memos:
        t = entry["ticker"]
        cid = entry["cycle_id"]
        memo = entry["memo"]
        all_ev = load_all_evidence(t, cid)
        if not all_ev:
            print(f"--- {t} ({cid}) — NO evidence in cache")
            continue
        result = cross_check(t, cid, memo, all_ev, verbose=verbose)
        total_claims += result["claim_count"]
        total_matched += result["matched_count"]
        total_suspicious += result["unmatched_count"]

        if result["claim_count"]:
            match_rate = f"{result['matched_count']}/{result['claim_count']} = {100 * result['matched_count'] / result['claim_count']:.0f}%"
        else:
            match_rate = "n/a"
        print(
            f"--- {t} ({cid}) --- grade={entry['grade']} score={entry['score']} "
            f"canon_size={result['canon_size']} claims={match_rate}"
        )
        if verbose:
            for val, hit, hit_val, ctx in result["matched"]:
                print(f"  ✅ {val:>10} → {hit}={hit_val}  | {ctx[-70:]}")
            for val, ctx in result["suspicious"]:
                print(f"  ⚠️ {val:>10} (unmatched)  | {ctx[-70:]}")
        print()

    print("=== TOTALS ===")
    if total_claims:
        print(f"  total numeric claims extracted: {total_claims}")
        print(f"  matched to evidence:           {total_matched} ({100 * total_matched / total_claims:.0f}%)")
        print(f"  unmatched (potentially wrong): {total_suspicious} ({100 * total_suspicious / total_claims:.0f}%)")
    else:
        print("  no claims extracted")


if __name__ == "__main__":
    main()

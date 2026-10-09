import requests
import time
import csv
import json
import webbrowser
import threading
from collections import Counter

BASE_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/mj5m-pzi6/0"
PAGE_SIZE = 500
SLEEP_BETWEEN_PAGES = 0.05

OUTPUT_CSV = "providers_filtered.csv"
UNIQUE_CSV = "providers_unique.csv"
OUTPUT_JSON = "providers_data.json"

# ============================================================
# Set your filters here - accepts strings or lists for multi-select
# e.g., "state": ["MA", "NY"], "pri_spec": ["CARDIOLOGY", "DERMATOLOGY"]
# ============================================================
FETCH_FILTERS = {
    # "pri_spec": ["PULMONARY DISEASE"],
    # "citytown": "CHELSEA",
    # "state": "MA",
}

OPERATOR = "contains"

KEEP = [
    "npi", "provider_first_name", "provider_last_name", "cred",
    "pri_spec", "sec_spec_all", "facility_name",
    "adr_ln_1", "citytown", "state", "zip_code",
    "telephone_number", "location_count",
]

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def build_params(offset, filters, operator, limit=PAGE_SIZE):
    params = [
        ("limit", str(limit)),
        ("offset", str(offset)),
        ("count", "true"),
    ]
    cond_idx = 0
    for prop, val in filters.items():
        if isinstance(val, (list, tuple, set)):
            val_list = [str(v).strip().upper() for v in val if str(v).strip()]
            if not val_list:
                continue
            if len(val_list) == 1:
                op = "=" if prop in ("state", "zip_code", "npi", "telehlth") else operator
                params.append((f"conditions[{cond_idx}][property]", prop))
                params.append((f"conditions[{cond_idx}][value]", val_list[0]))
                params.append((f"conditions[{cond_idx}][operator]", op))
            else:
                params.append((f"conditions[{cond_idx}][property]", prop))
                for v in val_list:
                    params.append((f"conditions[{cond_idx}][value][]", v))
                params.append((f"conditions[{cond_idx}][operator]", "in"))
            cond_idx += 1
        elif val:
            v_str = str(val).strip().upper()
            op = "=" if prop in ("state", "zip_code", "npi", "telehlth") else operator
            params.append((f"conditions[{cond_idx}][property]", prop))
            params.append((f"conditions[{cond_idx}][value]", v_str))
            params.append((f"conditions[{cond_idx}][operator]", op))
            cond_idx += 1
    return params


def get_total_count(filters, operator):
    params = build_params(0, filters, operator, limit=1)
    r = SESSION.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("count", 0)


def fetch_all(filters, operator):
    offset = 0
    total = None
    all_records = []

    while True:
        params = build_params(offset, filters, operator)
        try:
            r = SESSION.get(BASE_URL, params=params, timeout=30)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            break

        data = r.json()
        records = data.get("results", [])

        if total is None:
            total = data.get("count", 0)
            print(f"CMS total: {total:,}")
            if total == 0:
                break

        if not records:
            break

        all_records.extend(records)
        print(f"  fetched {len(all_records):,} / {total:,}", end="\r")

        if len(all_records) >= total:
            break

        offset += PAGE_SIZE
        time.sleep(SLEEP_BETWEEN_PAGES)

    print()
    return all_records, total


def dedupe(records):
    counter = Counter(r.get("npi", "") for r in records if r.get("npi"))
    seen = set()
    out = []
    for r in records:
        npi = r.get("npi", "")
        if npi and npi not in seen:
            seen.add(npi)
            row = dict(r)
            row["location_count"] = counter[npi]
            out.append(row)
    return out


def save_csv(records, path):
    if not records:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=records[0].keys())
        w.writeheader()
        w.writerows(records)
    print(f"CSV: {path} ({len(records):,} rows)")


def save_json(records, total_count, filters, path):
    slim = [{k: r.get(k, "") for k in KEEP} for r in records]
    payload = {
        "total": total_count,
        "unique_count": len({r["npi"] for r in slim if r.get("npi")}),
        "filters": filters,
        "records": slim,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"JSON: {path} ({len(slim):,} records)")


def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5000")


def main():
    print("=" * 60)
    print("  Fetching CMS data → JSON → Browser")
    print("=" * 60)

    if not FETCH_FILTERS:
        print("Note: FETCH_FILTERS is empty. Using default 'state': 'MA' for safety.")
        FETCH_FILTERS["state"] = "MA"
        print(f"Filter: {FETCH_FILTERS}")

    total_count = get_total_count(FETCH_FILTERS, OPERATOR)
    print(f"Total matching: {total_count:,}")

    if total_count == 0:
        print("No records found. Check FETCH_FILTERS.")
        return

    all_records, _ = fetch_all(FETCH_FILTERS, OPERATOR)
    print(f"Fetched: {len(all_records):,}")

    save_csv(all_records, OUTPUT_CSV)
    unique = dedupe(all_records)
    save_csv(unique, UNIQUE_CSV)
    save_json(unique, total_count, FETCH_FILTERS, OUTPUT_JSON)

    print("\nOpening browser...")
    threading.Thread(target=open_browser, daemon=True).start()


if __name__ == "__main__":
    main()
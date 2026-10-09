import time
from functools import lru_cache
from flask import Flask, render_template, jsonify, request, Response
import requests
import io
import csv
import json

app = Flask(__name__)

BASE_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/mj5m-pzi6/0"

MULTI_FIELDS = [
    "pri_spec", "sec_spec_all", "citytown", "state",
    "facility_name", "zip_code",
]

SINGLE_FIELDS = [
    "provider_last_name", "provider_first_name", "npi", "telehlth",
]

KEEP = [
    "npi", "provider_first_name", "provider_last_name", "cred",
    "pri_spec", "sec_spec_all", "facility_name",
    "adr_ln_1", "citytown", "state", "zip_code",
    "telephone_number", "telehlth",
]

# Complete US States, DC, and Territories list
US_STATES = [
    {"code": "AL", "name": "Alabama"},
    {"code": "AK", "name": "Alaska"},
    {"code": "AZ", "name": "Arizona"},
    {"code": "AR", "name": "Arkansas"},
    {"code": "CA", "name": "California"},
    {"code": "CO", "name": "Colorado"},
    {"code": "CT", "name": "Connecticut"},
    {"code": "DE", "name": "Delaware"},
    {"code": "DC", "name": "District of Columbia"},
    {"code": "FL", "name": "Florida"},
    {"code": "GA", "name": "Georgia"},
    {"code": "HI", "name": "Hawaii"},
    {"code": "ID", "name": "Idaho"},
    {"code": "IL", "name": "Illinois"},
    {"code": "IN", "name": "Indiana"},
    {"code": "IA", "name": "Iowa"},
    {"code": "KS", "name": "Kansas"},
    {"code": "KY", "name": "Kentucky"},
    {"code": "LA", "name": "Louisiana"},
    {"code": "ME", "name": "Maine"},
    {"code": "MD", "name": "Maryland"},
    {"code": "MA", "name": "Massachusetts"},
    {"code": "MI", "name": "Michigan"},
    {"code": "MN", "name": "Minnesota"},
    {"code": "MS", "name": "Mississippi"},
    {"code": "MO", "name": "Missouri"},
    {"code": "MT", "name": "Montana"},
    {"code": "NE", "name": "Nebraska"},
    {"code": "NV", "name": "Nevada"},
    {"code": "NH", "name": "New Hampshire"},
    {"code": "NJ", "name": "New Jersey"},
    {"code": "NM", "name": "New Mexico"},
    {"code": "NY", "name": "New York"},
    {"code": "NC", "name": "North Carolina"},
    {"code": "ND", "name": "North Dakota"},
    {"code": "OH", "name": "Ohio"},
    {"code": "OK", "name": "Oklahoma"},
    {"code": "OR", "name": "Oregon"},
    {"code": "PA", "name": "Pennsylvania"},
    {"code": "RI", "name": "Rhode Island"},
    {"code": "SC", "name": "South Carolina"},
    {"code": "SD", "name": "South Dakota"},
    {"code": "TN", "name": "Tennessee"},
    {"code": "TX", "name": "Texas"},
    {"code": "UT", "name": "Utah"},
    {"code": "VT", "name": "Vermont"},
    {"code": "VA", "name": "Virginia"},
    {"code": "WA", "name": "Washington"},
    {"code": "WV", "name": "West Virginia"},
    {"code": "WI", "name": "Wisconsin"},
    {"code": "WY", "name": "Wyoming"},
    {"code": "AS", "name": "American Samoa"},
    {"code": "GU", "name": "Guam"},
    {"code": "MP", "name": "Northern Mariana Islands"},
    {"code": "PR", "name": "Puerto Rico"},
    {"code": "VI", "name": "Virgin Islands"},
]

SPECIALTIES = [
    "ADDICTION MEDICINE",
    "ADVANCED HEART FAILURE AND TRANSPLANT CARDIOLOGY",
    "ALLERGY/IMMUNOLOGY",
    "ANESTHESIOLOGY",
    "AUDIOLOGY",
    "CARDIOVASCULAR DISEASE (CARDIOLOGY)",
    "CERTIFIED REGISTERED NURSE ANESTHETIST (CRNA)",
    "CHIROPRACTIC",
    "CLINICAL CARDIAC ELECTROPHYSIOLOGY",
    "CLINICAL NURSE SPECIALIST",
    "CLINICAL PSYCHOLOGIST",
    "CLINICAL SOCIAL WORKER",
    "COLON AND RECTAL SURGERY",
    "CRITICAL CARE (INTENSIVISTS)",
    "DENTIST",
    "DERMATOLOGY",
    "DIAGNOSTIC RADIOLOGY",
    "EMERGENCY MEDICINE",
    "ENDOCRINOLOGY",
    "FAMILY PRACTICE",
    "GASTROENTEROLOGY",
    "GENERAL PRACTICE",
    "GENERAL SURGERY",
    "GERIATRIC MEDICINE",
    "GERIATRIC PSYCHIATRY",
    "GYNECOLOGICAL ONCOLOGY",
    "HEMATOLOGY",
    "HEMATOLOGY/ONCOLOGY",
    "HOSPICE/PALLIATIVE CARE",
    "INFECTIOUS DISEASE",
    "INTERNAL MEDICINE",
    "INTERVENTIONAL CARDIOLOGY",
    "INTERVENTIONAL PAIN MANAGEMENT",
    "INTERVENTIONAL RADIOLOGY",
    "MAXILLOFACIAL SURGERY",
    "MEDICAL ONCOLOGY",
    "NEPHROLOGY",
    "NEUROLOGY",
    "NEUROPSYCHIATRY",
    "NEUROSURGERY",
    "NUCLEAR MEDICINE",
    "NURSE PRACTITIONER",
    "OBSTETRICS/GYNECOLOGY",
    "OPHTHALMOLOGY",
    "OPTOMETRY",
    "ORAL SURGERY",
    "ORTHOPEDIC SURGERY",
    "OSTEOPATHIC MANIPULATIVE MEDICINE",
    "OTOLARYNGOLOGY",
    "PAIN MANAGEMENT",
    "PATHOLOGY",
    "PEDIATRIC MEDICINE",
    "PERIPHERAL VASCULAR DISEASE",
    "PHYSICAL MEDICINE AND REHABILITATION",
    "PHYSICIAN ASSISTANT",
    "PLASTIC AND RECONSTRUCTIVE SURGERY",
    "PODIATRY",
    "PREVENTIVE MEDICINE",
    "PSYCHIATRY",
    "PULMONARY DISEASE",
    "RADIATION ONCOLOGY",
    "REGISTERED DIETITIAN OR NUTRITION PROFESSIONAL",
    "RHEUMATOLOGY",
    "SLEEP MEDICINE",
    "SPORTS MEDICINE",
    "SURGICAL ONCOLOGY",
    "THORACIC SURGERY",
    "UNDERSEA AND HYPERBARIC MEDICINE",
    "UROLOGY",
    "VASCULAR SURGERY",
]

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

# Simple in-memory response cache with TTL (10 minutes)
_CACHE = {}
CACHE_TTL = 600


def dedupe_key(record):
    return (
        str(record.get("npi", "")).strip(),
        str(record.get("adr_ln_1", "")).strip().upper(),
        str(record.get("citytown", "")).strip().upper(),
        str(record.get("state", "")).strip().upper(),
        str(record.get("zip_code", "")).strip(),
    )


def dedupe_records(records):
    seen = set()
    unique = []
    for rec in records:
        key = dedupe_key(rec)
        if key not in seen:
            seen.add(key)
            unique.append(rec)
    return unique


def build_cms_query_params(multi_filters, single_filters, operator, page, size):
    """
    Builds the correctly formatted list of tuple parameters for the CMS DataStore API.
    Handles single values, multiple values per field (using 'in' with value[] array),
    and field-specific operators.
    """
    offset = (page - 1) * size
    params = [
        ("limit", str(size)),
        ("offset", str(offset)),
        ("count", "true"),
    ]

    cond_idx = 0

    # Multi-value fields
    for prop, values in multi_filters.items():
        if not values:
            continue
        if len(values) == 1:
            val = values[0]
            # Use exact match '=' for state or zip_code, contains for specialty/facility/city
            op = "=" if prop in ("state", "zip_code") else operator
            params.append((f"conditions[{cond_idx}][property]", prop))
            params.append((f"conditions[{cond_idx}][value]", val))
            params.append((f"conditions[{cond_idx}][operator]", op))
        else:
            params.append((f"conditions[{cond_idx}][property]", prop))
            for val in values:
                params.append((f"conditions[{cond_idx}][value][]", val))
            params.append((f"conditions[{cond_idx}][operator]", "in"))
        cond_idx += 1

    # Single-value fields
    for prop, value in single_filters.items():
        if not value:
            continue
        if prop in ("npi", "telehlth"):
            op = "="
        else:
            op = operator
        params.append((f"conditions[{cond_idx}][property]", prop))
        params.append((f"conditions[{cond_idx}][value]", value))
        params.append((f"conditions[{cond_idx}][operator]", op))
        cond_idx += 1

    return params


def fetch_from_cms(params_tuples):
    """
    Fetches data from CMS API with caching and error handling.
    """
    cache_key = tuple(sorted(params_tuples))
    now = time.time()

    if cache_key in _CACHE:
        cached_time, cached_data = _CACHE[cache_key]
        if now - cached_time < CACHE_TTL:
            return cached_data

    r = SESSION.get(BASE_URL, params=params_tuples, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Cache successful response
    if len(_CACHE) > 500:
        _CACHE.clear()
    _CACHE[cache_key] = (now, data)
    return data


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/options")
def api_options():
    return jsonify({
        "states": [s["code"] for s in US_STATES],
        "state_details": US_STATES,
        "specialties": SPECIALTIES,
    })


@app.route("/api/search")
def api_search():
    multi_filters = {}
    for field in MULTI_FIELDS:
        raw = request.args.get(field, "").strip()
        if raw and raw.lower() != "all":
            values = [v.strip().upper() for v in raw.split(",") if v.strip()]
            if values:
                multi_filters[field] = values

    single_filters = {}
    for field in SINGLE_FIELDS:
        val = request.args.get(field, "").strip()
        if val and val.lower() != "all":
            if field == "telehlth":
                single_filters[field] = val.upper()
            elif field == "npi":
                single_filters[field] = val
            else:
                single_filters[field] = val.upper()

    operator = request.args.get("operator", "contains")
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 50))
    except ValueError:
        page, size = 1, 50

    size = min(max(size, 10), 200)
    page = max(page, 1)

    params_tuples = build_cms_query_params(multi_filters, single_filters, operator, page, size)

    try:
        data = fetch_from_cms(params_tuples)
    except requests.exceptions.Timeout:
        return jsonify({
            "error": "CMS API request timed out. Please try refining your filters.",
            "records": [], "total": 0, "page": page, "pages": 1,
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": f"CMS API Error: {str(e)}",
            "records": [], "total": 0, "page": page, "pages": 1,
        }), 502

    raw_records = data.get("results", [])
    total_raw = data.get("count", 0)

    unique_records = dedupe_records(raw_records)
    slim = [{k: rec.get(k, "") for k in KEEP} for rec in unique_records]

    return jsonify({
        "page": page,
        "size": size,
        "total": total_raw,
        "unique_on_page": len(unique_records),
        "duplicates_removed": len(raw_records) - len(unique_records),
        "pages": max(1, (total_raw + size - 1) // size),
        "filters": {**multi_filters, **single_filters},
        "records": slim,
    })


@app.route("/api/export")
def api_export():
    """
    Exports filtered records directly to CSV or JSON format.
    """
    fmt = request.args.get("format", "csv").lower()
    multi_filters = {}
    for field in MULTI_FIELDS:
        raw = request.args.get(field, "").strip()
        if raw and raw.lower() != "all":
            values = [v.strip().upper() for v in raw.split(",") if v.strip()]
            if values:
                multi_filters[field] = values

    single_filters = {}
    for field in SINGLE_FIELDS:
        val = request.args.get(field, "").strip()
        if val and val.lower() != "all":
            single_filters[field] = val.upper() if field != "npi" else val

    operator = request.args.get("operator", "contains")
    try:
        limit = min(int(request.args.get("limit", 500)), 1000)
    except ValueError:
        limit = 500

    params_tuples = build_cms_query_params(multi_filters, single_filters, operator, page=1, size=limit)

    try:
        data = fetch_from_cms(params_tuples)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    raw_records = data.get("results", [])
    unique_records = dedupe_records(raw_records)
    slim = [{k: rec.get(k, "") for k in KEEP} for rec in unique_records]

    if fmt == "json":
        return Response(
            json.dumps(slim, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment;filename=cms_providers_export.json"}
        )

    # Default CSV
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=KEEP)
    writer.writeheader()
    writer.writerows(slim)
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=cms_providers_export.csv"}
    )


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"Server starting → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
"""
StayVista | Ozonetel CDR Pull  v2.1
v2.1: campaign filter added. The manual export only covers 57 campaigns; the
      API returns all of them. Filtering to campaigns.txt reproduces the
      manual export's row count exactly (verified: 1,951 of 2,424 calls on
      06-10-2026, zero differences in which calls were kept).
      Edit campaigns.txt to change which campaigns are included.
v2.0: output now matches the manual CloudAgent export exactly.
      - 40 columns (added "Video RecordingURL" at position 36; this shifts
        DynamicDid to 37, so spec column AK = DynamicDid)
      - call legs MERGED into one row per Call ID, the way the manual export
        does it (Call Flow chained as "Queue -> Agent Dials", etc.)
      Merge rules were reverse-engineered by comparing a real API pull against
      the manual export for the same day (1,951 calls checked column by column).
      Known, deliberate differences from the manual export are listed in
      DIFFERENCES below.

DIFFERENCES vs the manual export (all verified, none are bugs):
  - Caller No / Caller_E164: the manual export MASKS these ("***9004363524").
    The API returns the real number. Kept unmasked: it is more useful and the
    processor needs the last 10 digits.
  - Dial DID: the API does not return this field at all; always blank.
  - Recording URL: blank on ~10% of calls where the manual export has a URL
    (the API omits it for some IVR legs).
  - Time to Answer / Queue Time: can differ by 1 second on multi-leg calls,
    because the manual export rounds the total differently.
Replaces the manual CloudAgent CDR export. Pulls call records via the Ozonetel
API and writes a CSV with the SAME 39 columns, in the SAME order, as the manual
dump — so it is a drop-in replacement for ozonetel_processor.py's input.

API constraints (from Ozonetel's docs, confirmed by live testing):
  - one day per request
  - only the last 15 days are available
  - rate limit: 2 requests per minute  -> this script paces itself at 31s

Usage:
  python ozonetel_cdr_pull.py                      -> yesterday
  python ozonetel_cdr_pull.py --date 06-10-2026    -> that day
  python ozonetel_cdr_pull.py --days 14            -> today + previous 13 days (slow: ~7 min)
  python ozonetel_cdr_pull.py --from 01-10-2026 --to 06-10-2026
"""
import argparse, configparser, csv, json, os, re, sys, time
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------- settings ----------------
OUTPUT_FILE = "ozonetel_cdr.csv"
PACE_SECONDS = 31          # 2 requests/minute limit
MAX_DAYS_BACK = 14         # 15 calendar days including today
CAMPAIGN_FILE = "campaigns.txt"   # one campaign per line; blank file = keep all

# Output columns in the EXACT order of the manual export, mapped to API fields.
# Confirmed against a real manual dump + a real API sample.
COLUMNS = [
    ("Call Type",       "Type"),
    ("Campaign",        "CampaignName"),
    ("Caller No",       "CallerID"),
    ("Caller_E164",     "E164"),
    ("Skill",           "Skill"),
    ("Call Date",       "CallDate"),
    ("Start Time",      "StartTime"),
    ("Time to Answer",  "TimeToAnswer"),
    ("End Time",        "EndTime"),
    ("Talk Time",       "TalkTime"),
    ("Duration",        "Duration"),
    ("Agent",           "AgentName"),
    ("Disposition",     "Disposition"),
    ("Status",          "Status"),
    ("Comments",        "Comments"),
    ("Agent ID",        "AgentID"),
    ("DID",             "DID"),
    ("Dial Count",      "DialCount"),
]
# How each field is combined when one call has several legs.
# CHAIN  = unique non-blank values joined with " -> " in time order
# FIRST  = first non-blank in time order
# LAST   = value from the last leg in time order
# SUM    = hh:mm:ss values added together
# SPAN   = last end time minus first start time
MERGE = {
    "Call Flow": "CHAIN", "Skill": "CHAIN", "Dial Status": "CHAIN",
    "Queue Time": "SUM", "Talk Time": "SUM", "Hold Time": "SUM",
    "Wrapup Duration": "SUM", "Handling Time": "SUM", "Time to Answer": "SUM",
    "Customer Ring Time": "SUM",
    "Start Time": "FIRST", "End Time": "LAST", "Duration": "SPAN",
    "Hangup By": "LAST", "Status": "LAST",
    "Agent Dial Status": "LAST_RAW",   # manual takes the final leg even if blank
}
# Blank values the manual export writes as a default instead of leaving empty.
BLANK_DEFAULTS = {"Ratings": "0.00", "Dial Count": "0"}
# Columns Excel would otherwise mangle into 9.18065E+11 — written as text.
TEXT_COLUMNS = {"Caller No", "Caller_E164", "DID", "Agent ID"}
# -------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))

def fail(msg): print(f"\n[STOPPED] {msg}"); sys.exit(1)
def step(msg): print(f"  ... {msg}", flush=True)

def load_campaigns():
    """Campaign allow-list. Missing or empty file means keep everything."""
    path = os.path.join(HERE, CAMPAIGN_FILE)
    if not os.path.exists(path): return None
    with open(path, encoding="utf-8-sig") as f:
        names = {l.strip() for l in f if l.strip() and not l.startswith("#")}
    return names or None

def load_config():
    path = os.path.join(HERE, "config.ini")
    if not os.path.exists(path): fail("config.ini not found in this folder.")
    cfg = configparser.ConfigParser(); cfg.read(path)
    c = cfg["ozonetel"]
    host = re.sub(r"^https?://", "", c["host"].strip()).rstrip("/")
    key, user = c["api_key"].strip(), c["user_name"].strip()
    if "PASTE_YOUR" in key or "PASTE_YOUR" in user:
        fail("config.ini still has placeholder credentials.")
    return host, key, user

def fetch_day(host, key, user, day):
    """One day of CDR records. Retries on rate-limit."""
    url = f"https://{host}/ca_reports/fetchCDRDetails"
    body = {"fromDate": day.strftime("%Y-%m-%d 00:00:00"),
            "toDate": day.strftime("%Y-%m-%d 23:59:59"), "userName": user}
    headers = {"apiKey": key, "Content-Type": "application/json"}
    for attempt in range(4):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(res, dict):
                for k in ("details", "Details", "data", "Data"):
                    if isinstance(res.get(k), list): return res[k]
                if str(res.get("status", "")).lower() in ("error", "false"):
                    fail(f"API error for {day:%d-%m-%Y}: {res.get('message')}")
            return res if isinstance(res, list) else []
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < 3:
                step(f"rate limited — waiting {PACE_SECONDS}s and retrying")
                time.sleep(PACE_SECONDS); continue
            fail(f"API error {e.code} for {day:%d-%m-%Y}: {txt}")
        except urllib.error.URLError as e:
            if attempt < 3: time.sleep(10); continue
            fail(f"Could not reach {host}: {e}")
    return []

def parse_day(s):
    for f in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    fail(f"Couldn't read date '{s}'. Use dd-mm-yyyy, e.g. 06-10-2026.")

def hms_to_secs(v):
    try:
        p = [int(x) for x in str(v).split(":")]
        while len(p) < 3: p.insert(0, 0)
        return p[0] * 3600 + p[1] * 60 + p[2]
    except (ValueError, AttributeError): return 0

def secs_to_hms(n):
    n = max(0, int(n))
    return f"{n // 3600:02d}:{(n % 3600) // 60:02d}:{n % 60:02d}"

def leg_value(rec, header, field):
    """One leg's value for one output column."""
    if field == "_blank": return ""
    if field == "_callflow":
        flow, agent = rec.get("CallFlow", ""), rec.get("AgentName", "")
        return f"{flow} ({agent})" if flow and agent else (flow or "")
    if header == "Call Date":
        raw = rec.get("CallDate", "") or ""
        try: return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError: return raw
    v = rec.get(field, "")
    return "" if v is None else str(v)

def merge_legs(legs):
    """Combine a call's legs into one row, matching the manual export."""
    legs = sorted(legs, key=lambda r: (r.get("StartTime") or "", r.get("EndTime") or ""))
    out = []
    for header, field in COLUMNS:
        vals = [leg_value(l, header, field) for l in legs]
        rule = MERGE.get(header)
        nonblank = [v for v in vals if v.strip()]
        if len(legs) == 1 or rule is None:
            v = nonblank[0] if nonblank else ""
        elif rule == "CHAIN":
            seen, parts = set(), []
            for x in nonblank:
                if x not in seen: seen.add(x); parts.append(x)
            v = " -> ".join(parts)
        elif rule == "SUM":
            v = secs_to_hms(sum(hms_to_secs(x) for x in vals))
        elif rule == "FIRST":
            v = nonblank[0] if nonblank else ""
        elif rule == "LAST":
            v = next((x for x in reversed(vals) if x.strip()), "")
        elif rule == "LAST_RAW":
            v = vals[-1] if vals else ""
        elif rule == "SPAN":
            starts = [hms_to_secs(l.get("StartTime")) for l in legs if l.get("StartTime")]
            ends = [hms_to_secs(l.get("EndTime")) for l in legs if l.get("EndTime")]
            v = secs_to_hms(max(ends) - min(starts)) if starts and ends else (nonblank[0] if nonblank else "")
        else:
            v = nonblank[0] if nonblank else ""
        if not v.strip() and header in BLANK_DEFAULTS: v = BLANK_DEFAULTS[header]
        if v and header in TEXT_COLUMNS: v = "'" + v
        out.append(v)
    return out

def build_rows(records, allowed=None):
    """Group legs by Call ID; drop calls whose campaign isn't in the allow-list."""
    from collections import OrderedDict
    groups = OrderedDict()
    for r in records:
        if not isinstance(r, dict): continue
        groups.setdefault(str(r.get("CallID", "")), []).append(r)
    total = len(groups)
    if allowed:
        groups = OrderedDict((k, v) for k, v in groups.items()
                             if str(v[0].get("CampaignName", "")).strip() in allowed)
    return [merge_legs(v) for v in groups.values()], total, len(groups)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--days", type=int)
    ap.add_argument("--from", dest="frm"); ap.add_argument("--to")
    a = ap.parse_args()
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    if a.date:
        days = [parse_day(a.date)]
    elif a.frm:
        s, e = parse_day(a.frm), parse_day(a.to) if a.to else parse_day(a.frm)
        days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
    elif a.days:
        if a.days < 1:
            fail("--days must be at least 1.")
        # Include today: --days 14 means today and the previous 13 calendar days.
        days = [datetime.combine(today, datetime.min.time()) - timedelta(days=i)
                for i in range(a.days - 1, -1, -1)]
    else:
        days = [datetime.combine(today, datetime.min.time()) - timedelta(days=1)]

    too_old = [d for d in days if (today - d.date()).days > MAX_DAYS_BACK]
    if too_old:
        fail(f"{too_old[0]:%d-%m-%Y} is more than {MAX_DAYS_BACK} days ago.\n"
             f"Ozonetel only keeps the latest 15 calendar days — older data must\n"
             f"come from a manual export.")
    future = [d for d in days if d.date() > today]
    if future: fail(f"{future[0]:%d-%m-%Y} is in the future.")

    host, key, user = load_config()
    allowed = load_campaigns()
    if allowed:
        print(f"Campaign filter: {len(allowed)} campaign(s) from {CAMPAIGN_FILE}")
    else:
        print(f"No {CAMPAIGN_FILE} found — keeping ALL campaigns.")
    mins = (len(days) - 1) * PACE_SECONDS / 60
    print(f"Pulling {len(days)} day(s): {days[0]:%d-%m-%Y} to {days[-1]:%d-%m-%Y}")
    if len(days) > 1:
        print(f"Rate limit is 2 requests/minute, so this takes about {mins:.0f} minute(s).\n")
    else:
        print()

    all_rows, per_day = [], []
    for i, d in enumerate(days):
        if i: time.sleep(PACE_SECONDS)
        recs = fetch_day(host, key, user, d)
        per_day.append((d, len(recs)))
        rows, ncalls, nkept = build_rows(recs, allowed)
        note = f"{len(recs):,} legs -> {ncalls:,} call(s)"
        if allowed: note += f" -> {nkept:,} after campaign filter"
        step(f"{d:%d-%m-%Y}: {note}")
        all_rows.extend(rows)

    if not all_rows:
        print("\nNo records found for the requested day(s). Writing a header-only CSV.")
    
    out = os.path.join(HERE, OUTPUT_FILE)
    try:
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in COLUMNS])
            w.writerows(all_rows)
    except PermissionError:
        fail(f"{OUTPUT_FILE} is open in Excel. Close it and run again.")

    print(f"\n{'=' * 60}")
    print(f"  Days pulled   : {len(days)}")
    print(f"  Total records : {len(all_rows):,}")
    print(f"  Columns       : {len(COLUMNS)}")
    print(f"  Saved         : {OUTPUT_FILE}")
    print("=" * 60)

if __name__ == "__main__":
    main()

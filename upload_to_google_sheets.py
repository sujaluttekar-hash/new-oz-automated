"""Append T-2 CDR rows without changing existing sheet data or the 18-column layout."""
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

CSV_PATH = Path(__file__).with_name("ozonetel_cdr.csv")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LOG_WORKSHEET_NAME = "logs"
EXPECTED_HEADERS = [
    "Call Type", "Campaign", "Caller No", "Caller_E164", "Skill",
    "Call Date", "Start Time", "Time to Answer", "End Time", "Talk Time",
    "Duration", "Agent", "Disposition", "Status", "Comments", "Agent ID",
    "DID", "Dial Count",
]
LOG_HEADERS = [
    "Run Time (IST)", "Status", "Refresh Start Date", "Refresh End Date",
    "Rows Added (New Dates)", "Rows Refreshed/Replaced", "Historical Rows Retained",
    "Total Data Rows After Refresh", "Columns Written", "Data Tab", "Trigger",
    "GitHub Run Number", "GitHub Run ID",
]


def get_or_create_worksheet(spreadsheet, title, rows=1000, cols=20):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def parse_date(value):
    value = (value or "").strip().lstrip("'")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            pass
    return None


def main():
    if not CSV_PATH.exists():
        raise SystemExit("Upload stopped: ozonetel_cdr.csv was not created by the API pull.")

    with CSV_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != EXPECTED_HEADERS:
        raise SystemExit(
            f"Upload stopped: CSV headers do not match the required {len(EXPECTED_HEADERS)} columns."
        )

    from_text = os.environ.get("CDR_FROM_DATE", "").strip()
    to_text = os.environ.get("CDR_TO_DATE", "").strip()
    start_date, end_date = parse_date(from_text), parse_date(to_text)
    if not start_date or not end_date or start_date > end_date:
        raise SystemExit("Upload stopped: CDR_FROM_DATE/CDR_TO_DATE are missing or invalid.")

    incoming = [row + [""] * (len(EXPECTED_HEADERS) - len(row)) for row in rows[1:] if any(c.strip() for c in row)]
    if not incoming:
        raise SystemExit("Upload stopped: CSV contains no data rows; existing sheet was left unchanged.")
    date_idx = EXPECTED_HEADERS.index("Call Date")
    invalid_dates = [r[date_idx] for r in incoming if not parse_date(r[date_idx])]
    if invalid_dates:
        raise SystemExit(f"Upload stopped: found an unparseable Call Date: {invalid_dates[0]!r}")

    # Reject rows outside the requested T-2 append date.
    incoming_dates = [parse_date(r[date_idx]) for r in incoming]
    if any(d < start_date or d > end_date for d in incoming_dates):
        raise SystemExit("Upload stopped: CSV contains Call Date values outside the requested refresh window.")

    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.environ.get("GOOGLE_WORKSHEET_NAME", "ozontel dump").strip()
    if not credentials_json or not sheet_id:
        raise SystemExit("Upload stopped: required Google Sheets secrets/configuration are missing.")
    try:
        service_account_info = json.loads(credentials_json)
    except json.JSONDecodeError:
        raise SystemExit("Upload stopped: GOOGLE_SERVICE_ACCOUNT_JSON must contain valid service-account JSON.")

    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    spreadsheet = gspread.authorize(credentials).open_by_key(sheet_id)
    worksheet = get_or_create_worksheet(
        spreadsheet, worksheet_name, rows=max(len(incoming) + 1000, 1000), cols=len(EXPECTED_HEADERS)
    )

    old_values = worksheet.get_all_values()
    if old_values:
        if old_values[0] != EXPECTED_HEADERS:
            raise SystemExit(
                f"Upload stopped: existing '{worksheet_name}' headers do not match the required 18 columns; "
                "sheet was left unchanged."
            )
        old_rows = [
            (r + [""] * (len(EXPECTED_HEADERS) - len(r)))[:len(EXPECTED_HEADERS)]
            for r in old_values[1:] if any(c.strip() for c in r)
        ]
    else:
        old_rows = []
        worksheet.update(range_name="A1", values=[EXPECTED_HEADERS], value_input_option="RAW")

    # Append-only: never clear, rewrite, sort, or replace existing sheet rows.
    # Exact-row matching prevents duplicates when the scheduled workflow reruns.
    existing_row_keys = {tuple(r) for r in old_rows}
    new_rows = []
    seen_new_keys = set()
    for row in incoming:
        key = tuple((row + [""] * len(EXPECTED_HEADERS))[:len(EXPECTED_HEADERS)])
        if key not in existing_row_keys and key not in seen_new_keys:
            new_rows.append(list(key))
            seen_new_keys.add(key)

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED", table_range="A1")

    rows_added = len(new_rows)
    rows_refreshed = 0
    historical_rows_count = len(old_rows)
    total_rows = len(old_rows) + rows_added

    log_ws = get_or_create_worksheet(spreadsheet, LOG_WORKSHEET_NAME, rows=1000, cols=len(LOG_HEADERS))
    current_headers = log_ws.row_values(1)
    if current_headers != LOG_HEADERS:
        log_ws.update(range_name="A1", values=[LOG_HEADERS], value_input_option="RAW")

    timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%b-%Y %I:%M:%S %p")
    log_row = [
        timestamp, "SUCCESS", start_date.strftime("%d-%m-%Y"), end_date.strftime("%d-%m-%Y"),
        rows_added, rows_refreshed, historical_rows_count, total_rows, len(EXPECTED_HEADERS),
        worksheet_name, os.environ.get("GITHUB_EVENT_NAME", "manual/local"),
        os.environ.get("GITHUB_RUN_NUMBER", ""), os.environ.get("GITHUB_RUN_ID", ""),
    ]
    log_ws.append_row(log_row, value_input_option="USER_ENTERED")
    print(
        f"Append successful: {rows_added} new rows appended; no existing rows changed; "
        f"{total_rows} total data rows; {len(EXPECTED_HEADERS)} columns. "
        f"Date {start_date:%d-%m-%Y}. Logged in '{LOG_WORKSHEET_NAME}'."
    )


if __name__ == "__main__":
    main()

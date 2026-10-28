"""Upload the latest Ozonetel CDR CSV to Google Sheets and append a run log."""
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
LOG_HEADERS = [
    "Run Time (IST)",
    "Status",
    "Data Rows Uploaded",
    "Total Rows After Refresh",
    "Columns",
    "Data Tab",
    "Trigger",
    "GitHub Run Number",
    "GitHub Run ID",
]


def get_or_create_worksheet(spreadsheet, title, rows=1000, cols=20):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def main():
    if not CSV_PATH.exists():
        raise SystemExit("Upload stopped: ozonetel_cdr.csv was not created by the API pull.")

    with CSV_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if len(rows) < 2 or not rows[0] or not any(cell.strip() for cell in rows[0]):
        raise SystemExit("Upload stopped: CSV has no data rows; existing sheet was left unchanged.")

    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.environ.get("GOOGLE_WORKSHEET_NAME", "ozontel dump").strip()
    if not credentials_json or not sheet_id:
        raise SystemExit("Upload stopped: required Google Sheets secrets/configuration are missing.")

    try:
        service_account_info = json.loads(credentials_json)
    except json.JSONDecodeError:
        raise SystemExit("Upload stopped: GOOGLE_SERVICE_ACCOUNT_JSON must contain the service-account JSON.")

    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = get_or_create_worksheet(
        spreadsheet, worksheet_name, rows=max(len(rows), 1000), cols=max(len(rows[0]), 40)
    )

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    data_row_count = len(normalized) - 1

    # Replace the data tab with this fresh CSV.
    worksheet.clear()
    worksheet.resize(rows=max(len(normalized), 1), cols=max(width, 1))
    worksheet.update(range_name="A1", values=normalized, value_input_option="USER_ENTERED")

    # Append one audit entry per successful refresh.
    log_ws = get_or_create_worksheet(
        spreadsheet, LOG_WORKSHEET_NAME, rows=1000, cols=len(LOG_HEADERS)
    )
    if log_ws.acell("A1").value != LOG_HEADERS[0]:
        log_ws.update(range_name="A1", values=[LOG_HEADERS], value_input_option="RAW")

    timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%b-%Y %I:%M:%S %p")
    log_row = [
        timestamp,
        "SUCCESS",
        data_row_count,
        data_row_count,
        width,
        worksheet_name,
        os.environ.get("GITHUB_EVENT_NAME", "manual/local"),
        os.environ.get("GITHUB_RUN_NUMBER", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    ]
    log_ws.append_row(log_row, value_input_option="USER_ENTERED")

    print(
        f"Google Sheets refresh successful: {data_row_count} data rows uploaded, "
        f"{width} columns, tab '{worksheet_name}'. Run logged in '{LOG_WORKSHEET_NAME}'."
    )


if __name__ == "__main__":
    main()

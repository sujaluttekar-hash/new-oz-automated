# Ozonetel CDR to Google Sheets

Automates Ozonetel CDR retrieval and appends only the previous day's (T-1) CDRs to the configured Google Sheets tab.

## Automation
- Scheduled daily at **10:30 AM Asia/Kolkata** (05:00 UTC).
- Each scheduled run pulls **yesterday only**; it must not pull T-2, today, or a multi-day range.
- Output worksheet: `ozontel dump`.
- Exact output columns, in order: `Call ID, Call Type, Campaign, Caller No, Caller_E164, Skill, Call Date, Start Time, Time to Answer, End Time, Talk Time, Duration, Agent, Disposition, Status, Comments, Agent ID, DID, Dial Count`.
- Call ID is the primary deduplication key. If a record has no Call ID, the uploader falls back to stable call fields.
- A `logs` worksheet records successful uploads.
- The uploader recognizes the previous 18-column layout and the old 19-column layout with Call ID at the end. It migrates the header/order while retaining existing cell values.

## Required GitHub Actions secrets
Configure these under **Settings → Secrets and variables → Actions**:
- `OZONETEL_API_KEY`
- `OZONETEL_USER_NAME`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SHEET_ID`

Optional repository variable:
- `OZONETEL_HOST` (defaults to `in1-ccaas-api.ozonetel.com`)

The Google Sheets service account must have edit access to the target spreadsheet.

## Local use
1. Install dependencies: `pip install gspread google-auth`
2. Enter Ozonetel credentials privately in `config.ini`.
3. Run `python ozonetel_cdr_pull.py --date DD-MM-YYYY` for a specific day.

Do not commit API credentials, service-account JSON, logs, or exported call data.

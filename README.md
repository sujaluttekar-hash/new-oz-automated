# Ozonetel CDR to Google Sheets

Automates Ozonetel CDR retrieval and refreshes the configured Google Sheets tab.

## Automation
- GitHub Actions runs hourly.
- Each run requests the latest 14 calendar days, including today.
- Output is uploaded to the `ozontel dump` worksheet.
- A `logs` worksheet records successful refreshes.

## Required GitHub Actions secrets
Configure these under **Settings → Secrets and variables → Actions**:
- `OZONETEL_API_KEY`
- `OZONETEL_USER_NAME`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Optional repository variable:
- `OZONETEL_HOST` (defaults to `in1-ccaas-api.ozonetel.com`)

The Google Sheets service account must have edit access to the target spreadsheet.

## Local use
1. Install dependencies: `pip install gspread google-auth`
2. Enter Ozonetel credentials privately in `config.ini`.
3. Run `python ozonetel_cdr_pull.py --days 14`.

Do not commit API credentials, service-account JSON, logs, or exported call data.

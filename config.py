"""
Config, environment variables, logging, conversation states.
"""

import os, json, logging

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
RECEIPTS_CHANNEL_ID = int(os.environ.get("RECEIPTS_CHANNEL_ID", "0"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_owners_raw = os.environ.get("OWNERS_JSON", '{"76341596":"Jeremy"}')
try: OWNERS = {int(k):v for k,v in json.loads(_owners_raw).items()}
except: OWNERS = {76341596:"Jeremy"}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

def is_owner(uid): return uid in OWNERS
def owner_name(uid): return OWNERS.get(uid,"?")

# ============================================================
# STATES
# ============================================================
(
    OWNER_MENU_ST, OWNER_FREE_TEXT,
    PHOTO_WAIT_RECEIPT, PHOTO_CONFIRM_RECEIPT,
    PHOTO_WAIT_INVOICE, PHOTO_CONFIRM_INVOICE,
    SUB_MENU_ST, SUB_SHIFT_SELECT, SUB_REGISTER_NAME,
    CONFIRM_ACTION,
    AI_CONFIRM_ST, AI_EDIT_ST,
) = range(12)

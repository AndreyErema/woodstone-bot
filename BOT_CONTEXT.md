# 🤖 TELEGRAM BOT — TECHNICAL CONTEXT

## Architecture
- **Runtime:** Python 3.13 on Railway.app (~$5/mo)
- **Library:** python-telegram-bot 21.7
- **Database:** Google Sheets (gspread library)
- **AI:** Claude API (Anthropic) for free-text parsing + receipt/invoice OCR
- **Photo storage:** Telegram channel (ID: -1003389113880)
- **Repo:** github.com/AndreyErema/woodstone-bot

## Environment Variables (Railway)
- `BOT_TOKEN` — Telegram bot token
- `SPREADSHEET_ID` — Google Sheets ID: 1578HzXUynjFcZ1iMC3kMfe0y3svl815WLT98RIEZrBQ
- `GOOGLE_CREDS_JSON` — Google service account JSON (full content)
- `ANTHROPIC_API_KEY` — Claude API key
- `RECEIPTS_CHANNEL_ID` — Telegram channel for receipt photos: -1003389113880
- `OWNERS_JSON` — Owner IDs: {"76341596":"Jeremy","139580832":"Serge","1173624685":"Kastet"}

## Google Sheets Structure

### Projects
| Col | Header | Notes |
|-----|--------|-------|
| A | Project ID | Auto-increment 0001, 0002... |
| B | PO | Short name e.g. "773 siding", "Falling Leaf deck" |
| C | Customer | Client name |
| D | Address | Full address |
| E | Description | Work description only (no contacts) |
| F | Price | Total project price |
| G | Status | New / In Progress / On Hold / Completed |
| H | Incom | Total received (auto-calculated or formula) |
| I | Expenses | Total expenses (auto-calculated or formula) |
| J | balance | Incom - Expenses |
| K | Date | Created date |
| L | Posted by | Who created |

### Payments
| Col | Header | Notes |
|-----|--------|-------|
| A | Project ID | |
| B | PO | Short project name |
| C | Amount | Payment amount |
| D | Data | Date/time |
| E | Posted by | Who recorded |
| F | Check | Link to invoice photo in Telegram channel |

### Expenses
| Col | Header | Notes |
|-----|--------|-------|
| A | Project ID | |
| B | PO | Short project name |
| C | Category | Materials / Subcontractor / Equipment Rental / Other |
| D | Amount | |
| E | Description | + link to receipt photo |
| F | Data | Date/time |
| G | Posted by | |

### Shifts
| Col | Header | Notes |
|-----|--------|-------|
| A | Data | Date |
| B | sub | Sub name |
| C | ID | Telegram ID |
| D | Project ID | |
| E | Start | Start datetime |
| F | Finish | End datetime |
| G | hours | Auto-calculated |
| H | PO | Project short name |

### Payroll
| Col | Header | Notes |
|-----|--------|-------|
| A | ID | (optional) |
| B | Sub | Sub name |
| C | Amount | Payment amount |
| D | Data | Date/time |
| E | Posted by | Who recorded, or "auto" for shift-based |

### Subs
| Col | Header | Notes |
|-----|--------|-------|
| A | Telegram ID | Auto-filled on registration |
| B | name | |
| C | Date added | |
| D | status | Pending / Approved / Rejected |
| E | Posted by | Who approved |
| F | Rate | $/hour |

### Journal (was "Обновления" / "Updates")
| Col | Header | Notes |
|-----|--------|-------|
| A | Project ID | |
| B | PO | |
| C | Description | Status updates, notes |
| D | Data | |
| E | Posted by | |

### Customers
| Col | Header | Notes |
|-----|--------|-------|
| A | Customer ID | Auto: C001, C002... |
| B | Name | |
| C | Address | |
| D | phone | |
| E | email | |
| F | projects (PO) | List of project POs |
| G | Communication | sms / messenger / email |
| H | Description | Notes about customer |
| I | Data | |
| J | Posted by | |

### Summary
- Weekly columns (Mon-Fri)
- Auto-updated on every payment/expense/payroll entry
- Shows totals, by-category, by-sub breakdowns

## Bot Roles

### Owners (OWNERS_JSON)
- Full menu + free text AI parsing
- Every text message → Claude API → parse intent → execute action
- Can: create projects, record payments/expenses, scan receipts/invoices, manage subs, view summaries, track own shifts
- Buttons as shortcuts, free text as primary input

### Subs (registered via bot, approved by owner)
- Button-only menu: Start shift / End shift
- Auto-registration: unknown user → enters name → owner gets approval request
- Auto-payroll: when shift ends, hours × rate = payment auto-recorded
- Rate set by owner via text: "Родя рейт 22" → $22/hr

## AI Features
- **Free text parsing:** Owner writes anything → Claude parses into structured action
- **Receipt OCR:** Photo of store receipt → Claude reads TOTAL amount
- **Invoice OCR:** Photo of client invoice (handwritten ok) → Claude reads amount
- **Project matching:** "773" finds "773 Central Heights", "Falling Leaf" finds "2090 Falling Leaf"
- **Sub matching:** "Родя" matches "Родя", "Дане" matches "Даня"

## Planned Features (TODO)
1. ☐ Split bot.py into modules (config, sheets, ai, owners, subs, etc.)
2. ☐ Timesheet pivot table (Google Sheets formulas) — by days × subs, filterable by period
3. ☐ Project Hours pivot table — by projects × subs, filterable by period
4. ☐ Google Sheets formulas for Incom/Expenses/balance in Projects (instead of bot writing)
5. ☐ PWA web app for subs without Telegram
6. ☐ Voice messages support
7. ☐ Communication column in Customers

## Known Issues
- Google Drive upload fails (storage quota on service account = 0) → using Telegram channel instead
- Old subs added without Telegram ID — need manual update in Subs sheet
- Some old data in Russian (Материалы etc.) — new entries in English

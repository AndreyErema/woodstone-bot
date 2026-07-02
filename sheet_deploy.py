"""
Structure/formula deployer for Timesheet and Project Hours.

Run explicitly via the /deploy_sheet command — the bot never touches these
two sheets any other way. All computation is done by Google Sheets formulas
(FILTER/UNIQUE/SORT/SEQUENCE/QUERY/BYROW/BYCOL/LAMBDA) referencing the period
cells (A2/B2) and the Shifts sheet directly, so once deployed the tables stay
live and up to date on their own — no further bot involvement, no risk of
the bot clearing anything the owner adds elsewhere on the sheet.

Re-running /deploy_sheet updates the formulas/labels but preserves whatever
period dates are already set in A2/B2 (only fills them with a default if
they're empty).
"""

from datetime import datetime, timedelta
import gspread

def _get_or_create(ss, title):
    try: return ss.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return ss.add_worksheet(title=title, rows=200, cols=26)

def _default_period():
    end = datetime.now()
    start = end - timedelta(days=30)
    return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")

def _parse_date_loose(s):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%y"):
        try: return datetime.strptime((s or "").strip(), fmt)
        except Exception: continue
    return None

def _existing_period(sh):
    """Only trust A2/B2 as a previously-set period if this sheet was already
    in our layout (A1 marker) AND both cells actually parse as dates —
    otherwise a sheet left over from an older layout (or a fresh one) would
    have its leftover data cells misread as period dates. Whatever format
    the cell happened to display in (e.g. ISO if some earlier bug/format
    left it that way), always re-normalize to MM/DD/YYYY before writing it
    back — Sheets' USER_ENTERED parser doesn't reliably recognize ISO dates
    under a US locale, and a plain-text date silently breaks every formula
    that does date arithmetic on it."""
    vals = sh.get_all_values()
    if not vals or not vals[0] or (vals[0][0] if vals[0] else "") != "Выбрать период":
        return "", ""
    a2 = vals[1][0] if len(vals) > 1 and len(vals[1]) > 0 else ""
    b2 = vals[1][1] if len(vals) > 1 and len(vals[1]) > 1 else ""
    da, db = _parse_date_loose(a2), _parse_date_loose(b2)
    if da and db:
        return da.strftime("%m/%d/%Y"), db.strftime("%m/%d/%Y")
    return "", ""

TIMESHEET_HEADER_FORMULA = '''=TRANSPOSE(QUERY(Shifts!A2:B1000,"select B where A>='"&TEXT($A$2,"yyyy-mm-dd")&"' and A<='"&TEXT($B$2,"yyyy-mm-dd")&"' and B<>'' group by B label B ''",0))'''
TIMESHEET_DATES_FORMULA = '''=SEQUENCE($B$2-$A$2+1,1,$A$2,1)'''
TIMESHEET_GRID_FORMULA = '''=BYROW(A9#,LAMBDA(d,BYCOL(B4#,LAMBDA(s,IF(SUMIFS(Shifts!$G:$G,Shifts!$A:$A,TEXT(d,"yyyy-mm-dd"),Shifts!$B:$B,s)=0,"Выходной",SUMIFS(Shifts!$G:$G,Shifts!$A:$A,TEXT(d,"yyyy-mm-dd"),Shifts!$B:$B,s))))))'''
TIMESHEET_TOTAL_FORMULA = '''=BYCOL(B9#,LAMBDA(col,SUMIF(col,"<>Выходной")))'''
TIMESHEET_WORKDAYS_FORMULA = '''=BYCOL(B9#,LAMBDA(col,COUNTIF(col,"<>Выходной")))'''

def deploy_timesheet_sheet(ss):
    """Сводная по сабам: период в A2/B2, колонки — только активные за период
    сабы, строка на каждый день периода (с "Выходной" на пустых днях),
    итого часов и количество отработанных дней."""
    sh = _get_or_create(ss, "Timesheet")
    a2, b2 = _existing_period(sh)
    ds, de = _default_period()

    sh.clear()
    if sh.col_count < 12: sh.resize(cols=12)
    if sh.row_count < 60: sh.resize(rows=60)

    sh.update("A1", [["Выбрать период"]], value_input_option="USER_ENTERED")
    sh.update("A2", [[a2 or ds, b2 or de]], value_input_option="USER_ENTERED")
    try: sh.format("A2:B2", {"numberFormat": {"type": "DATE", "pattern": "mm/dd/yyyy"}})
    except Exception: pass

    sh.update("A4", [["Сотрудник →"]], value_input_option="USER_ENTERED")
    sh.update("B4", [[TIMESHEET_HEADER_FORMULA]], value_input_option="USER_ENTERED")

    sh.update("A5", [["Итого часов"]], value_input_option="USER_ENTERED")
    sh.update("B5", [[TIMESHEET_TOTAL_FORMULA]], value_input_option="USER_ENTERED")

    sh.update("A6", [["Рабочих дней"]], value_input_option="USER_ENTERED")
    sh.update("B6", [[TIMESHEET_WORKDAYS_FORMULA]], value_input_option="USER_ENTERED")

    sh.update("A8", [["Дата ↓"]], value_input_option="USER_ENTERED")
    sh.update("A9", [[TIMESHEET_DATES_FORMULA]], value_input_option="USER_ENTERED")
    sh.update("B9", [[TIMESHEET_GRID_FORMULA]], value_input_option="USER_ENTERED")

    try: sh.format("A9:A60", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    except Exception: pass

PROJECT_HOURS_QUERY_FORMULA = '''=QUERY(Shifts!A2:H1000,"select H, sum(G) where A>='"&TEXT($A$2,"yyyy-mm-dd")&"' and A<='"&TEXT($B$2,"yyyy-mm-dd")&"' and H<>'' group by H label H 'Project', sum(G) 'Hours'",0)'''
PROJECT_HOURS_TOTAL_FORMULA = '''=SUMIFS(Shifts!$G:$G,Shifts!$A:$A,">="&TEXT($A$2,"yyyy-mm-dd"),Shifts!$A:$A,"<="&TEXT($B$2,"yyyy-mm-dd"))'''

def deploy_project_hours_sheet(ss):
    """Часы по проектам за период — только проекты с активностью в периоде,
    суммарные часы всех сабов вместе (для отчёта клиенту по почасовым
    проектам). Период задаётся в A2/B2."""
    sh = _get_or_create(ss, "Project Hours")
    a2, b2 = _existing_period(sh)
    ds, de = _default_period()

    sh.clear()
    if sh.col_count < 10: sh.resize(cols=10)
    if sh.row_count < 60: sh.resize(rows=60)

    sh.update("A1", [["Выбрать период"]], value_input_option="USER_ENTERED")
    sh.update("A2", [[a2 or ds, b2 or de]], value_input_option="USER_ENTERED")
    try: sh.format("A2:B2", {"numberFormat": {"type": "DATE", "pattern": "mm/dd/yyyy"}})
    except Exception: pass

    sh.update("D1", [["Итого часов за период"]], value_input_option="USER_ENTERED")
    sh.update("D2", [[PROJECT_HOURS_TOTAL_FORMULA]], value_input_option="USER_ENTERED")

    sh.update("A4", [[PROJECT_HOURS_QUERY_FORMULA]], value_input_option="USER_ENTERED")

def deploy_all(ss):
    deploy_timesheet_sheet(ss)
    deploy_project_hours_sheet(ss)

"""
Claude API wrapper: free-text intent parsing and receipt/invoice OCR.
"""

import json, base64, urllib.request
from datetime import datetime

from config import ANTHROPIC_API_KEY, log

def claude(prompt, image_b64=None, max_tokens=300):
    if not ANTHROPIC_API_KEY: return ""
    content = []
    if image_b64:
        content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":image_b64}})
    content.append({"type":"text","text":prompt})
    body=json.dumps({"model":"claude-sonnet-5","max_tokens":max_tokens,
        "messages":[{"role":"user","content":content}]})
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=body.encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"})
    try:
        with urllib.request.urlopen(req,timeout=60) as resp:
            res=json.loads(resp.read())
        return "".join(b.get("text","") for b in res.get("content",[]) if b.get("type")=="text")
    except Exception as e: log.error(f"Claude API: {e}"); return ""

def ai_parse(text, projects, subs, sender_name="", owners=None):
    """Send user message to Claude, get structured action."""
    proj_list = "\n".join(f"  ID:{p['id']} PO:{p['po']} Address:{p['addr']} Status:{p['status']}" for p in projects) or "  (none)"
    sub_list = "\n".join(f"  {s['name']} rate:${s['rate']}/hr" for s in subs) or "  (none)"
    owner_list = ", ".join(owners or ([sender_name] if sender_name else []))

    prompt = f"""You are a construction project management bot assistant. Parse the user's message and return a JSON action.

TODAY: {datetime.now().strftime("%Y-%m-%d")} ({datetime.now().strftime("%A")})
CURRENT TIME: {datetime.now().strftime("%H:%M")}
MESSAGE SENDER: {sender_name or "unknown"}
OWNERS (valid names for reminder assignment): {owner_list or "(none)"}

CURRENT PROJECTS:
{proj_list}

CURRENT SUBS:
{sub_list}

AVAILABLE ACTIONS (return exactly one):
- {{"action":"create_project","po":"short name","customer":"client name","phone":"client phone or empty","address":"full address","description":"work description","price":number_or_0}}
- {{"action":"payment","project_id":"N","amount":number,"note":"optional note"}}
- {{"action":"expense","project_id":"N","category":"Materials|Subcontractor|Equipment Rental|Other","amount":number,"description":"what"}}
- {{"action":"change_status","project_id":"N","status":"New|In Progress|On Hold|Completed"}}
- {{"action":"journal","project_id":"N","description":"update text"}}
- {{"action":"pay_sub","sub_name":"name","amount":number}}
- {{"action":"set_rate","sub_name":"name","rate":number}}
- {{"action":"record_hours","sub_name":"name","hours":number,"project_id":"N or empty","date":"YYYY-MM-DD or today"}}
- {{"action":"show_summary"}}
- {{"action":"show_project","project_id":"N"}}
- {{"action":"list_projects"}}
- {{"action":"add_customer","name":"","address":"","phone":"","email":"","communication":"sms/messenger/email"}}
- {{"action":"update_project","project_id":"N","field":"po|customer|address|description|price","value":"new value"}} (edit one field of an EXISTING project)
- {{"action":"update_customer","name":"customer name to match","field":"phone|email|communication|address","value":"new value"}} (edit one field of an EXISTING customer)
- {{"action":"scan_receipt"}} (user wants to scan a store receipt - expense)
- {{"action":"scan_invoice"}} (user wants to scan a client invoice/check - payment)
- {{"action":"create_reminder","date":"YYYY-MM-DD","time":"HH:MM or empty","assigned_to":["name",...],"description":"...","project_id":"N or empty"}} (user wants to be reminded of something, or wants someone reminded)
- {{"action":"update_reminder","match":"text fragment identifying which reminder (description/date/who)","field":"date|time|status","value":"new value, or 'Done' or 'Snoozed' for status"}} (user is rescheduling, cancelling, or marking an existing reminder done)
- {{"action":"unknown","reply":"your helpful response"}}

RULES:
- Match projects by ID, PO, address fragment, or street number. "773" matches "773 Central Heights". "Falling Leaf" matches "2090 Falling Leaf".
- Match subs by name (partial ok). "Родя" = "Родя", "Дане" = "Даня".
- create_project "po": if the user did NOT explicitly give a project name/label, derive it from the address as "house number + street or city" (e.g. "102 E 5th Watauga" for "102 E. 5th Avenue, Watauga TN 37694"). NEVER put the type/description of work (e.g. "landscaping and patio") into "po" — that belongs in "description" only.
- Any phone number in the message (digits, possibly with dashes) that belongs to the client goes into "phone", never into "description" or "po".
- If user mentions receiving money/check/deposit FROM client → payment. If user mentions spending/buying/purchasing → expense.
- If the message starts with the word "оплатили" (any case) → always expense (we paid for something), regardless of how the rest of the sentence sounds. Distinguish this from "оплатил клиент"/"получили оплату" and similar phrasing where money comes IN from the client — that's still payment.
- "закрой проект" or "close project" → status Completed.
- If the user is correcting/renaming a field of a project or customer that already exists (e.g. "PO пусть будет 671", "смени адрес на ...", "поменяй телефон клиента ..."), use update_project or update_customer — do NOT create a new project/customer for this.
- Currency: always USD.
- If the message contains a line starting with "CORRECTION:", it is a correction the user is making to the request right above it. Re-parse the whole thing as ONE corrected action (same action type as before unless the correction clearly changes it).
- create_reminder: resolve "мне"/"себе"/"me"/"myself" to the MESSAGE SENDER's name. If no recipient is named at all, default to the sender. Only use names from OWNERS for "assigned_to" (match partial/nicknames the same way as subs). A message can name multiple recipients ("напомни мне и Джереми" → both). If no explicit time is given, leave "time" empty (digest-only reminder). Resolve relative dates ("завтра"/"tomorrow", "в понедельник") to an actual YYYY-MM-DD using today's date context if given, otherwise your best guess.
- create_reminder also applies to statements about someone's obligation/schedule, not just explicit "напомни" requests — e.g. "Kastet должен быть на проекте 773 через 2 часа", "Даня будет на объекте в 3" → create_reminder with assigned_to from the named person(s), description summarizing the obligation (e.g. "быть на проекте").
- create_reminder relative time ("через N часов"/"через N минут"/"in N hours"): compute the actual clock time by adding N hours/minutes to CURRENT TIME above, rounded to the nearest 5 minutes. If this crosses midnight, roll the date to the next day. Always fill both "date" and "time" as real values in this case, never leave them as the literal phrase.
- If you can't determine the action, return unknown with a helpful reply.
- Return ONLY valid JSON, no markdown, no explanation.

USER MESSAGE: {text}"""

    result = claude(prompt)
    log.info(f"AI parse: {result}")
    try:
        # Clean potential markdown
        r = result.strip()
        if r.startswith("```"): r = r.split("\n",1)[1] if "\n" in r else r[3:]
        if r.endswith("```"): r = r[:-3]
        r = r.strip()
        return json.loads(r)
    except: return {"action":"unknown","reply":"Не понял. Попробуй переформулировать."}

def scan_amount(fp, receipt_type="receipt"):
    prompt = "This is a store receipt. Find the TOTAL amount paid (not subtotal, not tax)." if receipt_type=="receipt" else "This is a handwritten invoice/check from a client. Find the TOTAL amount."
    prompt += " Return ONLY the number. Example: 2004.14"
    try:
        with open(fp,"rb") as f: img=base64.b64encode(f.read()).decode()
        ans=claude(prompt, image_b64=img, max_tokens=50)
        return float(ans.strip().replace("$","").replace(",",""))
    except: return 0.0

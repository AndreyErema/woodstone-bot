"""
Claude API wrapper: free-text intent parsing and receipt/invoice OCR.
"""

import json, base64, urllib.request

from config import ANTHROPIC_API_KEY, log

def claude(prompt, image_b64=None, max_tokens=300):
    if not ANTHROPIC_API_KEY: return ""
    content = []
    if image_b64:
        content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":image_b64}})
    content.append({"type":"text","text":prompt})
    body=json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":max_tokens,
        "messages":[{"role":"user","content":content}]})
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=body.encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"})
    try:
        with urllib.request.urlopen(req,timeout=60) as resp:
            res=json.loads(resp.read())
        return "".join(b.get("text","") for b in res.get("content",[]) if b.get("type")=="text")
    except Exception as e: log.error(f"Claude API: {e}"); return ""

def ai_parse(text, projects, subs):
    """Send user message to Claude, get structured action."""
    proj_list = "\n".join(f"  ID:{p['id']} PO:{p['po']} Address:{p['addr']} Status:{p['status']}" for p in projects) or "  (none)"
    sub_list = "\n".join(f"  {s['name']} rate:${s['rate']}/hr" for s in subs) or "  (none)"

    prompt = f"""You are a construction project management bot assistant. Parse the user's message and return a JSON action.

CURRENT PROJECTS:
{proj_list}

CURRENT SUBS:
{sub_list}

AVAILABLE ACTIONS (return exactly one):
- {{"action":"create_project","po":"short name","customer":"client name","address":"full address","description":"work description","price":number_or_0}}
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
- {{"action":"scan_receipt"}} (user wants to scan a store receipt - expense)
- {{"action":"scan_invoice"}} (user wants to scan a client invoice/check - payment)
- {{"action":"unknown","reply":"your helpful response"}}

RULES:
- Match projects by ID, PO, address fragment, or street number. "773" matches "773 Central Heights". "Falling Leaf" matches "2090 Falling Leaf".
- Match subs by name (partial ok). "Родя" = "Родя", "Дане" = "Даня".
- If user mentions receiving money/check/deposit FROM client → payment. If user mentions spending/buying/purchasing → expense.
- "закрой проект" or "close project" → status Completed.
- Currency: always USD.
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

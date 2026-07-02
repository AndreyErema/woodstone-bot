"""
Reminders: background JobQueue notifications and Done/Snooze buttons.

One job (registered in bot.py via app.job_queue.run_repeating(interval=1800))
scans the Reminders sheet every 30 minutes and sends:
  - an evening digest (~19:00) of tomorrow's reminders, grouped per owner
  - a morning digest (~07:00) of today's reminders, grouped per owner
  - a 2h-before and a 1h-before ping for timed reminders (1h ping includes
    clickable Apple Maps / tel: links)
  - a "how did it go?" follow-up a few hours after the reminder's time, if
    no new Journal entry was added for the linked project since then

A "Sent Stages" column on each reminder row tracks which of the above have
already fired, so re-running the job every 30 minutes never double-sends.
"""

import html
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import log, OWNERS
from sheets import get_ss, pending_reminders, find_reminder_row, proj_addr

NAME_TO_UID = {v: k for k, v in OWNERS.items()}

FOLLOWUP_DELAY = timedelta(hours=3)

def _stages(s):
    return set(x.strip() for x in (s or "").split(",") if x.strip())

def _write_stages(ss, row, stage_set):
    ss.worksheet("Reminders").update(f"K{row}", [[", ".join(sorted(stage_set))]], value_input_option="USER_ENTERED")

def _reminder_dt(r):
    if not r["date"] or not r["time"]:
        return None
    try:
        return datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M")
    except Exception:
        return None

def _customer_contact(ss, pid, customer_name):
    """Look up phone for a project's linked customer by name (Customers.Name)."""
    if not customer_name:
        return ""
    try:
        for r in ss.worksheet("Customers").get_all_values()[1:]:
            if len(r) > 1 and r[1].strip().lower() == customer_name.strip().lower():
                return r[3] if len(r) > 3 else ""
    except Exception:
        pass
    return ""

def _done_snooze_kb(rid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Готово", callback_data=f"remdone_{rid}"),
        InlineKeyboardButton("⏰ Отложить на 1ч", callback_data=f"remsnooze_{rid}"),
    ]])

def _digest_line(r):
    time_part = f"{r['time']} — " if r["time"] else ""
    return f"• {time_part}{r['description']}"

def _detail_text(ss, r):
    """Full HTML-formatted ping text with Apple Maps / tel: links, for the 1h-before ping."""
    lines = [f"⏰ <b>Через час:</b> {html.escape(r['description'])}"]
    if r["project_id"]:
        addr = proj_addr(ss.worksheet("Projects"), r["project_id"])
        if addr:
            maps_url = "https://maps.apple.com/?q=" + addr.replace(" ", "+")
            lines.append(f"📍 <a href=\"{html.escape(maps_url)}\">{html.escape(addr)}</a>")
        phone = _customer_contact(ss, r["project_id"], r["customer"])
        if phone:
            digits = "".join(c for c in phone if c.isdigit() or c == "+")
            lines.append(f"📞 <a href=\"tel:{html.escape(digits)}\">{html.escape(phone)}</a>")
    return "\n".join(lines)

async def reminders_job(context):
    try:
        ss = get_ss()
    except Exception as e:
        log.error(f"reminders_job: DB error: {e}")
        return

    now = datetime.now()
    today_s = now.strftime("%Y-%m-%d")
    tomorrow_s = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        rems = pending_reminders(ss)
    except Exception as e:
        log.error(f"reminders_job: read error: {e}")
        return

    evening_bucket, morning_bucket = {}, {}

    dirty = {}  # row -> (original_stage_set, mutable_stage_set), flushed once per reminder at the end

    for r in rems:
        cur = _stages(r["sent_stages"])
        rid, row = r["id"], r["row"]
        dirty[row] = (set(cur), cur)

        if now.hour == 19 and now.minute < 30 and r["date"] == tomorrow_s and "evening" not in cur:
            for name in r["assigned_to"]:
                evening_bucket.setdefault(name, []).append(r)

        if now.hour == 7 and now.minute < 30 and r["date"] == today_s and "morning" not in cur:
            for name in r["assigned_to"]:
                morning_bucket.setdefault(name, []).append(r)

        dt = _reminder_dt(r)
        if dt:
            if "2h" not in cur and timedelta(0) <= dt - now <= timedelta(hours=2):
                for name in r["assigned_to"]:
                    uid = NAME_TO_UID.get(name)
                    if uid:
                        try:
                            await context.bot.send_message(uid, f"⏰ Через 2 часа: {r['description']}", reply_markup=_done_snooze_kb(rid))
                        except Exception as e:
                            log.error(f"reminders_job 2h send: {e}")
                cur.add("2h")

            if "1h" not in cur and timedelta(0) <= dt - now <= timedelta(hours=1):
                text = _detail_text(ss, r)
                for name in r["assigned_to"]:
                    uid = NAME_TO_UID.get(name)
                    if uid:
                        try:
                            await context.bot.send_message(uid, text, parse_mode="HTML", reply_markup=_done_snooze_kb(rid))
                        except Exception as e:
                            log.error(f"reminders_job 1h send: {e}")
                cur.add("1h")

            if "followup" not in cur and now - dt >= FOLLOWUP_DELAY:
                had_journal = False
                if r["project_id"]:
                    try:
                        for j in ss.worksheet("Journal").get_all_values()[1:]:
                            if str(j[0]) == str(r["project_id"]) and len(j) > 3 and j[3] >= r["date"]:
                                had_journal = True; break
                    except Exception:
                        pass
                if not had_journal:
                    for name in r["assigned_to"]:
                        uid = NAME_TO_UID.get(name)
                        if uid:
                            try:
                                await context.bot.send_message(uid, f"❓ Как прошло: {r['description']}?")
                            except Exception as e:
                                log.error(f"reminders_job followup send: {e}")
                cur.add("followup")

    for name, items in evening_bucket.items():
        uid = NAME_TO_UID.get(name)
        if not uid: continue
        text = "🌙 Напоминания на завтра:\n\n" + "\n".join(_digest_line(r) for r in items)
        try: await context.bot.send_message(uid, text)
        except Exception as e: log.error(f"reminders_job evening send: {e}")
        for r in items: dirty[r["row"]][1].add("evening")

    for name, items in morning_bucket.items():
        uid = NAME_TO_UID.get(name)
        if not uid: continue
        text = "☀️ Напоминания на сегодня:\n\n" + "\n".join(_digest_line(r) for r in items)
        try: await context.bot.send_message(uid, text)
        except Exception as e: log.error(f"reminders_job morning send: {e}")
        for r in items: dirty[r["row"]][1].add("morning")

    for row, (original, current) in dirty.items():
        if current != original:
            try: _write_stages(ss, row, current)
            except Exception as e: log.error(f"reminders_job stage write: {e}")

async def reminder_button_cb(update, ctx):
    q = update.callback_query; await q.answer()
    action, rid = q.data.split("_", 1)
    try:
        ss = get_ss()
        row = find_reminder_row(ss, rid)
        if row < 1:
            await q.edit_message_text("❌ Напоминание не найдено."); return
        rs = ss.worksheet("Reminders")
        if action == "remdone":
            rs.update(f"H{row}", [["Done"]], value_input_option="USER_ENTERED")
            await q.edit_message_text("✅ Отмечено как готово.")
        elif action == "remsnooze":
            vals = rs.row_values(row)
            date_s = vals[1] if len(vals) > 1 else ""
            time_s = vals[2] if len(vals) > 2 else ""
            try:
                dt = datetime.strptime(f"{date_s} {time_s or '00:00'}", "%Y-%m-%d %H:%M") + timedelta(hours=1)
            except Exception:
                dt = datetime.now() + timedelta(hours=1)
            rs.update(f"B{row}", [[dt.strftime("%Y-%m-%d")]], value_input_option="USER_ENTERED")
            rs.update(f"C{row}", [[dt.strftime("%H:%M")]], value_input_option="USER_ENTERED")
            rs.update(f"K{row}", [[""]], value_input_option="USER_ENTERED")
            await q.edit_message_text(f"⏰ Отложено на 1ч → {dt.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        log.error(f"reminder_button_cb: {e}")
        await q.edit_message_text("❌ Ошибка.")

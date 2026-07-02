"""
Owner shift start/end (project time tracking).
"""

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import log, owner_name, OWNER_MENU_ST, CONFIRM_ACTION
from keyboards import OWNER_KB
from sheets import get_ss, active_shift, active_projects, proj_po, update_timesheet_sheet, update_project_hours_sheet

async def owner_shift_start(update, ctx):
    uid=update.effective_user.id
    try:
        ss=get_ss(); a=active_shift(ss,uid)
        if a: await update.message.reply_text(f"⚠️ Already on shift!\n📍 {a['po']}\n🕐 {a['start']}", reply_markup=OWNER_KB); return OWNER_MENU_ST
        projs=active_projects(ss.worksheet("Projects"))
    except: await update.message.reply_text("❌", reply_markup=OWNER_KB); return OWNER_MENU_ST
    if not projs: await update.message.reply_text("📭 No projects.", reply_markup=OWNER_KB); return OWNER_MENU_ST
    btns=[[InlineKeyboardButton(f"{p['id']} — {p['po']}",callback_data=f"oshift_{p['id']}")] for p in projs]
    btns.append([InlineKeyboardButton("❌",callback_data="cancel")])
    await update.message.reply_text("📍 Project:", reply_markup=InlineKeyboardMarkup(btns))
    return CONFIRM_ACTION

async def oshift_cb(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cancel": await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST
    pid=q.data.replace("oshift_",""); uid=q.from_user.id; name=owner_name(uid)
    now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss=get_ss(); po=proj_po(ss.worksheet("Projects"),pid)
        ss.worksheet("Shifts").append_row([now_s[:10],name,str(uid),pid,now_s,"","",po], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"🟢 Shift started!\n👤 {name}\n📍 {pid} — {po}\n🕐 {now_s}")
    except: await q.edit_message_text("❌")
    await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST

async def owner_shift_end(update, ctx):
    uid=update.effective_user.id; name=owner_name(uid)
    try:
        ss=get_ss(); a=active_shift(ss,uid)
        if not a: await update.message.reply_text("❌ No active shift.", reply_markup=OWNER_KB); return OWNER_MENU_ST
        sh=ss.worksheet("Shifts"); now=datetime.now(); now_s=now.strftime("%Y-%m-%d %H:%M")
        start=datetime.strptime(a["start"],"%Y-%m-%d %H:%M"); hrs=round((now-start).total_seconds()/3600,2)
        sh.update(f"F{a['row']}",[[now_s]]); sh.update(f"G{a['row']}",[[hrs]])
        update_timesheet_sheet(ss); update_project_hours_sheet(ss)
        await update.message.reply_text(f"🔴 Shift ended!\n👤 {name}\n📍 {a['po']}\n⏱ {hrs}h", reply_markup=OWNER_KB)
    except Exception as e:
        log.error(f"Shift end: {e}"); await update.message.reply_text("❌", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

"""
Wood & Stone Construction LLC — Telegram Bot v6
AI-driven: owners type free text, Claude parses intent.
Subs: button-based shift tracking.

Entry point — wires up the Application and ConversationHandler.
Business logic lives in config.py, sheets.py, ai.py, keyboards.py,
and the handlers_*.py modules.
"""

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters

from config import (
    BOT_TOKEN, log,
    OWNER_MENU_ST, OWNER_FREE_TEXT,
    PHOTO_WAIT_RECEIPT, PHOTO_CONFIRM_RECEIPT,
    PHOTO_WAIT_INVOICE, PHOTO_CONFIRM_INVOICE,
    SUB_MENU_ST, SUB_SHIFT_SELECT, SUB_REGISTER_NAME,
    CONFIRM_ACTION, AI_CONFIRM_ST, AI_EDIT_ST,
)
from sheets import get_ss, init
from handlers_owner import start, cancel_cmd, owner_handler, free_text_handler, ai_confirm_cb, ai_edit_text, deploy_sheet_cmd
from handlers_scan import (
    receipt_proj_select, invoice_proj_select, photo_received,
    scan_confirm, scan_manual_amt, scan_category_cb,
)
from handlers_shifts import oshift_cb
from handlers_subs import sub_register, approve_sub, sub_handler, sub_shift_cb
from handlers_reminders import reminders_job, reminder_button_cb


def main():
    try: ss=get_ss(); init(ss); log.info("✅ Sheets OK")
    except Exception as e: log.error(f"Init: {e}")

    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(approve_sub, pattern="^(approve_|reject_)"))
    app.add_handler(CallbackQueryHandler(reminder_button_cb, pattern="^(remdone_|remsnooze_)"))
    app.add_handler(CommandHandler("deploy_sheet", deploy_sheet_cmd))
    if app.job_queue:
        app.job_queue.run_repeating(reminders_job, interval=1800, first=10)

    ch=ConversationHandler(
        entry_points=[CommandHandler("start",start)],
        states={
            OWNER_MENU_ST:[MessageHandler(filters.TEXT & ~filters.COMMAND, owner_handler), MessageHandler(filters.PHOTO, photo_received)],
            OWNER_FREE_TEXT:[MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler)],
            PHOTO_WAIT_RECEIPT:[CallbackQueryHandler(receipt_proj_select,pattern="^proj_"),CallbackQueryHandler(lambda u,c:scan_confirm(u,c),pattern="^cancel$"),MessageHandler(filters.PHOTO,photo_received)],
            PHOTO_WAIT_INVOICE:[CallbackQueryHandler(invoice_proj_select,pattern="^proj_"),CallbackQueryHandler(lambda u,c:scan_confirm(u,c),pattern="^cancel$"),MessageHandler(filters.PHOTO,photo_received)],
            PHOTO_CONFIRM_RECEIPT:[CallbackQueryHandler(scan_confirm,pattern="^scanc_"),CallbackQueryHandler(scan_category_cb,pattern="^scancat_"),CallbackQueryHandler(scan_confirm,pattern="^cancel$"),MessageHandler(filters.TEXT & ~filters.COMMAND,scan_manual_amt)],
            PHOTO_CONFIRM_INVOICE:[CallbackQueryHandler(scan_confirm,pattern="^scanc_"),CallbackQueryHandler(scan_confirm,pattern="^cancel$"),MessageHandler(filters.TEXT & ~filters.COMMAND,scan_manual_amt)],
            SUB_MENU_ST:[MessageHandler(filters.TEXT & ~filters.COMMAND,sub_handler)],
            SUB_SHIFT_SELECT:[CallbackQueryHandler(sub_shift_cb,pattern="^sshift_"),CallbackQueryHandler(sub_shift_cb,pattern="^scancel$")],
            SUB_REGISTER_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,sub_register)],
            CONFIRM_ACTION:[CallbackQueryHandler(oshift_cb,pattern="^oshift_"),CallbackQueryHandler(oshift_cb,pattern="^cancel$")],
            AI_CONFIRM_ST:[CallbackQueryHandler(ai_confirm_cb,pattern="^(aiok|aiedit|aicancel)$")],
            AI_EDIT_ST:[MessageHandler(filters.TEXT & ~filters.COMMAND,ai_edit_text)],
        },
        fallbacks=[CommandHandler("cancel",cancel_cmd),CommandHandler("start",start)],
    )
    app.add_handler(ch)
    log.info("🚀 Bot started!")
    app.run_polling()

if __name__=="__main__": main()

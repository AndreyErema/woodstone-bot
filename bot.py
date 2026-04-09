"""
Wood & Stone Construction LLC — Telegram Project Tracker Bot v2
Многолистовая структура Google Sheets:
  - Лист "Проекты" — сводка (1 строка = 1 проект)
  - Лист "Платежи" — все платежи
  - Лист "Расходы" — все расходы
  - Лист "Обновления" — описания статуса
"""

import os
import json
import logging
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters
)
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID_HERE")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")

ALLOWED_USERS = {
    76341596: "Jeremy",
    # 987654321: "Partner 2",
    # 111222333: "Partner 3",
    # 444555666: "Partner 4",
}

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# СОСТОЯНИЯ РАЗГОВОРА
# ============================================================
(
    MAIN_MENU,
    NEW_PROJECT_ADDRESS,
    NEW_PROJECT_DESCRIPTION,
    NEW_PROJECT_PRICE,
    PAYMENT_SELECT_PROJECT,
    PAYMENT_AMOUNT,
    EXPENSE_SELECT_PROJECT,
    EXPENSE_CATEGORY,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    STATUS_DESC_SELECT_PROJECT,
    STATUS_DESC_TEXT,
    CHANGE_STATUS_SELECT_PROJECT,
    CHANGE_STATUS_VALUE,
    VIEW_STATUS_SELECT_PROJECT,
) = range(15)

# ============================================================
# GOOGLE SHEETS — ПОДКЛЮЧЕНИЕ
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_creds():
    """Получить credentials для Google API."""
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if creds_json:
        creds_info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
    return creds


def get_spreadsheet():
    """Подключиться к Google Sheets и вернуть spreadsheet."""
    creds = get_google_creds()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet


def get_or_create_sheet(spreadsheet, title, headers):
    """Получить лист или создать с заголовками."""
    try:
        sheet = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        sheet.append_row(headers, value_input_option="USER_ENTERED")
    return sheet


def init_sheets(spreadsheet):
    """Инициализировать все листы."""
    projects = get_or_create_sheet(spreadsheet, "Проекты", [
        "Project ID", "Адрес", "Описание", "Цена",
        "Статус", "Получено", "Расходы", "Баланс",
        "Дата создания", "Создал"
    ])
    payments = get_or_create_sheet(spreadsheet, "Платежи", [
        "Project ID", "Адрес", "Сумма", "Дата", "Кто записал"
    ])
    expenses = get_or_create_sheet(spreadsheet, "Расходы", [
        "Project ID", "Адрес", "Категория", "Сумма",
        "Описание", "Дата", "Кто записал"
    ])
    updates = get_or_create_sheet(spreadsheet, "Обновления", [
        "Project ID", "Адрес", "Текст", "Дата", "Кто записал"
    ])
    return projects, payments, expenses, updates


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def check_access(update: Update) -> bool:
    user_id = update.effective_user.id
    return user_id in ALLOWED_USERS


def get_user_name(update: Update) -> str:
    user_id = update.effective_user.id
    return ALLOWED_USERS.get(user_id, "Unknown")


def get_user_name_by_id(user_id: int) -> str:
    return ALLOWED_USERS.get(user_id, "Unknown")


def get_next_project_id(projects_sheet) -> str:
    """Получить следующий ID проекта (0001, 0002, ...)."""
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return "0001"
    max_id = 0
    for row in all_records[1:]:
        try:
            pid = int(row[0])
            if pid > max_id:
                max_id = pid
        except (ValueError, IndexError):
            continue
    return str(max_id + 1).zfill(4)


def get_active_projects(projects_sheet) -> list:
    """Получить список активных проектов (не Завершён)."""
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return []
    projects = []
    for row in all_records[1:]:
        try:
            status = row[4] if len(row) > 4 else "Новый"
            if status != "Завершён":
                projects.append({
                    "id": row[0],
                    "address": row[1],
                    "status": status,
                })
        except (IndexError, ValueError):
            continue
    return projects


def get_all_projects(projects_sheet) -> list:
    """Получить ВСЕ проекты."""
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return []
    projects = []
    for row in all_records[1:]:
        try:
            projects.append({
                "id": row[0],
                "address": row[1],
                "status": row[4] if len(row) > 4 else "Новый",
            })
        except (IndexError, ValueError):
            continue
    return projects


def find_project_row(projects_sheet, project_id: str) -> int:
    """Найти номер строки проекта в листе Проекты (1-indexed)."""
    all_records = projects_sheet.get_all_values()
    for i, row in enumerate(all_records):
        if row[0] == project_id:
            return i + 1
    return -1


def update_project_totals(spreadsheet, project_id: str):
    """Пересчитать Получено, Расходы, Баланс в листе Проекты."""
    projects_sheet = spreadsheet.worksheet("Проекты")
    payments_sheet = spreadsheet.worksheet("Платежи")
    expenses_sheet = spreadsheet.worksheet("Расходы")

    total_paid = 0
    payments = payments_sheet.get_all_values()
    for row in payments[1:]:
        if row[0] == project_id:
            try:
                total_paid += float(row[2])
            except (ValueError, IndexError):
                continue

    total_expenses = 0
    expenses = expenses_sheet.get_all_values()
    for row in expenses[1:]:
        if row[0] == project_id:
            try:
                total_expenses += float(row[3])
            except (ValueError, IndexError):
                continue

    row_num = find_project_row(projects_sheet, project_id)
    if row_num == -1:
        return

    balance = total_paid - total_expenses

    # Обновляем: Получено (F), Расходы (G), Баланс (H)
    projects_sheet.update(f"F{row_num}", [[total_paid]])
    projects_sheet.update(f"G{row_num}", [[total_expenses]])
    projects_sheet.update(f"H{row_num}", [[balance]])


def get_project_address(projects_sheet, project_id: str) -> str:
    """Получить адрес проекта по ID."""
    all_records = projects_sheet.get_all_values()
    for row in all_records[1:]:
        if row[0] == project_id:
            return row[1]
    return ""


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📋 Новый проект", "💰 Записать платёж"],
        ["💸 Записать расход", "📝 Добавить описание"],
        ["🔄 Изменить статус", "📊 Статус проекта"],
        ["📁 Архив"],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context) -> int:
    if not check_access(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return ConversationHandler.END

    name = get_user_name(update)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        f"🏗 Wood & Stone Construction — Project Tracker\n\n"
        f"Выбери действие:",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return MAIN_MENU


async def main_menu_handler(update: Update, context) -> int:
    if not check_access(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END

    text = update.message.text

    if text == "📋 Новый проект":
        await update.message.reply_text(
            "📋 Создаём новый проект.\n\nВведи адрес проекта:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_PROJECT_ADDRESS

    elif text == "💰 Записать платёж":
        return await show_project_list(update, context, PAYMENT_SELECT_PROJECT)

    elif text == "💸 Записать расход":
        return await show_project_list(update, context, EXPENSE_SELECT_PROJECT)

    elif text == "📝 Добавить описание":
        return await show_project_list(update, context, STATUS_DESC_SELECT_PROJECT)

    elif text == "🔄 Изменить статус":
        return await show_project_list(update, context, CHANGE_STATUS_SELECT_PROJECT)

    elif text == "📊 Статус проекта":
        return await show_project_list(update, context, VIEW_STATUS_SELECT_PROJECT, include_all=True)

    elif text == "📁 Архив":
        return await show_archive(update, context)

    return MAIN_MENU


async def show_project_list(update, context, next_state, include_all=False):
    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        projects = get_all_projects(projects_sheet) if include_all else get_active_projects(projects_sheet)
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        await update.message.reply_text(
            "❌ Ошибка подключения к таблице.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return MAIN_MENU

    if not projects:
        await update.message.reply_text(
            "📭 Нет активных проектов. Создай новый!",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return MAIN_MENU

    buttons = []
    for p in projects:
        label = f"{p['id']} — {p['address']}"
        if include_all:
            label += f" [{p['status']}]"
        buttons.append([InlineKeyboardButton(label, callback_data=f"proj_{p['id']}")])

    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    await update.message.reply_text(
        "Выбери проект:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return next_state


async def show_archive(update, context):
    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        all_projects = get_all_projects(projects_sheet)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    archived = [p for p in all_projects if p["status"] == "Завершён"]

    if not archived:
        await update.message.reply_text(
            "📁 Архив пуст.", reply_markup=MAIN_MENU_KEYBOARD
        )
        return MAIN_MENU

    text = "📁 *Архив проектов:*\n\n"
    for p in archived:
        text += f"• {p['id']} — {p['address']}\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


async def cancel_callback(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено.")
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# НОВЫЙ ПРОЕКТ
# ============================================================

async def new_project_address(update: Update, context) -> int:
    context.user_data["new_address"] = update.message.text
    await update.message.reply_text("📝 Введи описание проекта:")
    return NEW_PROJECT_DESCRIPTION


async def new_project_description(update: Update, context) -> int:
    context.user_data["new_description"] = update.message.text
    await update.message.reply_text("💵 Введи цену проекта (число):")
    return NEW_PROJECT_PRICE


async def new_project_price(update: Update, context) -> int:
    try:
        price = float(update.message.text.replace(",", "").replace("$", ""))
        context.user_data["new_price"] = price
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 15000")
        return NEW_PROJECT_PRICE

    user_name = get_user_name(update)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet, _, _, _ = init_sheets(spreadsheet)
        project_id = get_next_project_id(projects_sheet)

        # Проекты: ID | Адрес | Описание | Цена | Статус | Получено | Расходы | Баланс | Дата | Создал
        row = [
            project_id,
            context.user_data["new_address"],
            context.user_data["new_description"],
            price,
            "Новый",
            0,
            0,
            0,
            now,
            user_name,
        ]
        projects_sheet.append_row(row, value_input_option="USER_ENTERED")

        await update.message.reply_text(
            f"✅ Проект создан!\n\n"
            f"🆔 ID: {project_id}\n"
            f"📍 Адрес: {context.user_data['new_address']}\n"
            f"💵 Цена: ${price:,.2f}\n"
            f"👤 Создал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        await update.message.reply_text(
            "❌ Ошибка при создании проекта.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )

    for key in ["new_address", "new_description", "new_price"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ ПЛАТЁЖ
# ============================================================

async def payment_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["payment_project_id"] = project_id
    await query.edit_message_text(f"💰 Проект {project_id}.\n\nВведи сумму платежа:")
    return PAYMENT_AMOUNT


async def payment_amount(update: Update, context) -> int:
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 5000")
        return PAYMENT_AMOUNT

    project_id = context.user_data["payment_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        payments_sheet = spreadsheet.worksheet("Платежи")
        address = get_project_address(projects_sheet, project_id)

        payments_sheet.append_row(
            [project_id, address, amount, now, user_name],
            value_input_option="USER_ENTERED"
        )

        update_project_totals(spreadsheet, project_id)

        await update.message.reply_text(
            f"✅ Платёж записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"💵 Сумма: ${amount:,.2f}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error recording payment: {e}")
        await update.message.reply_text(
            "❌ Ошибка при записи платежа.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )

    context.user_data.pop("payment_project_id", None)
    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ РАСХОД
# ============================================================

EXPENSE_CATEGORIES = ["Материалы", "Субподрядчик", "Аренда оборудования", "Прочее"]


async def expense_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["expense_project_id"] = project_id

    buttons = []
    for cat in EXPENSE_CATEGORIES:
        buttons.append([InlineKeyboardButton(cat, callback_data=f"expcat_{cat}")])

    await query.edit_message_text(
        f"💸 Проект {project_id}.\n\nВыбери категорию расхода:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return EXPENSE_CATEGORY


async def expense_category(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data.replace("expcat_", "")
    context.user_data["expense_category"] = category
    await query.edit_message_text(f"💸 Категория: {category}.\n\nВведи сумму расхода:")
    return EXPENSE_AMOUNT


async def expense_amount(update: Update, context) -> int:
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 1200")
        return EXPENSE_AMOUNT

    context.user_data["expense_amount"] = amount
    await update.message.reply_text(
        "📝 Добавь описание расхода (или отправь «-» чтобы пропустить):"
    )
    return EXPENSE_DESCRIPTION


async def expense_description(update: Update, context) -> int:
    description = update.message.text if update.message.text != "-" else ""
    project_id = context.user_data["expense_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        address = get_project_address(projects_sheet, project_id)

        expenses_sheet.append_row(
            [project_id, address, context.user_data["expense_category"],
             context.user_data["expense_amount"], description, now, user_name],
            value_input_option="USER_ENTERED"
        )

        update_project_totals(spreadsheet, project_id)

        await update.message.reply_text(
            f"✅ Расход записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"📂 Категория: {context.user_data['expense_category']}\n"
            f"💸 Сумма: ${context.user_data['expense_amount']:,.2f}\n"
            f"📝 Описание: {description or '—'}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error recording expense: {e}")
        await update.message.reply_text(
            "❌ Ошибка при записи расхода.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )

    for key in ["expense_project_id", "expense_category", "expense_amount"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# ДОБАВИТЬ ОПИСАНИЕ
# ============================================================

async def status_desc_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["desc_project_id"] = project_id
    await query.edit_message_text(
        f"📝 Проект {project_id}.\n\nВведи описание текущего состояния:"
    )
    return STATUS_DESC_TEXT


async def status_desc_text(update: Update, context) -> int:
    description = update.message.text
    project_id = context.user_data["desc_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        updates_sheet = spreadsheet.worksheet("Обновления")
        address = get_project_address(projects_sheet, project_id)

        updates_sheet.append_row(
            [project_id, address, description, now, user_name],
            value_input_option="USER_ENTERED"
        )

        await update.message.reply_text(
            f"✅ Описание добавлено!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"📝 {description}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error recording update: {e}")
        await update.message.reply_text(
            "❌ Ошибка при записи описания.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )

    context.user_data.pop("desc_project_id", None)
    return MAIN_MENU


# ============================================================
# ИЗМЕНИТЬ СТАТУС
# ============================================================

STATUS_OPTIONS = ["Новый", "В работе", "Приостановлен", "Завершён"]


async def change_status_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["status_project_id"] = project_id

    buttons = []
    for status in STATUS_OPTIONS:
        buttons.append([InlineKeyboardButton(status, callback_data=f"status_{status}")])

    await query.edit_message_text(
        f"🔄 Проект {project_id}.\n\nВыбери новый статус:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CHANGE_STATUS_VALUE


async def change_status_value(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    new_status = query.data.replace("status_", "")
    project_id = context.user_data["status_project_id"]
    user_name = get_user_name_by_id(query.from_user.id)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")

        row_num = find_project_row(projects_sheet, project_id)
        if row_num != -1:
            projects_sheet.update(f"E{row_num}", [[new_status]])

        updates_sheet = spreadsheet.worksheet("Обновления")
        address = get_project_address(projects_sheet, project_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        updates_sheet.append_row(
            [project_id, address, f"Статус изменён → {new_status}", now, user_name],
            value_input_option="USER_ENTERED"
        )

        await query.edit_message_text(
            f"✅ Статус обновлён!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"🔄 Новый статус: {new_status}\n"
            f"👤 Изменил: {user_name}"
        )
    except Exception as e:
        logger.error(f"Error changing status: {e}")
        await query.edit_message_text("❌ Ошибка при смене статуса.")

    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data.pop("status_project_id", None)
    return MAIN_MENU


# ============================================================
# СТАТУС ПРОЕКТА — СВОДКА
# ============================================================

async def view_status_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        payments_sheet = spreadsheet.worksheet("Платежи")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        updates_sheet = spreadsheet.worksheet("Обновления")

        row_num = find_project_row(projects_sheet, project_id)
        if row_num == -1:
            await query.edit_message_text("❌ Проект не найден.")
            return MAIN_MENU

        proj = projects_sheet.row_values(row_num)
        address = proj[1] if len(proj) > 1 else ""
        description = proj[2] if len(proj) > 2 else ""
        price = float(proj[3]) if len(proj) > 3 and proj[3] else 0
        status = proj[4] if len(proj) > 4 else "Новый"
        total_paid = float(proj[5]) if len(proj) > 5 and proj[5] else 0
        total_expenses = float(proj[6]) if len(proj) > 6 and proj[6] else 0
        balance = float(proj[7]) if len(proj) > 7 and proj[7] else 0
        remaining = price - total_paid

        text = (
            f"📊 *Проект {project_id}*\n\n"
            f"📍 Адрес: {address}\n"
            f"📝 Описание: {description}\n"
            f"🔄 Статус: {status}\n\n"
            f"💵 Цена проекта: ${price:,.2f}\n"
            f"✅ Получено: ${total_paid:,.2f}\n"
            f"💸 Расходы: ${total_expenses:,.2f}\n"
            f"📈 Остаток от клиента: ${remaining:,.2f}\n"
            f"💰 Баланс: ${balance:,.2f}\n"
        )

        # Последние платежи
        all_payments = payments_sheet.get_all_values()
        proj_payments = [r for r in all_payments[1:] if r[0] == project_id]
        if proj_payments:
            text += "\n*Платежи:*\n"
            for p in proj_payments[-5:]:
                text += f"  • {p[3]} — ${float(p[2]):,.2f} ({p[4]})\n"

        # Последние расходы
        all_expenses = expenses_sheet.get_all_values()
        proj_expenses = [r for r in all_expenses[1:] if r[0] == project_id]
        if proj_expenses:
            text += "\n*Расходы:*\n"
            for e in proj_expenses[-5:]:
                desc = f" — {e[4]}" if e[4] else ""
                text += f"  • {e[5]} — ${float(e[3]):,.2f} [{e[2]}]{desc} ({e[6]})\n"

        # Последние обновления
        all_updates = updates_sheet.get_all_values()
        proj_updates = [r for r in all_updates[1:] if r[0] == project_id]
        if proj_updates:
            text += "\n*Обновления:*\n"
            for u in proj_updates[-5:]:
                text += f"  • {u[3]} — {u[2]} ({u[4]})\n"

        await query.edit_message_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error viewing status: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке статуса.")

    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# ОТМЕНА
# ============================================================

async def cancel(update: Update, context) -> int:
    await update.message.reply_text(
        "❌ Действие отменено.", reply_markup=MAIN_MENU_KEYBOARD
    )
    return MAIN_MENU


# ============================================================
# ЗАПУСК БОТА
# ============================================================

def main():
    """Запуск бота."""
    try:
        spreadsheet = get_spreadsheet()
        init_sheets(spreadsheet)
        logger.info("✅ Листы Google Sheets инициализированы.")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Google Sheets: {e}")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler),
            ],
            NEW_PROJECT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_address),
            ],
            NEW_PROJECT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_description),
            ],
            NEW_PROJECT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_price),
            ],
            PAYMENT_SELECT_PROJECT: [
                CallbackQueryHandler(payment_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            PAYMENT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount),
            ],
            EXPENSE_SELECT_PROJECT: [
                CallbackQueryHandler(expense_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            EXPENSE_CATEGORY: [
                CallbackQueryHandler(expense_category, pattern="^expcat_"),
            ],
            EXPENSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount),
            ],
            EXPENSE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_description),
            ],
            STATUS_DESC_SELECT_PROJECT: [
                CallbackQueryHandler(status_desc_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            STATUS_DESC_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, status_desc_text),
            ],
            CHANGE_STATUS_SELECT_PROJECT: [
                CallbackQueryHandler(change_status_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            CHANGE_STATUS_VALUE: [
                CallbackQueryHandler(change_status_value, pattern="^status_"),
            ],
            VIEW_STATUS_SELECT_PROJECT: [
                CallbackQueryHandler(view_status_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)

    logger.info("🚀 Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()

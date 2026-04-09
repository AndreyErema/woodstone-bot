"""
Wood & Stone Construction LLC — Telegram Project Tracker Bot
Бот для управления строительными проектами через Telegram.
Данные сохраняются в Google Sheets, фото — в Google Drive.
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
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ============================================================
# НАСТРОЙКИ — ЗАПОЛНИ ПЕРЕД ЗАПУСКОМ
# ============================================================

# Telegram Bot Token (получишь от @BotFather)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Google Sheets ID (из URL таблицы)
# Пример: https://docs.google.com/spreadsheets/d/ЭТОТ_ID_СЮДА/edit
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID_HERE")

# Google Drive Folder ID (из URL папки)
# Пример: https://drive.google.com/drive/folders/ЭТОТ_ID_СЮДА
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID_HERE")

# Путь к файлу сервисного аккаунта Google
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")

# Telegram ID партнёров (узнать можно через @userinfobot)
ALLOWED_USERS = {
    76341596: "Jeremy",
    # Добавь партнёров позже:
    # 987654321: "Partner 2",
    # 111222333: "Partner 3",
    # 444555666: "Partner 4",
}

PARTNER_NAMES = list(set(ALLOWED_USERS.values()))

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# СОСТОЯНИЯ РАЗГОВОРА (ConversationHandler)
# ============================================================
(
    MAIN_MENU,
    # Новый проект
    NEW_PROJECT_ADDRESS,
    NEW_PROJECT_DESCRIPTION,
    NEW_PROJECT_PRICE,
    NEW_PROJECT_OWNER,
    # Платёж
    PAYMENT_SELECT_PROJECT,
    PAYMENT_AMOUNT,
    # Расход
    EXPENSE_SELECT_PROJECT,
    EXPENSE_CATEGORY,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    # Фото
    PHOTO_SELECT_PROJECT,
    PHOTO_UPLOAD,
    # Описание статуса
    STATUS_DESC_SELECT_PROJECT,
    STATUS_DESC_TEXT,
    # Изменить статус
    CHANGE_STATUS_SELECT_PROJECT,
    CHANGE_STATUS_VALUE,
    # Статус проекта
    VIEW_STATUS_SELECT_PROJECT,
) = range(18)

# ============================================================
# GOOGLE SHEETS & DRIVE — ПОДКЛЮЧЕНИЕ
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_creds():
    """Получить credentials для Google API."""
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
    return creds


def get_sheet():
    """Подключиться к Google Sheets."""
    creds = get_google_creds()
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet


def get_drive_service():
    """Подключиться к Google Drive."""
    creds = get_google_creds()
    service = build("drive", "v3", credentials=creds)
    return service


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def check_access(update: Update) -> bool:
    """Проверить, есть ли у пользователя доступ."""
    user_id = update.effective_user.id
    return user_id in ALLOWED_USERS


def get_user_name(update: Update) -> str:
    """Получить имя пользователя."""
    user_id = update.effective_user.id
    return ALLOWED_USERS.get(user_id, "Unknown")


def get_next_project_id(sheet) -> str:
    """Получить следующий ID проекта (0001, 0002, ...)."""
    all_records = sheet.get_all_values()
    if len(all_records) <= 1:  # только заголовок
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


def get_active_projects(sheet) -> list:
    """Получить список активных проектов (не 'Завершён')."""
    all_records = sheet.get_all_values()
    if len(all_records) <= 1:
        return []

    projects = {}
    for row in all_records[1:]:
        try:
            pid = row[0]
            record_type = row[1]
            if record_type == "Новый проект":
                address = row[3]
                status = row[7] if len(row) > 7 else "Новый"
                projects[pid] = {
                    "id": pid,
                    "address": address,
                    "status": status,
                }
            # Обновить статус если менялся
            if record_type == "Статус" and pid in projects:
                projects[pid]["status"] = row[7] if len(row) > 7 else projects[pid]["status"]
        except (IndexError, ValueError):
            continue

    # Вернуть только активные
    active = [p for p in projects.values() if p["status"] != "Завершён"]
    return active


def get_all_projects(sheet) -> list:
    """Получить ВСЕ проекты включая завершённые."""
    all_records = sheet.get_all_values()
    if len(all_records) <= 1:
        return []

    projects = {}
    for row in all_records[1:]:
        try:
            pid = row[0]
            record_type = row[1]
            if record_type == "Новый проект":
                address = row[3]
                status = row[7] if len(row) > 7 else "Новый"
                price = row[5] if len(row) > 5 else "0"
                owner = row[6] if len(row) > 6 else ""
                projects[pid] = {
                    "id": pid,
                    "address": address,
                    "status": status,
                    "price": price,
                    "owner": owner,
                }
            if record_type == "Статус" and pid in projects:
                projects[pid]["status"] = row[7] if len(row) > 7 else projects[pid]["status"]
        except (IndexError, ValueError):
            continue
    return list(projects.values())


def get_project_summary(sheet, project_id: str) -> dict:
    """Собрать полную сводку по проекту."""
    all_records = sheet.get_all_values()
    summary = {
        "id": project_id,
        "address": "",
        "description": "",
        "price": 0,
        "owner": "",
        "status": "Новый",
        "payments": [],
        "expenses": [],
        "total_paid": 0,
        "total_expenses": 0,
        "updates": [],
    }

    for row in all_records[1:]:
        try:
            if row[0] != project_id:
                continue

            record_type = row[1]

            if record_type == "Новый проект":
                summary["address"] = row[3]
                summary["description"] = row[4]
                summary["price"] = float(row[5]) if row[5] else 0
                summary["owner"] = row[6]
                summary["status"] = row[7] if len(row) > 7 else "Новый"

            elif record_type == "Платёж":
                amount = float(row[8]) if row[8] else 0
                summary["payments"].append({
                    "date": row[2],
                    "amount": amount,
                    "who": row[13] if len(row) > 13 else "",
                })
                summary["total_paid"] += amount

            elif record_type == "Расход":
                amount = float(row[10]) if row[10] else 0
                category = row[9] if len(row) > 9 else ""
                summary["expenses"].append({
                    "date": row[2],
                    "amount": amount,
                    "category": category,
                    "who": row[13] if len(row) > 13 else "",
                })
                summary["total_expenses"] += amount

            elif record_type == "Статус":
                summary["status"] = row[7] if len(row) > 7 else summary["status"]

            elif record_type == "Описание":
                summary["updates"].append({
                    "date": row[2],
                    "text": row[11] if len(row) > 11 else "",
                    "who": row[13] if len(row) > 13 else "",
                })

        except (IndexError, ValueError):
            continue

    return summary


def upload_photo_to_drive(file_path: str, project_id: str, address: str) -> str:
    """Загрузить фото в Google Drive и вернуть ссылку."""
    service = get_drive_service()

    # Найти или создать папку проекта
    folder_name = f"{project_id} — {address}"
    query = (
        f"name='{folder_name}' and "
        f"'{DRIVE_FOLDER_ID}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"trashed=false"
    )
    results = service.files().list(q=query, fields="files(id)").execute()
    folders = results.get("files", [])

    if folders:
        folder_id = folders[0]["id"]
    else:
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID],
        }
        folder = service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = folder["id"]

    # Загрузить фото
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_metadata = {
        "name": f"progress_{timestamp}.jpg",
        "parents": [folder_id],
    }
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id, webViewLink"
    ).execute()

    # Сделать файл доступным по ссылке
    service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return uploaded.get("webViewLink", "")


def add_row_to_sheet(sheet, row_data: list):
    """Добавить строку в таблицу."""
    sheet.append_row(row_data, value_input_option="USER_ENTERED")


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📋 Новый проект", "💰 Записать платёж"],
        ["💸 Записать расход", "📸 Добавить фото"],
        ["📝 Добавить описание", "🔄 Изменить статус"],
        ["📊 Статус проекта", "📁 Архив"],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context) -> int:
    """Стартовая команда /start."""
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
    """Обработка выбора из главного меню."""
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

    elif text == "📸 Добавить фото":
        return await show_project_list(update, context, PHOTO_SELECT_PROJECT)

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
    """Показать список проектов как inline-кнопки."""
    sheet = get_sheet()
    projects = get_all_projects(sheet) if include_all else get_active_projects(sheet)

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
    """Показать завершённые проекты."""
    sheet = get_sheet()
    all_projects = get_all_projects(sheet)
    archived = [p for p in all_projects if p["status"] == "Завершён"]

    if not archived:
        await update.message.reply_text(
            "📁 Архив пуст.", reply_markup=MAIN_MENU_KEYBOARD
        )
        return MAIN_MENU

    text = "📁 **Архив проектов:**\n\n"
    for p in archived:
        text += f"• {p['id']} — {p['address']}\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


async def cancel_callback(update: Update, context) -> int:
    """Обработка кнопки Отмена."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено.")
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# НОВЫЙ ПРОЕКТ
# ============================================================

async def new_project_address(update: Update, context) -> int:
    """Получить адрес проекта."""
    context.user_data["new_address"] = update.message.text
    await update.message.reply_text("📝 Введи описание проекта:")
    return NEW_PROJECT_DESCRIPTION


async def new_project_description(update: Update, context) -> int:
    """Получить описание проекта."""
    context.user_data["new_description"] = update.message.text
    await update.message.reply_text("💵 Введи цену проекта (число):")
    return NEW_PROJECT_PRICE


async def new_project_price(update: Update, context) -> int:
    """Получить цену проекта."""
    try:
        price = float(update.message.text.replace(",", "").replace("$", ""))
        context.user_data["new_price"] = price
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 15000")
        return NEW_PROJECT_PRICE

    # Показать список партнёров
    buttons = []
    for name in PARTNER_NAMES:
        buttons.append([InlineKeyboardButton(name, callback_data=f"owner_{name}")])

    await update.message.reply_text(
        "👷 Кто ведёт проект?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return NEW_PROJECT_OWNER


async def new_project_owner(update: Update, context) -> int:
    """Получить ответственного и сохранить проект."""
    query = update.callback_query
    await query.answer()

    owner = query.data.replace("owner_", "")
    context.user_data["new_owner"] = owner

    # Сохранить в таблицу
    sheet = get_sheet()
    project_id = get_next_project_id(sheet)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = ALLOWED_USERS.get(query.from_user.id, "Unknown")

    # Колонки: Project ID | Тип | Дата | Адрес | Описание | Цена | Ответственный | Статус | Платёж | Кат.расхода | Расход | Описание статуса | Фото | Кто записал
    row = [
        project_id,
        "Новый проект",
        now,
        context.user_data["new_address"],
        context.user_data["new_description"],
        context.user_data["new_price"],
        owner,
        "Новый",
        "",  # платёж
        "",  # категория расхода
        "",  # сумма расхода
        "",  # описание статуса
        "",  # фото
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    await query.edit_message_text(
        f"✅ Проект создан!\n\n"
        f"🆔 ID: {project_id}\n"
        f"📍 Адрес: {context.user_data['new_address']}\n"
        f"💵 Цена: ${context.user_data['new_price']:,.2f}\n"
        f"👷 Ответственный: {owner}"
    )
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)

    # Очистить данные
    for key in ["new_address", "new_description", "new_price", "new_owner"]:
        context.user_data.pop(key, None)

    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ ПЛАТЁЖ
# ============================================================

async def payment_select_project(update: Update, context) -> int:
    """Выбран проект для платежа."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["payment_project_id"] = project_id
    await query.edit_message_text(f"💰 Проект {project_id}.\n\nВведи сумму платежа:")
    return PAYMENT_AMOUNT


async def payment_amount(update: Update, context) -> int:
    """Получить сумму платежа и сохранить."""
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 5000")
        return PAYMENT_AMOUNT

    sheet = get_sheet()
    project_id = context.user_data["payment_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    row = [
        project_id, "Платёж", now,
        "", "", "", "", "",
        amount,  # сумма платежа
        "", "", "", "",
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    await update.message.reply_text(
        f"✅ Платёж записан!\n\n"
        f"🆔 Проект: {project_id}\n"
        f"💵 Сумма: ${amount:,.2f}\n"
        f"📅 Дата: {now}",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    context.user_data.pop("payment_project_id", None)
    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ РАСХОД
# ============================================================

EXPENSE_CATEGORIES = ["Материалы", "Субподрядчик", "Аренда оборудования", "Прочее"]


async def expense_select_project(update: Update, context) -> int:
    """Выбран проект для расхода."""
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
    """Выбрана категория расхода."""
    query = update.callback_query
    await query.answer()

    category = query.data.replace("expcat_", "")
    context.user_data["expense_category"] = category
    await query.edit_message_text(f"💸 Категория: {category}.\n\nВведи сумму расхода:")
    return EXPENSE_AMOUNT


async def expense_amount(update: Update, context) -> int:
    """Получить сумму расхода."""
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
    """Получить описание расхода и сохранить."""
    description = update.message.text if update.message.text != "-" else ""

    sheet = get_sheet()
    project_id = context.user_data["expense_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    row = [
        project_id, "Расход", now,
        "", "", "", "", "",
        "",  # платёж
        context.user_data["expense_category"],
        context.user_data["expense_amount"],
        description,
        "",
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    await update.message.reply_text(
        f"✅ Расход записан!\n\n"
        f"🆔 Проект: {project_id}\n"
        f"📂 Категория: {context.user_data['expense_category']}\n"
        f"💸 Сумма: ${context.user_data['expense_amount']:,.2f}\n"
        f"📝 Описание: {description or '—'}",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

    for key in ["expense_project_id", "expense_category", "expense_amount"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# ДОБАВИТЬ ФОТО
# ============================================================

async def photo_select_project(update: Update, context) -> int:
    """Выбран проект для фото."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")
    context.user_data["photo_project_id"] = project_id
    await query.edit_message_text(
        f"📸 Проект {project_id}.\n\nОтправь фото прогресса:"
    )
    return PHOTO_UPLOAD


async def photo_upload(update: Update, context) -> int:
    """Получить фото, загрузить в Drive, записать в таблицу."""
    if not update.message.photo:
        await update.message.reply_text("❌ Отправь фото, не файл.")
        return PHOTO_UPLOAD

    # Скачать фото
    photo = update.message.photo[-1]  # лучшее качество
    file = await context.bot.get_file(photo.file_id)
    file_path = f"/tmp/photo_{photo.file_id}.jpg"
    await file.download_to_drive(file_path)

    # Получить адрес проекта для названия папки
    sheet = get_sheet()
    project_id = context.user_data["photo_project_id"]
    summary = get_project_summary(sheet, project_id)

    # Загрузить в Google Drive
    try:
        drive_link = upload_photo_to_drive(file_path, project_id, summary["address"])
    except Exception as e:
        logger.error(f"Drive upload error: {e}")
        drive_link = "Ошибка загрузки"

    # Записать в таблицу
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    row = [
        project_id, "Фото", now,
        "", "", "", "", "",
        "", "", "", "",
        drive_link,
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    # Удалить временный файл
    try:
        os.remove(file_path)
    except OSError:
        pass

    await update.message.reply_text(
        f"✅ Фото загружено!\n\n"
        f"🆔 Проект: {project_id}\n"
        f"🔗 Ссылка: {drive_link}",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    context.user_data.pop("photo_project_id", None)
    return MAIN_MENU


# ============================================================
# ДОБАВИТЬ ОПИСАНИЕ
# ============================================================

async def status_desc_select_project(update: Update, context) -> int:
    """Выбран проект для описания."""
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
    """Сохранить описание статуса."""
    description = update.message.text

    sheet = get_sheet()
    project_id = context.user_data["desc_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    row = [
        project_id, "Описание", now,
        "", "", "", "", "",
        "", "", "",
        description,
        "",
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    await update.message.reply_text(
        f"✅ Описание добавлено!\n\n"
        f"🆔 Проект: {project_id}\n"
        f"📝 {description}",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    context.user_data.pop("desc_project_id", None)
    return MAIN_MENU


# ============================================================
# ИЗМЕНИТЬ СТАТУС
# ============================================================

STATUS_OPTIONS = ["Новый", "В работе", "Приостановлен", "Завершён"]


async def change_status_select_project(update: Update, context) -> int:
    """Выбран проект для смены статуса."""
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
    """Сохранить новый статус."""
    query = update.callback_query
    await query.answer()

    new_status = query.data.replace("status_", "")

    sheet = get_sheet()
    project_id = context.user_data["status_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = ALLOWED_USERS.get(query.from_user.id, "Unknown")

    row = [
        project_id, "Статус", now,
        "", "", "", "",
        new_status,
        "", "", "", "", "",
        user_name,
    ]
    add_row_to_sheet(sheet, row)

    await query.edit_message_text(
        f"✅ Статус обновлён!\n\n"
        f"🆔 Проект: {project_id}\n"
        f"🔄 Новый статус: {new_status}"
    )
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data.pop("status_project_id", None)
    return MAIN_MENU


# ============================================================
# СТАТУС ПРОЕКТА — СВОДКА
# ============================================================

async def view_status_select_project(update: Update, context) -> int:
    """Выбран проект для просмотра сводки."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        return await cancel_callback(update, context)

    project_id = query.data.replace("proj_", "")

    sheet = get_sheet()
    s = get_project_summary(sheet, project_id)

    balance = s["total_paid"] - s["total_expenses"]
    remaining = s["price"] - s["total_paid"]

    text = (
        f"📊 **Проект {s['id']}**\n\n"
        f"📍 Адрес: {s['address']}\n"
        f"📝 Описание: {s['description']}\n"
        f"👷 Ответственный: {s['owner']}\n"
        f"🔄 Статус: {s['status']}\n\n"
        f"💵 Цена проекта: ${s['price']:,.2f}\n"
        f"✅ Получено: ${s['total_paid']:,.2f}\n"
        f"💸 Расходы: ${s['total_expenses']:,.2f}\n"
        f"📈 Остаток от клиента: ${remaining:,.2f}\n"
        f"💰 Текущая прибыль: ${balance:,.2f}\n"
    )

    if s["payments"]:
        text += "\n**Платежи:**\n"
        for p in s["payments"]:
            text += f"  • {p['date']} — ${p['amount']:,.2f} ({p['who']})\n"

    if s["expenses"]:
        text += "\n**Расходы:**\n"
        for e in s["expenses"]:
            text += f"  • {e['date']} — ${e['amount']:,.2f} [{e['category']}] ({e['who']})\n"

    if s["updates"]:
        text += "\n**Последние обновления:**\n"
        for u in s["updates"][-5:]:
            text += f"  • {u['date']} — {u['text']} ({u['who']})\n"

    await query.edit_message_text(text, parse_mode="Markdown")
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# ОТМЕНА
# ============================================================

async def cancel(update: Update, context) -> int:
    """Команда /cancel."""
    await update.message.reply_text(
        "❌ Действие отменено.", reply_markup=MAIN_MENU_KEYBOARD
    )
    return MAIN_MENU


# ============================================================
# ЗАПУСК БОТА
# ============================================================

def main():
    """Запуск бота."""
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler),
            ],
            # Новый проект
            NEW_PROJECT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_address),
            ],
            NEW_PROJECT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_description),
            ],
            NEW_PROJECT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_price),
            ],
            NEW_PROJECT_OWNER: [
                CallbackQueryHandler(new_project_owner, pattern="^owner_"),
            ],
            # Платёж
            PAYMENT_SELECT_PROJECT: [
                CallbackQueryHandler(payment_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            PAYMENT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount),
            ],
            # Расход
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
            # Фото
            PHOTO_SELECT_PROJECT: [
                CallbackQueryHandler(photo_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            PHOTO_UPLOAD: [
                MessageHandler(filters.PHOTO, photo_upload),
            ],
            # Описание
            STATUS_DESC_SELECT_PROJECT: [
                CallbackQueryHandler(status_desc_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            STATUS_DESC_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, status_desc_text),
            ],
            # Статус
            CHANGE_STATUS_SELECT_PROJECT: [
                CallbackQueryHandler(change_status_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            CHANGE_STATUS_VALUE: [
                CallbackQueryHandler(change_status_value, pattern="^status_"),
            ],
            # Просмотр
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

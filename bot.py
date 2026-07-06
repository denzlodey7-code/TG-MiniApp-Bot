#!/usr/bin/env python3
"""
Telegram Mini App Bot — aiogram 3.x + APScheduler + SQLite
ТЗ: проактивный бот с трёхуровневой системой проектов.
Управление через DM: полный CRUD проектов, подпроектов и задач.
"""
import json
import logging
import os
import asyncio
import re
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    MenuButtonWebApp,
    WebAppInfo,
)
from aiogram.filters import CommandStart, Command, CommandObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

import db

# ─── Конфигурация ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://powerseller-vocals-albums-costs.trycloudflare.com")
REMINDER_INTERVAL_HOURS = int(os.environ.get("REMINDER_INTERVAL", "4"))  # часов

# AI-конфигурация
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
AI_MODEL = "deepseek-v4-flash"
AI_BASE_URL = "https://api.deepseek.com/v1/chat/completions"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("tg-miniapp-bot")

# ─── Объекты ──────────────────────────────────────────────────
bot    = Bot(token=BOT_TOKEN)
dp     = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ─── Клавиатуры ───────────────────────────────────────────────
def miniapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть Mini App",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )]
    ])

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

# ─── Хелперы ──────────────────────────────────────────────────
STATUS_EMOJI = {"active": "🟢", "review": "🔵", "paused": "🟡", "done": "⚪", "planning": "📝"}

def fmt_progress(pct: int) -> str:
    """Текстовый прогресс-бар."""
    filled = pct // 10
    return "█" * filled + "░" * (10 - filled) + f" {pct}%"

def fmt_project_brief(p: dict, idx: int = None) -> str:
    prefix = f"{idx}. " if idx else ""
    dates = ""
    ds = p.get('date_start')
    de = p.get('date_end')
    if ds and de:
        dates = f"\n   📅 {ds} → {de}"
        try:
            from datetime import date
            d_end = date.fromisoformat(de)
            d_now = date.today()
            delta = (d_end - d_now).days
            if delta > 0:
                dates += f" · ⏰ {delta} дн."
            elif delta == 0:
                dates += " · ⏰ Сегодня!"
            else:
                dates += f" · ⏰ Просрочен {abs(delta)} дн."
        except Exception:
            pass
    return (
        f"{prefix}📁 **{p['name']}** — `{p['id']}`\n"
        f"   {fmt_progress(p['progress'])}\n"
        f"   📁 {p['subprojects_done']}/{p['subprojects_total']} подпроектов"
        f"{dates}\n"
    )

def fmt_project_detail(p: dict) -> str:
    lines = [f"📁 **{p['name']}** — `{p['id']}`"]
    if p.get('description'):
        lines.append(f"📝 {p['description']}")
    # Даты
    ds = p.get('date_start')
    de = p.get('date_end')
    if ds and de:
        lines.append(f"📅 {ds} → {de}")
        # Дней до завершения
        try:
            from datetime import date
            d_end = date.fromisoformat(de)
            d_now = date.today()
            delta = (d_end - d_now).days
            if delta > 0:
                lines.append(f"⏰ {delta} дн. до завершения")
            elif delta == 0:
                lines.append("⏰ Сегодня дедлайн!")
            else:
                lines.append(f"⏰ Просрочен {abs(delta)} дн.")
        except Exception:
            pass
    lines.append(fmt_progress(p['progress']))
    lines.append(f"📁 Подпроектов: {p['subprojects_done']}/{p['subprojects_total']}")
    lines.append("")

    for i, sp in enumerate(p['subprojects'], 1):
        lines.append(f"  {i}. 📂 **{sp['name']}** — `{sp['id']}`")
        lines.append(f"     {fmt_progress(sp['progress'])} ({sp['done_count']}/{sp['total_count']} задач)")
        for j, t in enumerate(sp['tasks'], 1):
            mark = "✅" if t['done'] else "⬜"
            lines.append(f"        {mark} {j}. {t['text']} — `{t['id']}`")
        lines.append("")

    return "\n".join(lines)

HELP_TEXT = (
    "📋 **Управление проектами — команды**\n\n"
    "— **AI-создание:**\n"
    "Просто напиши: `создай проект <название>`\n"
    "Бот сам продумает структуру подпроектов и задач.\n\n"
    "— **Просмотр:**\n"
    "/projects — список всех проектов\n"
    "/project `<id>` — детали проекта\n\n"
    "— **Проекты:**\n"
    "/add_project `Название | Описание`\n"
    "/edit_project `<id>` `Новое название | Новое описание`\n"
    "/del_project `<id>`\n\n"
    "— **Подпроекты:**\n"
    "/add_sub `<project_id>` `Название`\n"
    "/edit_sub `<subproject_id>` `Новое название`\n"
    "/del_sub `<subproject_id>`\n\n"
    "— **Задачи:**\n"
    "/add_task `<subproject_id>` `Текст задачи`\n"
    "/done_task `<task_id>`\n"
    "/undone_task `<task_id>`\n\n"
    "— **Прочее:**\n"
    "/help — этот список\n"
    "/reminders — статус напоминаний\n"
    "/interval `<часы>` — изменить интервал\n"
)

# ─── AI-генерация структуры проекта ───────────────────────────
AI_PROMPT = """Ты — эксперт по управлению проектами. Создай детальную структуру проекта в формате JSON.

Проект: "{project_name}"

Создай 3-5 подпроектов (этапов), каждый с 3-7 задачами. Задачи должны быть конкретными и выполнимыми.

Ответь ТОЛЬКО валидным JSON, без markdown, без пояснений:
{{
  "name": "Название проекта",
  "description": "Краткое описание (1-2 предложения)",
  "subprojects": [
    {{
      "name": "Название подпроекта/этапа",
      "tasks": ["Задача 1", "Задача 2", "Задача 3"]
    }}
  ]
}}"""


async def generate_project_structure(project_name: str) -> dict | None:
    """Генерирует структуру проекта через DeepSeek API."""
    if not DEEPSEEK_API_KEY:
        log.warning("DEEPSEEK_API_KEY not set, cannot generate structure")
        return None

    prompt = AI_PROMPT.format(project_name=project_name)
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "Ты эксперт по управлению проектами. Отвечай только валидным JSON на русском языке."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.7,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                AI_BASE_URL,
                json=payload,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.error("AI API error %d: %s", resp.status, text[:200])
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]

                # Извлекаем JSON из ответа (на случай если модель обернула в markdown)
                json_match = re.search(r'\{[\s\S]*\}', content)
                if not json_match:
                    log.error("No JSON found in AI response: %s", content[:200])
                    return None

                return json.loads(json_match.group())
    except asyncio.TimeoutError:
        log.error("AI API timeout")
        return None
    except Exception as e:
        log.error("AI API error: %s", e)
        return None


async def create_project_from_structure(structure: dict, user_name: str,
                                        date_start: str = "", date_end: str = "") -> dict:
    """Создаёт проект с подпроектами и задачами из AI-структуры."""
    name = structure.get("name", "Без названия")
    desc = structure.get("description", "")
    pid = db.add_project(name, desc, date_start, date_end)

    created_subs = 0
    created_tasks = 0

    for sp_data in structure.get("subprojects", []):
        sp_name = sp_data.get("name", "Подпроект")
        sid = db.add_subproject(pid, sp_name)
        created_subs += 1

        for task_text in sp_data.get("tasks", []):
            db.add_task(sid, task_text)
            created_tasks += 1

    log.info("AI project created: id=%s name=%s subs=%d tasks=%d by %s",
             pid, name, created_subs, created_tasks, user_name)

    return {"pid": pid, "name": name, "desc": desc,
            "subs": created_subs, "tasks": created_tasks,
            "structure": structure}


def fmt_ai_result(info: dict, date_start: str = "", date_end: str = "") -> str:
    """Форматирует результат AI-создания проекта."""
    s = info["structure"]
    lines = [
        f"🤖 **Проект создан с AI-структурой!**\n",
        f"📁 **{info['name']}**\n",
        f"🆔 `{info['pid']}`\n",
        f"📝 {info['desc']}\n",
    ]
    if date_start and date_end:
        lines.append(f"📅 {date_start} → {date_end}\n")
    lines.append(f"📊 {info['subs']} подпроектов · {info['tasks']} задач\n")

    for i, sp in enumerate(s.get("subprojects", []), 1):
        lines.append(f"\n{i}. 📂 **{sp.get('name', '—')}**")
        for j, task in enumerate(sp.get("tasks", []), 1):
            lines.append(f"   ⬜ {j}. {task}")

    lines.append(f"\nОткрыть в Mini App 👇")
    return "\n".join(lines)

# ─── Хендлеры: базовые команды ─────────────────────────────────
# Состояния для ввода пароля
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

class AuthStates(StatesGroup):
    waiting_password = State()

# ID администратора (DenZlodey)
ADMIN_ID = 385655859


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    name = message.from_user.first_name or "друг"
    username = message.from_user.username or ""

    db.register_user(uid, username, name)

    # Проверяем авторизацию
    if db.is_authorized(uid):
        await message.answer(
            f"С возвращением, {name}! 👋\n\n"
            f"Управляй проектами через Mini App 👇\n"
            f"📋 /help — список команд",
            reply_markup=miniapp_keyboard(),
        )
    elif db.has_password(uid):
        # Пароль установлен, но пользователь не авторизован — запрашиваем
        await state.set_state(AuthStates.waiting_password)
        await message.answer(
            f"🔒 Доступ ограничен.\n\n"
            f"Введите пароль для доступа к боту:"
        )
    else:
        # Нет пароля — если админ, устанавливаем; иначе отказ
        if uid == ADMIN_ID:
            await state.set_state(AuthStates.waiting_password)
            await message.answer(
                f"Добро пожаловать, {name}! 👋\n\n"
                f"🔒 У вас нет пароля. Введите пароль для установки:"
            )
        else:
            await message.answer(
                f"🔒 Доступ к боту ограничен.\n\n"
                f"Обратитесь к администратору для получения пароля."
            )

    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(
            text="📱 Приложение",
            web_app=WebAppInfo(url=WEBAPP_URL),
        ),
    )
    log.info("User registered: id=%s name=%s", uid, name)


@router.message(AuthStates.waiting_password)
async def process_password(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    password = message.text.strip()

    if db.has_password(uid):
        # Пароль уже установлен — проверяем
        if db.verify_password(uid, password):
            db.set_authorized(uid, True)
            await state.clear()
            await message.answer(
                "✅ Доступ разрешён!\n\n"
                "Управляй проектами через Mini App 👇\n"
                "📋 /help — список команд",
                reply_markup=miniapp_keyboard(),
            )
            log.info("User authorized: id=%s", uid)
        else:
            await message.answer("❌ Неверный пароль. Попробуйте снова:")
    else:
        # Пароля нет — устанавливаем (только для админа)
        if uid == ADMIN_ID:
            db.set_password(uid, password)
            db.set_authorized(uid, True)
            await state.clear()
            await message.answer(
                "✅ Пароль установлен и доступ разрешён!\n\n"
                "Управляй проектами через Mini App 👇\n"
                "📋 /help — список команд",
                reply_markup=miniapp_keyboard(),
            )
            log.info("Admin password set: id=%s", uid)
        else:
            await state.clear()
            await message.answer("🔒 Доступ запрещён. Обратитесь к администратору.")


@router.message(Command("setpassword"))
async def cmd_setpassword(message: Message, state: FSMContext) -> None:
    """Команда для смены пароля (только админ)."""
    uid = message.from_user.id
    if uid != ADMIN_ID:
        await message.answer("🔒 Команда доступна только администратору.")
        return
    await state.set_state(AuthStates.waiting_password)
    await message.answer("🔒 Введите новый пароль:")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    uid = message.from_user.id
    if not db.is_authorized(uid):
        await message.answer("🔒 Доступ ограничен. Введите /start для авторизации.")
        return
    await message.answer(HELP_TEXT, reply_markup=miniapp_keyboard())


# ─── Хендлеры: просмотр ────────────────────────────────────────
# ─── Middleware авторизации ─────────────────────────────────────
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            uid = event.from_user.id
            cmd = event.text.split()[0].lower()
            if cmd not in ("/start", "/setpassword") and not db.is_authorized(uid):
                await event.answer("🔒 Доступ ограничен. Введите /start для авторизации.")
                return  # Блокируем — не вызываем handler
        return await handler(event, data)  # Пропускаем к нужному хендлеру


router.message.middleware(AuthMiddleware())


@router.message(Command("projects"))
async def cmd_projects(message: Message) -> None:
    structure = db.get_full_structure()
    if not structure:
        await message.answer("📂 Пока нет проектов.\nСоздайте: /add_project `Название | Описание`")
        return
    lines = ["📋 **Все проекты:**\n"]
    for i, p in enumerate(structure, 1):
        lines.append(fmt_project_brief(p, i))
    lines.append("\nДетали: /project `<id>`")
    await message.answer("\n".join(lines), reply_markup=miniapp_keyboard())


@router.message(Command("project"))
async def cmd_project(message: Message, command: CommandObject) -> None:
    pid = (command.args or "").strip()
    if not pid:
        await message.answer("Укажите ID проекта: /project `p_demo1`")
        return
    structure = db.get_full_structure()
    p = next((x for x in structure if x['id'] == pid), None)
    if not p:
        await message.answer(f"❌ Проект `{pid}` не найден.\nСписок: /projects")
        return
    await message.answer(fmt_project_detail(p), reply_markup=miniapp_keyboard())


# ─── Хендлеры: CRUD проектов ──────────────────────────────────
@router.message(Command("add_project"))
async def cmd_add_project(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: /add_project `Название | Описание | ДД.ММ.ГГГГ-ДД.ММ.ГГГГ`\n"
            "Пример: /add_project `Запуск лендинга | Лендинг для CRM | 01.07.2026-15.08.2026`\n\n"
            "Даты можно опустить: /add_project `Название | Описание`"
        )
        return
    parts = args.split("|")
    name = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else ""
    date_start = ""
    date_end = ""
    if len(parts) > 2:
        dates = parts[2].strip()
        # Парсим ДД.ММ.ГГГГ-ДД.ММ.ГГГГ
        date_match = re.match(r'(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})', dates)
        if date_match:
            ds = date_match.group(1)
            de = date_match.group(2)
            # Конвертируем в ISO формат YYYY-MM-DD
            date_start = f"{ds[6:10]}-{ds[3:5]}-{ds[0:2]}"
            date_end = f"{de[6:10]}-{de[3:5]}-{de[0:2]}"
    if not name:
        await message.answer("❌ Название не может быть пустым.")
        return
    pid = db.add_project(name, desc, date_start, date_end)
    log.info("New project via DM: id=%s name=%s by %s", pid, name, message.from_user.id)
    dates_info = ""
    if date_start and date_end:
        dates_info = f"\n📅 {ds} → {de}"
    await message.answer(
        f"✅ Проект создан!\n\n"
        f"📁 **{name}**\n"
        f"🆔 `{pid}`\n"
        f"📝 {desc or '—'}"
        f"{dates_info}\n\n"
        f"Добавить подпроект: /add_sub `{pid}` `Название`",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("del_project"))
async def cmd_del_project(message: Message, command: CommandObject) -> None:
    pid = (command.args or "").strip()
    if not pid:
        await message.answer("Укажите ID проекта: /del_project `p_demo1`")
        return
    structure = db.get_full_structure()
    p = next((x for x in structure if x['id'] == pid), None)
    if not p:
        await message.answer(f"❌ Проект `{pid}` не найден.")
        return
    db.delete_project(pid)
    log.info("Project deleted via DM: id=%s name=%s by %s", pid, p['name'], message.from_user.id)
    await message.answer(
        f"🗑 Проект **{p['name']}** удалён со всеми подпроектами и задачами.",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("edit_project"))
async def cmd_edit_project(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: /edit_project `<id>` `Новое название | Новое описание | ДД.ММ.ГГГГ-ДД.ММ.ГГГГ`"
        )
        return
    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.answer("❌ Укажите ID и новые данные.\nФормат: /edit_project `<id>` `Название | Описание | ДД.ММ.ГГГГ-ДД.ММ.ГГГГ`")
        return
    pid = parts[0].strip()
    rest = parts[1].strip()
    name_parts = rest.split("|")
    name = name_parts[0].strip()
    desc = name_parts[1].strip() if len(name_parts) > 1 else ""
    date_start = ""
    date_end = ""
    if len(name_parts) > 2:
        dates = name_parts[2].strip()
        date_match = re.match(r'(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})', dates)
        if date_match:
            ds = date_match.group(1)
            de = date_match.group(2)
            date_start = f"{ds[6:10]}-{ds[3:5]}-{ds[0:2]}"
            date_end = f"{de[6:10]}-{de[3:5]}-{de[0:2]}"

    structure = db.get_full_structure()
    p = next((x for x in structure if x['id'] == pid), None)
    if not p:
        await message.answer(f"❌ Проект `{pid}` не найден.")
        return
    db.update_project(pid, name, desc, date_start, date_end)
    log.info("Project edited via DM: id=%s by %s", pid, message.from_user.id)
    dates_info = ""
    if date_start and date_end:
        dates_info = f"\n📅 {ds} → {de}"
    await message.answer(
        f"✏️ Проект обновлён!\n\n"
        f"📁 **{name}**\n"
        f"🆔 `{pid}`\n"
        f"📝 {desc or '—'}"
        f"{dates_info}",
        reply_markup=miniapp_keyboard(),
    )


# ─── Хендлеры: CRUD подпроектов ───────────────────────────────
@router.message(Command("add_sub"))
async def cmd_add_sub(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /add_sub `<project_id>` `Название`\n"
            "Пример: /add_sub `p_demo1` `Дизайн лендинга`"
        )
        return
    pid, name = parts[0].strip(), parts[1].strip()
    structure = db.get_full_structure()
    p = next((x for x in structure if x['id'] == pid), None)
    if not p:
        await message.answer(f"❌ Проект `{pid}` не найден.\nСписок: /projects")
        return
    sid = db.add_subproject(pid, name)
    log.info("New subproject via DM: id=%s project=%s by %s", sid, pid, message.from_user.id)
    await message.answer(
        f"✅ Подпроект создан!\n\n"
        f"📂 **{name}**\n"
        f"🆔 `{sid}`\n"
        f"📁 В проекте: **{p['name']}**\n\n"
        f"Добавить задачу: /add_task `{sid}` `Текст задачи`",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("del_sub"))
async def cmd_del_sub(message: Message, command: CommandObject) -> None:
    sid = (command.args or "").strip()
    if not sid:
        await message.answer("Укажите ID подпроекта: /del_sub `sp1_1`\nДетали: /project `<project_id>`")
        return
    # Ищем подпроект в структуре
    structure = db.get_full_structure()
    found = None
    for p in structure:
        for sp in p['subprojects']:
            if sp['id'] == sid:
                found = (p, sp)
                break
    if not found:
        await message.answer(f"❌ Подпроект `{sid}` не найден.")
        return
    p, sp = found
    db.delete_subproject(sid)
    log.info("Subproject deleted via DM: id=%s name=%s by %s", sid, sp['name'], message.from_user.id)
    await message.answer(
        f"🗑 Подпроект **{sp['name']}** удалён со всеми задачами.\n"
        f"📁 Из проекта: **{p['name']}**",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("edit_sub"))
async def cmd_edit_sub(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.answer("Формат: /edit_sub `<subproject_id>` `Новое название`")
        return
    sid, name = parts[0].strip(), parts[1].strip()
    structure = db.get_full_structure()
    found = None
    for p in structure:
        for sp in p['subprojects']:
            if sp['id'] == sid:
                found = sp
                break
    if not found:
        await message.answer(f"❌ Подпроект `{sid}` не найден.")
        return
    db.update_subproject(sid, name)
    log.info("Subproject edited via DM: id=%s by %s", sid, message.from_user.id)
    await message.answer(
        f"✏️ Подпроект обновлён!\n\n"
        f"📂 **{name}**\n"
        f"🆔 `{sid}`",
        reply_markup=miniapp_keyboard(),
    )


# ─── Хендлеры: CRUD задач ─────────────────────────────────────
@router.message(Command("add_task"))
async def cmd_add_task(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /add_task `<subproject_id>` `Текст задачи`\n"
            "Пример: /add_task `sp1_1` `Настроить CI/CD`"
        )
        return
    spid, text = parts[0].strip(), parts[1].strip()
    # Проверяем что подпроект существует
    structure = db.get_full_structure()
    found = None
    for p in structure:
        for sp in p['subprojects']:
            if sp['id'] == spid:
                found = (p, sp)
                break
    if not found:
        await message.answer(f"❌ Подпроект `{spid}` не найден.\nДетали: /project `<project_id>`")
        return
    p, sp = found
    tid = db.add_task(spid, text)
    log.info("New task via DM: id=%s subproject=%s by %s", tid, spid, message.from_user.id)
    await message.answer(
        f"✅ Задача добавлена!\n\n"
        f"⬜ {text}\n"
        f"🆔 `{tid}`\n"
        f"📂 В подпроекте: **{sp['name']}**\n\n"
        f"Отметить выполненной: /done_task `{tid}`",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("done_task"))
async def cmd_done_task(message: Message, command: CommandObject) -> None:
    tid = (command.args or "").strip()
    if not tid:
        await message.answer("Укажите ID задачи: /done_task `t_xxx`")
        return
    db.update_tasks([{"task_id": tid, "done": True}])
    log.info("Task done via DM: id=%s by %s", tid, message.from_user.id)
    await message.answer(f"✅ Задача `{tid}` отмечена выполненной.", reply_markup=miniapp_keyboard())


@router.message(Command("undone_task"))
async def cmd_undone_task(message: Message, command: CommandObject) -> None:
    tid = (command.args or "").strip()
    if not tid:
        await message.answer("Укажите ID задачи: /undone_task `t_xxx`")
        return
    db.update_tasks([{"task_id": tid, "done": False}])
    log.info("Task undone via DM: id=%s by %s", tid, message.from_user.id)
    await message.answer(f"⬜ Задача `{tid}` возвращена в работу.", reply_markup=miniapp_keyboard())


# ─── Хендлеры: напоминания ─────────────────────────────────────
@router.message(Command("reminders"))
async def cmd_reminders(message: Message) -> None:
    await message.answer(
        f"⏰ **Статус напоминаний**\n\n"
        f"🌅 Утренняя сводка: ежедневно в 09:00\n"
        f"🔄 Интервальный опрос: каждые {REMINDER_INTERVAL_HOURS} ч.\n\n"
        f"Изменить интервал: /interval `<часы>`",
        reply_markup=miniapp_keyboard(),
    )


@router.message(Command("interval"))
async def cmd_interval(message: Message, command: CommandObject) -> None:
    global REMINDER_INTERVAL_HOURS
    args = (command.args or "").strip()
    if not args or not args.isdigit():
        await message.answer("Формат: /interval `4` — каждые 4 часа")
        return
    hours = int(args)
    if hours < 1:
        await message.answer("❌ Интервал должен быть не менее 1 часа.")
        return
    REMINDER_INTERVAL_HOURS = hours
    # Обновляем джоб в планировщике
    scheduler.reschedule_job(
        "interval_job",
        trigger=IntervalTrigger(hours=hours),
    )
    log.info("Interval changed to %dh by %s", hours, message.from_user.id)
    await message.answer(
        f"✅ Интервал напоминаний изменён: каждые {hours} ч.",
        reply_markup=miniapp_keyboard(),
    )


# ─── Хендлер: AI-создание проекта по тексту ────────────────────
AI_TRIGGERS = [
    "создай проект", "создать проект", "новый проект",
    "create project", "make project",
]

AI_TRIGGERS_REGEX = re.compile(
    r'^(?:создай|создать|новый|create|make|сделай)\s+(?:проект|project)\s+(.+)',
    re.IGNORECASE
)


@router.message(F.text)
async def handle_ai_project_creation(message: Message) -> None:
    """Обрабатывает текстовые сообщения для AI-создания проекта."""
    text = (message.text or "").strip()

    # Проверяем триггер
    match = AI_TRIGGERS_REGEX.match(text)
    if not match:
        # Не триггер — показываем подсказку
        await message.answer(
            "💡 Напиши: `создай проект <название>`\n"
            "Бот сам продумает структуру подпроектов и задач.\n\n"
            "С датами: `создай проект Название с 01.07.2026 по 15.08.2026`\n\n"
            "📋 /help — все команды",
            reply_markup=miniapp_keyboard(),
        )
        return

    project_name = match.group(1).strip().strip('"').strip("'").strip("«»").strip()

    if not project_name or len(project_name) < 2:
        await message.answer("❌ Укажи название проекта.\nПример: `создай проект Запуск интернет-магазина`")
        return

    # Парсим даты из текста: "с ДД.ММ.ГГГГ по ДД.ММ.ГГГГ"
    date_start = ""
    date_end = ""
    date_match = re.search(r'с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})', text)
    if date_match:
        ds = date_match.group(1)
        de = date_match.group(2)
        date_start = f"{ds[6:10]}-{ds[3:5]}-{ds[0:2]}"
        date_end = f"{de[6:10]}-{de[3:5]}-{de[0:2]}"
        # Убираем даты из названия проекта
        project_name = re.sub(r'\s+с\s+\d{2}\.\d{2}\.\d{4}\s+по\s+\d{2}\.\d{2}\.\d{4}', '', project_name).strip()

    user_name = message.from_user.first_name or "друг"

    # Сообщаем о начале генерации
    dates_info = f"\n📅 {ds} → {de}" if date_start and date_end else ""
    progress_msg = await message.answer(
        f"🤖 Анализирую «{project_name}»...\n⏳ Генерирую структуру проекта...{dates_info}",
    )

    # Генерируем структуру
    structure = await generate_project_structure(project_name)

    if not structure:
        await progress_msg.edit_text(
            f"⚠️ Не удалось сгенерировать структуру для «{project_name}».\n\n"
            f"Создай вручную: /add_project `{project_name} | Описание`"
        )
        return

    # Создаём проект в БД
    try:
        info = await create_project_from_structure(structure, user_name, date_start, date_end)
    except Exception as e:
        log.error("Failed to create AI project: %s", e)
        await progress_msg.edit_text("❌ Ошибка при создании проекта. Попробуй позже.")
        return

    # Отправляем результат
    result_text = fmt_ai_result(info, date_start, date_end)
    await progress_msg.edit_text(result_text, reply_markup=miniapp_keyboard())


# ─── Хендлер: web_app_data из Mini App ─────────────────────────
@router.message(F.web_app_data)
async def handle_web_app_data(message: Message) -> None:
    """Приём данных из Mini App."""
    raw = message.web_app_data.data
    log.info("web_app_data from user %s: %s", message.from_user.id, raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await message.answer("⚠️ Получены некорректные данные.")
        return

    msg_type = data.get("type", "status_update")
    user_name = data.get("user_name", "Гость")
    ts = data.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts).strftime("%d.%m.%Y %H:%M")
    except Exception:
        dt = ts or "—"

    # ─── Обновление задач (чек-листов) ───
    if msg_type == "task_updates":
        changes = data.get("changes", [])
        result = db.update_tasks(changes)
        n = result["tasks_updated"]

        structure = db.get_full_structure()
        summary_parts = []
        for p in structure:
            if p["progress"] < 100:
                summary_parts.append(f"📁 {p['name']}: {p['progress']}% ({p['subprojects_done']}/{p['subprojects_total']} подпроектов)")

        text = (
            f"🔔 **Данные успешно обновлены!**\n\n"
            f"✅ Изменено задач: {n}\n"
            f"🕐 Время: {dt}\n\n"
        )
        if summary_parts:
            text += "**Текущие статусы проектов:**\n" + "\n".join(summary_parts)

        await message.answer(text, reply_markup=miniapp_keyboard())
        log.info("Tasks updated: %d changes by %s", n, user_name)

    # ─── Создание нового проекта ───
    elif msg_type == "new_project":
        name = data.get("project_name", "—")
        desc = data.get("description", "—")
        pid = db.add_project(name, desc)
        log.info("New project via Mini App: id=%s name=%s by %s", pid, name, user_name)

        text = (
            f"🆕 **Создан новый проект**\n\n"
            f"👤 Автор: {user_name}\n"
            f"📁 Название: {name}\n"
            f"📝 Описание: {desc}\n"
            f"🕐 Время: {dt}\n\n"
            f"Проект сохранён в базу данных. "
            f"Открой Mini App, чтобы добавить подпроекты и задачи."
        )
        await message.answer(text, reply_markup=miniapp_keyboard())

    else:
        await message.answer("⚠️ Неизвестный тип данных от Mini App.")


# ─── APScheduler: фоновые напоминания ─────────────────────────
async def morning_summary() -> None:
    """Утренняя сводка в 09:00 — проактивный опрос."""
    uids = db.get_all_user_ids()
    if not uids:
        return
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    log.info("Morning summary to %d users at %s", len(uids), now)

    structure = db.get_full_structure()
    parts = []
    for p in structure:
        if p["progress"] < 100:
            parts.append(f"  • {p['name']} — {p['progress']}% ({p['subprojects_done']}/{p['subprojects_total']} подпроектов)")

    for uid in uids:
        try:
            text = (
                f"🌅 **Доброе утро! Время плановой сверки.**\n\n"
                f"Каковы изменения по текущим проектам на сегодня? "
                f"Давай зафиксируем прогресс.\n\n"
            )
            if parts:
                text += "**Проекты, требующие внимания:**\n" + "\n".join(parts) + "\n\n"
            text += "Нажми кнопку ниже, чтобы открыть Mini App.\n\n"
            text += "📋 /help — команды для управления из чата"

            await bot.send_message(
                chat_id=uid,
                text=text,
                reply_markup=miniapp_keyboard(),
            )
        except Exception as e:
            log.warning("Morning summary failed for %s: %s", uid, e)


async def interval_reminder() -> None:
    """Мягкий интервальный опрос (каждые N часов)."""
    uids = db.get_all_user_ids()
    if not uids:
        return
    now = datetime.now().strftime("%H:%M")
    log.info("Interval reminder to %d users at %s", len(uids), now)

    for uid in uids:
        try:
            await bot.send_message(
                chat_id=uid,
                text=(
                    f"⏰ **Напоминание ({now})**\n\n"
                    f"Есть изменения по проектам? "
                    f"Обнови статусы через Mini App.\n\n"
                    f"📋 /help — команды для управления из чата"
                ),
                reply_markup=miniapp_keyboard(),
            )
        except Exception as e:
            log.warning("Interval reminder failed for %s: %s", uid, e)


# ─── Запуск ───────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

async def main() -> None:
    global scheduler
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан!")

    db.init_db()
    log.info("SQLite DB initialized")

    # Утренняя сводка — ежедневно в 09:00
    scheduler.add_job(
        morning_summary,
        trigger=CronTrigger(hour=9, minute=0),
        id="morning_job",
        name="Morning project summary",
        replace_existing=True,
    )

    # Интервальный опрос — каждые N часов
    scheduler.add_job(
        interval_reminder,
        trigger=IntervalTrigger(hours=REMINDER_INTERVAL_HOURS),
        id="interval_job",
        name="Interval project reminder",
        replace_existing=True,
    )

    scheduler.start()
    log.info("Scheduler started — morning 09:00, interval %dh", REMINDER_INTERVAL_HOURS)

    log.info("Bot starting polling…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
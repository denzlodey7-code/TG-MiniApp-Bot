"""
SQLite база данных для трёхуровневой системы проектов.
Уровень 1: Проект → Уровень 2: Подпроект → Уровень 3: Задача (чек-лист)
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "projects.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Создание таблиц и заполнение демо-данными при первом запуске."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            registered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS passwords (
            chat_id    INTEGER PRIMARY KEY,
            password   TEXT NOT NULL,
            is_authorized INTEGER DEFAULT 0,
            set_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT,
            date_start  TEXT,
            date_end    TEXT
        );

        CREATE TABLE IF NOT EXISTS subprojects (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            name        TEXT NOT NULL,
            deadline    TEXT,
            sort_order  INTEGER DEFAULT 0,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id             TEXT PRIMARY KEY,
            subproject_id  TEXT NOT NULL,
            text           TEXT NOT NULL,
            done           INTEGER DEFAULT 0,
            sort_order     INTEGER DEFAULT 0,
            FOREIGN KEY (subproject_id) REFERENCES subprojects(id) ON DELETE CASCADE
        );
    """)
    # Миграция: добавляем колонки дат если их нет (существующие БД)
    try:
        db.execute("ALTER TABLE projects ADD COLUMN date_start TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE projects ADD COLUMN date_end TEXT")
    except sqlite3.OperationalError:
        pass
    # Миграция: добавляем sort_order для tasks если колонки нет
    try:
        db.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # колонка уже существует
    # Нумеруем существующие задачи по rowid
    db.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY subproject_id ORDER BY rowid) AS rn
            FROM tasks WHERE sort_order = 0
        )
        UPDATE tasks SET sort_order = (SELECT rn FROM ranked WHERE ranked.id = tasks.id)
        WHERE sort_order = 0
    """)

    # Миграция: добавляем sort_order для subprojects
    try:
        db.execute("ALTER TABLE subprojects ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Нумеруем существующие подпроекты по rowid
    db.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY rowid) AS rn
            FROM subprojects WHERE sort_order = 0
        )
        UPDATE subprojects SET sort_order = (SELECT rn FROM ranked WHERE ranked.id = subprojects.id)
        WHERE sort_order = 0
    """)
    db.commit()

    # Сидируем демо-данные если таблица проектов пуста
    count = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    if count == 0:
        _seed_demo(db)
    db.close()


def _seed_demo(db: sqlite3.Connection) -> None:
    """Демонстрационные проекты для первого запуска."""
    now = datetime.now().isoformat()

    projects = [
        ("p_demo1", "Разработка Telegram-бота", "Проактивный бот с Mini App для управления проектами"),
        ("p_demo2", "DRW Bot (@donrewritingpro)", "Бот-копирайтер с публикацией в Telegram и VK"),
    ]

    subprojects_data = {
        "p_demo1": [
            ("sp1_1", "Фронтенд Mini App", None, [
                "Подключить telegram-web-app.js",
                "Сверстать главный экран с карточками",
                "Реализовать прогресс-бары с CSS-анимацией",
                "Добавить аккордеоны с чек-листами",
            ]),
            ("sp1_2", "Бэкенд бота", None, [
                "Настроить aiogram 3.x polling",
                "Создать SQLite-базу (3 уровня)",
                "Реализовать приём web_app_data",
            ]),
            ("sp1_3", "Деплой и инфраструктура", None, [
                "Установить Nginx на порт 8080",
                "Настроить Cloudflare-туннель (HTTPS)",
                "Создать systemd-сервис с Restart=always",
                "Настроить APScheduler на 09:00",
            ]),
        ],
        "p_demo2": [
            ("sp2_1", "Генерация контента", None, [
                "Написать скрипт генерации постов",
                "Подобрать ГОСТ-оформление",
            ]),
            ("sp2_2", "Публикация", None, [
                "Настроить Telegram-канал",
                "Настроить VK-кросспост",
                "Добавить Yandex ART иллюстрации",
            ]),
        ],
    }

    for pid, name, desc in projects:
        db.execute(
            "INSERT INTO projects (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (pid, name, desc, now),
        )

    for pid, subs in subprojects_data.items():
        for sid, sname, deadline, tasks in subs:
            db.execute(
                "INSERT INTO subprojects (id, project_id, name, deadline) VALUES (?, ?, ?, ?)",
                (sid, pid, sname, deadline),
            )
            for task_text in tasks:
                tid = "t_" + uuid.uuid4().hex[:10]
                db.execute(
                    "INSERT INTO tasks (id, subproject_id, text, done) VALUES (?, ?, ?, ?)",
                    (tid, sid, task_text, 0),
                )

    # Отметим пару задач как выполненные для демонстрации прогресса
    db.execute("UPDATE tasks SET done=1 WHERE id IN (SELECT id FROM tasks WHERE subproject_id='sp1_1' LIMIT 2)")
    db.execute("UPDATE tasks SET done=1 WHERE id IN (SELECT id FROM tasks WHERE subproject_id='sp1_2' LIMIT 1)")
    db.execute("UPDATE tasks SET done=1 WHERE id IN (SELECT id FROM tasks WHERE subproject_id='sp2_1' LIMIT 1)")
    db.commit()


# ─── API: пользователи ────────────────────────────────────────
def register_user(chat_id: int, username: str, first_name: str) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO users (chat_id, username, first_name, registered_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET username=?, first_name=?""",
        (chat_id, username, first_name, datetime.now().isoformat(),
         username, first_name),
    )
    db.commit()
    db.close()


def get_all_user_ids() -> list[int]:
    db = get_db()
    rows = db.execute("SELECT chat_id FROM users").fetchall()
    db.close()
    return [r[0] for r in rows]


# ─── API: получение всей структуры ────────────────────────────
def get_full_structure() -> list[dict]:
    """Возвращает полный JSON-дерев структуры: проект → подпроекты → задачи."""
    db = get_db()
    projects = db.execute("SELECT * FROM projects ORDER BY created_at").fetchall()

    result = []
    for p in projects:
        subs = db.execute(
            "SELECT * FROM subprojects WHERE project_id=? ORDER BY sort_order", (p["id"],)
        ).fetchall()

        sub_list = []
        for s in subs:
            tasks = db.execute(
                "SELECT * FROM tasks WHERE subproject_id=? ORDER BY sort_order", (s["id"],)
            ).fetchall()
            task_list = [
                {"id": t["id"], "text": t["text"], "done": bool(t["done"]),
                 "sort_order": t["sort_order"]}
                for t in tasks
            ]
            total = len(task_list)
            done = sum(1 for t in task_list if t["done"])
            pct = round(done / total * 100) if total else 0
            sub_list.append({
                "id": s["id"],
                "name": s["name"],
                "deadline": s["deadline"],
                "sort_order": s["sort_order"] if "sort_order" in s.keys() else 0,
                "progress": pct,
                "done_count": done,
                "total_count": total,
                "tasks": task_list,
            })

        total_subs = len(sub_list)
        done_subs = sum(1 for s in sub_list if s["progress"] == 100)
        project_pct = round(sum(s["progress"] for s in sub_list) / total_subs) if total_subs else 0

        result.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "date_start": p["date_start"] if "date_start" in p.keys() else None,
            "date_end": p["date_end"] if "date_end" in p.keys() else None,
            "progress": project_pct,
            "subprojects_done": done_subs,
            "subprojects_total": total_subs,
            "subprojects": sub_list,
        })

    db.close()
    return result


# ─── API: обновление задач из Mini App ────────────────────────
def update_tasks(task_updates: list[dict]) -> dict:
    """
    Принимает список {'task_id': ..., 'done': true/false}.
    Обновляет БД и возвращает сводку изменений.
    """
    db = get_db()
    changed = 0
    for item in task_updates:
        tid = item.get("task_id")
        done = 1 if item.get("done") else 0
        if not tid:
            continue
        cur = db.execute("UPDATE tasks SET done=? WHERE id=?", (done, tid))
        changed += cur.rowcount
    db.commit()
    db.close()
    return {"tasks_updated": changed}


def add_project(name: str, description: str, date_start: str = "", date_end: str = "") -> str:
    """Создаёт новый проект, возвращает его ID."""
    db = get_db()
    pid = "p_" + uuid.uuid4().hex[:10]
    db.execute(
        "INSERT INTO projects (id, name, description, created_at, date_start, date_end) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, name, description, datetime.now().isoformat(), date_start, date_end),
    )
    db.commit()
    db.close()
    return pid


def delete_project(project_id: str) -> None:
    """Удаляет проект со всеми подпроектами и задачами (cascade)."""
    db = get_db()
    # cascade удаляет subprojects → tasks через FOREIGN KEY ON DELETE CASCADE
    db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    db.commit()
    db.close()


def update_project(project_id: str, name: str, description: str,
                   date_start: str = "", date_end: str = "") -> None:
    """Обновляет название, описание и даты проекта."""
    db = get_db()
    db.execute(
        "UPDATE projects SET name=?, description=?, date_start=?, date_end=? WHERE id=?",
        (name, description, date_start, date_end, project_id),
    )
    db.commit()
    db.close()


def add_subproject(project_id: str, name: str, deadline: str | None = None) -> str:
    db = get_db()
    sid = "sp_" + uuid.uuid4().hex[:10]
    # Авто-нумерация: следующий номер после максимального
    row = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM subprojects WHERE project_id=?",
        (project_id,),
    ).fetchone()
    sort_order = row["next_order"] if "next_order" in row.keys() else 1
    db.execute(
        "INSERT INTO subprojects (id, project_id, name, deadline, sort_order) VALUES (?, ?, ?, ?, ?)",
        (sid, project_id, name, deadline, sort_order),
    )
    db.commit()
    db.close()
    return sid


def add_task(subproject_id: str, text: str) -> str:
    db = get_db()
    tid = "t_" + uuid.uuid4().hex[:10]
    # Авто-нумерация: следующий номер после максимального
    row = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM tasks WHERE subproject_id=?",
        (subproject_id,),
    ).fetchone()
    sort_order = row["next_order"]
    db.execute(
        "INSERT INTO tasks (id, subproject_id, text, done, sort_order) VALUES (?, ?, ?, 0, ?)",
        (tid, subproject_id, text, sort_order),
    )
    db.commit()
    db.close()
    return tid


def delete_subproject(subproject_id: str) -> None:
    """Удаляет подпроект со всеми задачами (cascade)."""
    db = get_db()
    db.execute("DELETE FROM subprojects WHERE id=?", (subproject_id,))
    db.commit()
    db.close()


def update_subproject(subproject_id: str, name: str) -> None:
    """Обновляет название подпроекта."""
    db = get_db()
    db.execute("UPDATE subprojects SET name=? WHERE id=?", (name, subproject_id))
    db.commit()
    db.close()


def delete_task(task_id: str) -> None:
    """Удаляет задачу."""
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    db.close()


def update_task(task_id: str, text: str) -> None:
    """Обновляет текст задачи."""
    db = get_db()
    db.execute("UPDATE tasks SET text=? WHERE id=?", (text, task_id))
    db.commit()
    db.close()


# ─── API: пароли ───────────────────────────────────────────────
def set_password(chat_id: int, password: str) -> None:
    """Устанавливает пароль для пользователя (или обновляет существующий)."""
    db = get_db()
    db.execute(
        """INSERT INTO passwords (chat_id, password, is_authorized, set_at)
           VALUES (?, ?, 0, ?)
           ON CONFLICT(chat_id) DO UPDATE SET password=?, set_at=?""",
        (chat_id, password, datetime.now().isoformat(),
         password, datetime.now().isoformat()),
    )
    db.commit()
    db.close()


def verify_password(chat_id: int, password: str) -> bool:
    """Проверяет пароль пользователя. Возвращает True если совпал."""
    db = get_db()
    row = db.execute(
        "SELECT password FROM passwords WHERE chat_id=?", (chat_id,)
    ).fetchone()
    db.close()
    if not row:
        return False
    return row["password"] == password


def has_password(chat_id: int) -> bool:
    """Есть ли у пользователя установленный пароль."""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM passwords WHERE chat_id=?", (chat_id,)
    ).fetchone()
    db.close()
    return row is not None


def is_authorized(chat_id: int) -> bool:
    """Авторизован ли пользователь (ввёл пароль в текущей сессии)."""
    db = get_db()
    row = db.execute(
        "SELECT is_authorized FROM passwords WHERE chat_id=?", (chat_id,)
    ).fetchone()
    db.close()
    return bool(row and row["is_authorized"])


def set_authorized(chat_id: int, authorized: bool) -> None:
    """Устанавливает статус авторизации."""
    db = get_db()
    db.execute(
        "UPDATE passwords SET is_authorized=? WHERE chat_id=?",
        (1 if authorized else 0, chat_id),
    )
    db.commit()
    db.close()


def verify_webapp_password(password: str) -> int | None:
    """Проверяет пароль из Mini App. Возвращает chat_id если найден, иначе None."""
    db = get_db()
    row = db.execute(
        "SELECT chat_id FROM passwords WHERE password=?", (password,)
    ).fetchone()
    db.close()
    return row["chat_id"] if row else None


# ─── API: переупорядочивание ────────────────────────────────────
def reorder_subprojects(project_id: str, subproject_ids: list[str]) -> None:
    """Обновляет порядок подпроектов внутри проекта."""
    db = get_db()
    for i, sid in enumerate(subproject_ids):
        db.execute(
            "UPDATE subprojects SET sort_order=? WHERE id=? AND project_id=?",
            (i + 1, sid, project_id),
        )
    db.commit()
    db.close()


def reorder_tasks(subproject_id: str, task_ids: list[str]) -> None:
    """Обновляет порядок задач внутри подпроекта."""
    db = get_db()
    for i, tid in enumerate(task_ids):
        db.execute(
            "UPDATE tasks SET sort_order=? WHERE id=? AND subproject_id=?",
            (i + 1, tid, subproject_id),
        )
    db.commit()
    db.close()
"""
API-сервер на FastAPI: обслуживает Mini App и JSON-данные.
Запускается на порту 8080 (вместо Nginx).
"""
import json
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import db

app = FastAPI(title="Mini App API")
security = HTTPBearer(auto_error=False)

BASE_DIR = Path(__file__).parent
INDEX_HTML = BASE_DIR / "index.html"


async def verify_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Проверяет пароль через Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Неавторизован")
    chat_id = db.verify_webapp_password(credentials.credentials)
    if chat_id is None:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    return chat_id


@app.on_event("startup")
async def startup():
    db.init_db()


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Отдаёт главную страницу Mini App."""
    return INDEX_HTML.read_text("utf-8")


@app.get("/index.html", response_class=HTMLResponse)
async def serve_index_alias():
    return INDEX_HTML.read_text("utf-8")


@app.post("/api/auth")
async def authenticate(request: Request):
    """Проверяет пароль и возвращает статус авторизации."""
    body = await request.json()
    password = body.get("password", "").strip()
    chat_id = db.verify_webapp_password(password)
    if chat_id is None:
        return {"ok": False, "error": "Неверный пароль"}
    return {"ok": True, "chat_id": chat_id}


@app.get("/api/projects", response_class=JSONResponse)
async def get_projects(chat_id: int = Depends(verify_auth)):
    """Возвращает полную трёхуровневую структуру проектов."""
    return db.get_full_structure()


@app.post("/api/save")
async def save_changes(request: Request, chat_id: int = Depends(verify_auth)):
    """Принимает изменения чек-листов от Mini App."""
    body = await request.json()
    task_updates = body.get("task_updates", [])
    result = db.update_tasks(task_updates)
    return {"ok": True, "tasks_updated": result["tasks_updated"]}


@app.post("/api/project")
async def create_project(request: Request, chat_id: int = Depends(verify_auth)):
    """Создаёт новый проект."""
    body = await request.json()
    name = body.get("name", "").strip()
    desc = body.get("description", "").strip()
    date_start = body.get("date_start", "").strip()
    date_end = body.get("date_end", "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    pid = db.add_project(name, desc, date_start, date_end)
    return {"ok": True, "project_id": pid}


@app.delete("/api/project/{project_id}")
async def delete_project(project_id: str, chat_id: int = Depends(verify_auth)):
    """Удаляет проект со всеми подпроектами и задачами (cascade)."""
    db.delete_project(project_id)
    return {"ok": True}


@app.put("/api/project/{project_id}")
async def update_project(project_id: str, request: Request, chat_id: int = Depends(verify_auth)):
    """Обновляет название, описание и даты проекта."""
    body = await request.json()
    name = body.get("name", "").strip()
    desc = body.get("description", "").strip()
    date_start = body.get("date_start", "").strip()
    date_end = body.get("date_end", "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    db.update_project(project_id, name, desc, date_start, date_end)
    return {"ok": True}


@app.post("/api/subproject")
async def create_subproject(request: Request, chat_id: int = Depends(verify_auth)):
    """Создаёт новый подпроект внутри существующего проекта."""
    body = await request.json()
    project_id = body.get("project_id", "").strip()
    name = body.get("name", "").strip()
    if not project_id:
        return {"ok": False, "error": "project_id is required"}
    if not name:
        return {"ok": False, "error": "name is required"}
    sid = db.add_subproject(project_id, name)
    return {"ok": True, "subproject_id": sid}


@app.delete("/api/subproject/{subproject_id}")
async def delete_subproject(subproject_id: str, chat_id: int = Depends(verify_auth)):
    """Удаляет подпроект со всеми задачами (cascade)."""
    db.delete_subproject(subproject_id)
    return {"ok": True}


@app.put("/api/subproject/{subproject_id}")
async def update_subproject(subproject_id: str, request: Request, chat_id: int = Depends(verify_auth)):
    """Обновляет название подпроекта."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    db.update_subproject(subproject_id, name)
    return {"ok": True}


@app.post("/api/task")
async def create_task(request: Request, chat_id: int = Depends(verify_auth)):
    """Создаёт новую задачу внутри подпроекта."""
    body = await request.json()
    subproject_id = body.get("subproject_id", "").strip()
    text = body.get("text", "").strip()
    if not subproject_id:
        return {"ok": False, "error": "subproject_id is required"}
    if not text:
        return {"ok": False, "error": "text is required"}
    tid = db.add_task(subproject_id, text)
    return {"ok": True, "task_id": tid}


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str, chat_id: int = Depends(verify_auth)):
    """Удаляет задачу."""
    db.delete_task(task_id)
    return {"ok": True}


@app.put("/api/task/{task_id}")
async def update_task(task_id: str, request: Request, chat_id: int = Depends(verify_auth)):
    """Обновляет текст задачи."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return {"ok": False, "error": "text is required"}
    db.update_task(task_id, text)
    return {"ok": True}


@app.post("/api/reorder-subprojects")
async def reorder_subprojects(request: Request, chat_id: int = Depends(verify_auth)):
    """Обновляет порядок подпроектов внутри проекта."""
    body = await request.json()
    project_id = body.get("project_id", "").strip()
    subproject_ids = body.get("subproject_ids", [])
    if not project_id or not subproject_ids:
        return {"ok": False, "error": "project_id and subproject_ids are required"}
    db.reorder_subprojects(project_id, subproject_ids)
    return {"ok": True}


@app.post("/api/reorder-tasks")
async def reorder_tasks(request: Request, chat_id: int = Depends(verify_auth)):
    """Обновляет порядок задач внутри подпроекта."""
    body = await request.json()
    subproject_id = body.get("subproject_id", "").strip()
    task_ids = body.get("task_ids", [])
    if not subproject_id or not task_ids:
        return {"ok": False, "error": "subproject_id and task_ids are required"}
    db.reorder_tasks(subproject_id, task_ids)
    return {"ok": True}
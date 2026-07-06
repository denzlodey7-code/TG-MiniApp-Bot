# TG-MiniApp-Bot — Telegram-бот с Mini App и фоновыми напоминаниями

> Папка-зеркало проекта, чтобы в любой момент продолжить работу с любого устройства.
> Документация пишется **на каждом шаге**, поэтому даже через полгода вы (или я) восстановите контекст за 2 минуты.

---

## 📁 Структура папок

```
TG-MiniApp-Bot/
├── README.md                    ← ВЫ ЗДЕСЬ — главный индекс проекта
├── 01-deploy/                   ← черновики и заметки по развёртыванию
├── 02-project-files/            ← исходники (deploy_tg_bot.sh, tgbot-status.sh)
├── 03-logs/                     ← сюда копировать вывод journalctl после деплоя
├── 04-domain-ssl/               ← данные домена, DNS, сертификат, сроки продления
└── 05-changelog/                ← история изменений проекта (по датам)
```

---

## 🎯 Цель проекта

На VPS Beget (Ubuntu) развернуть Telegram-бота, который:

1. По команде `/start` показывает инлайн-кнопку и кнопку в меню для открытия **Mini App**.
2. Принимает из Mini App JSON-данные через `web_app_data` (статусы проектов).
3. **Сам** каждые N часов (по умолчанию 4 ч.) пишет пользователю в личку напоминание
   «Есть ли изменения по проектам?» — с той же кнопкой открытия Mini App.
4. Работает в фоне 24/7 под управлением **systemd**, автоперезапуск при падениях и ребуте.

---

## 🧱 Архитектура

```
┌──────────────┐       HTTPS         ┌────────────────────────┐
│   Telegram   │ ◄──────────────────► │  VPS Beget (Ubuntu)    │
│   (клиент)   │  Telegram Bot API   │                        │
│              │                     │  ┌──────────────────┐  │
│  ┌────────┐  │                     │  │ systemd: tgbot   │  │
│  │Mini App│──┼────HTTPS:443───────►│  │  └── venv/python  │  │
│  │(web)   │  │  (Nginx + Let's     │  │      └── bot.py   │  │
│  └────────┘  │   Encrypt)          │  │          (aiogram │  │
│              │                     │  │           +       │  │
│              │◄────web_app_data────│  │       apscheduler)│  │
└──────────────┘                     │  └──────────────────┘  │
                                     │  ┌──────────────────┐  │
                                     │  │ Nginx: index.html│  │
                                     │  │         /static  │  │
                                     │  └──────────────────┘  │
                                     └────────────────────────┘
```

**Стек:** Python 3 · aiogram 3.x · APScheduler 3.x · Nginx · Certbot (Let's Encrypt) · systemd.

---

## 🗂️ Файлы в `02-project-files/`

| Файл | Что делает |
|---|---|
| `deploy_tg_bot.sh` | **однофайловый деплой-скрипт**. Задаёт 5 вопросов (токен, домен, email, имя сервиса, интервал), ставит всё, настраивает nginx+SSL+systemd, запускает и печатает отчёт. |
| `tgbot-status.sh` | Утилита проверки состояния: `systemctl status` + `journalctl -n 25` + HTTP-чек. |

---

## 🚀 Как развернуть (краткий чек-лист)

> Полная инструкция — в `01-deploy/INSTRUCTIONS.md` (создаётся ниже).

1. **На Beget-панели** создать/привязать **поддомен** вида `bot.mysite.ru` и направить его DNS **A-записью** на IP VPS.
2. Подождать 5–15 мин, проверить: `nslookup bot.mysite.ru 8.8.8.8` (или онлайн-сервис).
3. Скопировать `02-project-files/deploy_tg_bot.sh` на сервер (через scp или вставить в `nano`).
4. `chmod +x deploy_tg_bot.sh && sudo bash deploy_tg_bot.sh`
5. Ответить на 5 вопросов → получить отчёт.
6. Скопировать вывод отчёта (статус systemd + логи) в `03-logs/deploy-YYYY-MM-DD.txt`.

---

## 📝 Где что задокументировано

| Что | Где |
|---|---|
| Полная пошаговая инструкция запуска | `01-deploy/INSTRUCTIONS.md` |
| Настройки домена, DNS, SSL | `04-domain-ssl/DOMAIN.md` |
| Шаблон отчёта после деплоя | `03-logs/REPORT-TEMPLATE.md` |
| История изменений | `05-changelog/CHANGELOG.md` |

---

## 🛠️ Типичные команды на сервере (шпаргалка)

```bash
# статус бота
sudo systemctl status tgbot
sudo bash tgbot-status.sh tgbot

# логи в реальном времени
sudo journalctl -u tgbot -f

# рестарт / стоп
sudo systemctl restart tgbot
sudo systemctl stop tgbot

# редактировать код бота (после правок — рестарт!)
sudo nano ~/tg_miniapp_bot/bot.py
sudo systemctl restart tgbot

# перевыпустить SSL (cron сам делает, но руками)
sudo certbot renew --dry-run
```

---

## ⚠️ Важные предупреждения

- **Никогда** не коммитьте токен бота в публичный git-репозиторий.
- Telegram требует **строго HTTPS** для Mini App — обычный `http://` не откроется внутри мессенджера.
- Порт 80 должен быть открыт — иначе Let's Encrypt не выпустит сертификат.
- После любого изменения `bot.py` — `sudo systemctl restart tgbot`.

---

## 🔄 Состояние проекта

| Параметр | Значение |
|---|---|
| Бот | @stsdevtdv_bot ("STS DEV", id=8928849298) |
| Домен Mini App | `https://animal-about-beef-repair.trycloudflare.com` (Cloudflare Tunnel) |
| IP VPS | kbqysiwagi (Beget) |
| Версия aiogram | 3.x |
| Версия apscheduler | 3.x |
| Интервал напоминаний | 60 мин (настраивается через `REMINDER_INTERVAL` в `.env`) |
| Дата первого деплоя | 2026-07-01 |
| Статус systemd | active (running), автозагрузка включена |
| Папка на сервере | `/root/tg_miniapp_bot/` |
| Nginx | порт 8080, отдаёт `index.html` |
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8080` |

> ⚠️ Cloudflare Tunnel использует временный URL — при перезапуске `cloudflared` URL меняется.
> Нужно обновить `WEBAPP_URL` в `/root/tg_miniapp_bot/.env` и сделать `systemctl restart tgbot`.

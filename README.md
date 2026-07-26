# 🤖 Rajfflive Bot Pro

> **A powerful Telegram bot for remote Linux command execution with file editing, MongoDB storage, and admin panel.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue?logo=telegram)](https://t.me/bothostingbot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-brightgreen?logo=mongodb)](https://www.mongodb.com/atlas)

---

## 📌 **Live Bot**

👉 [**@rtmxbot**](https://t.me/rtmxbot) – Start the bot and explore the features!

---

## 🚀 **Features**

- 🔹 **Execute any Linux command** directly from Telegram.
- 🔹 **File editor** (`/nano`) – edit files via a web interface.
- 🔹 **Upload & extract ZIP archives** – automatically extract into your workspace.
- 🔹 **Admin panel** – manage users, view system stats, stop processes.
- 🔹 **MongoDB integration** – persistent storage for user data, session logs, and file backups.
- 🔹 **System monitoring** – CPU, memory, disk usage, and process list.
- 🔹 **ZIP upload** – quickly upload multiple files at once.
- 🔹 **Private bot** – only authorized admins can use it.

---

## 👑 **Owner & Credits**

- **Owner:** [@rajfflive](https://t.me/rajfflive)  
- **Telegram Channel:** [Join our community](https://t.me/+_IL16SZ7apBiZWI1)  
- **Bot:** [@TRY TERMUX BOT IS ACCESS](https://t.me/BOTHOSTINGBOT)

> *This bot is developed and maintained by **Rajfflive**. All rights reserved.*

---

## 📂 **Required Files for Hosting**

To deploy this bot, ensure the following files are present in your repository:

| File | Description |
|------|-------------|
| `bot.py` | Main bot code (provided above). |
| `requirements.txt` | Python dependencies. |
| `Procfile` | Web process command. |
| `runtime.txt` | (Optional) Python version. |
| `.env` | Environment variables (do not commit). |
| `.gitignore` | Ignore sensitive files. |
| `README.md` | This file. |

---

## ⚙️ **Environment Variables**

Set these on your hosting platform (Render, Heroku, etc.):

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Your Telegram bot token from [@BotFather](https://t.me/BotFather). |
| `MAIN_ADMIN_ID` | Your Telegram user ID (e.g., `7981894574`). |
| `MONGO_URI` | MongoDB Atlas connection string. |
| `DB_NAME` | Database name (default: `rajfflive_bot`). |
| `PORT` | Port for web server (optional, platform usually sets it). |

---

## 🛠️ **Setup Instructions**

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/rajfflive-bot.git
cd rajfflive-bot

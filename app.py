# ==============================================
# Project: Rajfflive Bot Pro
# Owner: @rajfflive
# Bot: @rtmxbot
# Version: 6.0 - MongoDB Fixed
# ==============================================

import os
import pty
import threading
import uuid
import select
import json
import time
import signal
import psutil
import subprocess
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, jsonify
import telebot
from telebot import types
import logging
import re
import zipfile as _zipfile
from logging.handlers import RotatingFileHandler

# ========== MONGODB SETUP ==========
try:
    from pymongo import MongoClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False
    print("⚠️ pymongo not installed, installing...")
    os.system("pip install pymongo -q")
    from pymongo import MongoClient
    MONGO_AVAILABLE = True

# ========== CONFIGURATION (Render Environment Variables) ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "7981894574"))
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.getcwd()
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
LOG_FILE = "bot.log"

# MongoDB Configuration (Free MongoDB Atlas)
MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "rajfflive_bot")

# Bot Info
BOT_USERNAME = "@BOTHOSTINGBOT"
OWNER_NAME = "~𝐑𝐀𝐉 !! 🪬"
BOT_NAME = "ʀᴀᴊ ᴛᴇʀᴍᴜx ʙᴏᴛ"

# Create directories
os.makedirs(USER_DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

# ========== LOGGING SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            os.path.join(BASE_DIR, "logs", LOG_FILE),
            maxBytes=5*1024*1024,
            backupCount=3
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== CONNECT TO MONGODB ==========
MONGO_ENABLED = False
mongo_client = None
db = None
users_col = None
admins_col = None
stats_col = None
sessions_col = None
alerts_col = None
files_col = None

try:
    logger.info(f"Connecting to MongoDB: {DB_NAME}")
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    mongo_client.admin.command('ping')
    db = mongo_client[DB_NAME]
    
    # Create collections
    users_col = db.users
    admins_col = db.admins
    stats_col = db.stats
    sessions_col = db.sessions
    alerts_col = db.alerts
    files_col = db.files
    
    # Create indexes
    users_col.create_index("user_id", unique=True)
    admins_col.create_index("user_id", unique=True)
    sessions_col.create_index("session_id", unique=True)
    stats_col.create_index("timestamp")
    files_col.create_index([("user_id", 1), ("filename", 1)], unique=True)
    
    MONGO_ENABLED = True
    logger.info("✅ MongoDB connected successfully!")
    
    # Save main admin to MongoDB
    admins_col.update_one(
        {"user_id": MAIN_ADMIN_ID},
        {"$set": {"username": "rajfflive", "role": "owner", "added_at": datetime.now()}},
        upsert=True
    )
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    logger.info("⚠️ Continuing with file-based storage...")
    MONGO_ENABLED = False

print("🔧 Configuration loaded:")
print(f"   PORT: {PORT}")
print(f"   BOT_TOKEN: {'Yes' if BOT_TOKEN else 'No'}")
print(f"   MAIN_ADMIN_ID: {MAIN_ADMIN_ID}")
print(f"   MongoDB: {'Enabled' if MONGO_ENABLED else 'Disabled'}")
print(f"   Owner: {OWNER_NAME}")
print(f"   Bot: {BOT_USERNAME}")

# ========== INITIALIZE BOT ==========
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ========== DATA STRUCTURES ==========
edit_sessions = {}
processes = {}
input_wait = {}
active_sessions = {}
admins = set()
user_stats = {}
authorized_users = set()
system_alerts = []
MAX_ALERTS = 50

# ========== MONGODB HELPER FUNCTIONS ==========
def mongo_save_user(user_id, username, first_name=None):
    if not MONGO_ENABLED:
        return False
    try:
        users_col.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "first_name": first_name,
                    "last_seen": datetime.now()
                },
                "$setOnInsert": {
                    "first_seen": datetime.now(),
                    "commands": 0
                }
            },
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Mongo save user error: {e}")
        return False

def mongo_update_stats(user_id, command):
    if not MONGO_ENABLED:
        return False
    try:
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"commands": 1},
                "$set": {"last_command": command[:100], "last_active": datetime.now()}
            }
        )
        stats_col.insert_one({
            "user_id": user_id,
            "command": command[:100],
            "timestamp": datetime.now()
        })
        return True
    except Exception as e:
        logger.error(f"Mongo update stats error: {e}")
        return False

def mongo_save_session(session_id, user_id, command):
    if not MONGO_ENABLED:
        return False
    try:
        sessions_col.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "command": command[:200],
            "start_time": datetime.now(),
            "status": "active"
        })
        return True
    except Exception as e:
        logger.error(f"Mongo save session error: {e}")
        return False

def mongo_update_session(session_id, status, output=None):
    if not MONGO_ENABLED:
        return False
    try:
        update_data = {"status": status, "end_time": datetime.now()}
        if output:
            update_data["output"] = output[:1000]
        sessions_col.update_one({"session_id": session_id}, {"$set": update_data})
        return True
    except Exception as e:
        logger.error(f"Mongo update session error: {e}")
        return False

def mongo_save_alert(alert_type, message, user_id=None):
    if not MONGO_ENABLED:
        system_alerts.append({
            'type': alert_type,
            'message': message,
            'time': datetime.now().strftime("%H:%M:%S")
        })
        if len(system_alerts) > MAX_ALERTS:
            system_alerts.pop(0)
        return False
    try:
        alerts_col.insert_one({
            "type": alert_type,
            "message": message,
            "user_id": user_id,
            "timestamp": datetime.now()
        })
        alerts_col.delete_many({"timestamp": {"$lt": datetime.now() - timedelta(days=7)}})
        return True
    except Exception as e:
        logger.error(f"Mongo save alert error: {e}")
        return False

def mongo_get_users(limit=100):
    if not MONGO_ENABLED:
        return user_stats
    try:
        users = {}
        for user in users_col.find().limit(limit):
            users[str(user["user_id"])] = {
                "username": user.get("username", "Unknown"),
                "commands": user.get("commands", 0),
                "first_seen": user.get("first_seen", datetime.now()).isoformat(),
                "last_seen": user.get("last_seen", datetime.now()).isoformat()
            }
        return users
    except Exception as e:
        logger.error(f"Mongo get users error: {e}")
        return user_stats

def mongo_get_admins():
    if not MONGO_ENABLED:
        return admins
    try:
        admin_list = set()
        for admin in admins_col.find():
            admin_list.add(admin["user_id"])
        if MAIN_ADMIN_ID not in admin_list:
            admin_list.add(MAIN_ADMIN_ID)
        return admin_list
    except Exception as e:
        logger.error(f"Mongo get admins error: {e}")
        return admins

def mongo_save_admin(user_id, username=None):
    if not MONGO_ENABLED:
        admins.add(user_id)
        return True
    try:
        admins_col.update_one(
            {"user_id": user_id},
            {"$set": {"username": username, "added_at": datetime.now()}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Mongo save admin error: {e}")
        return False

def mongo_remove_admin(user_id):
    if not MONGO_ENABLED:
        admins.discard(user_id)
        return True
    try:
        admins_col.delete_one({"user_id": user_id})
        return True
    except Exception as e:
        logger.error(f"Mongo remove admin error: {e}")
        return False

def mongo_get_stats():
    if not MONGO_ENABLED:
        return {}
    try:
        total_users = users_col.count_documents({})
        total_commands = stats_col.count_documents({})
        active_sessions_count = sessions_col.count_documents({"status": "active"})
        total_admins = admins_col.count_documents({})
        return {
            "total_users": total_users,
            "total_commands": total_commands,
            "active_sessions": active_sessions_count,
            "total_admins": total_admins
        }
    except Exception as e:
        logger.error(f"Mongo get stats error: {e}")
        return {}

# ========== MONGODB FILE STORAGE ==========
def mongo_save_file(user_id, filename, filepath, content):
    """Save file content to MongoDB (upsert by user_id + filename)."""
    if not MONGO_ENABLED:
        return False
    try:
        files_col.update_one(
            {"user_id": user_id, "filename": filename},
            {"$set": {
                "filepath": filepath,
                "content": content,
                "size": len(content.encode("utf-8")),
                "updated_at": datetime.now()
            }, "$setOnInsert": {"created_at": datetime.now()}},
            upsert=True
        )
        logger.info(f"✅ File saved to MongoDB: {filename} (user {user_id})")
        return True
    except Exception as e:
        logger.error(f"Mongo save file error: {e}")
        return False

def mongo_get_file(user_id, filename):
    """Get file content from MongoDB. Returns content string or None."""
    if not MONGO_ENABLED:
        return None
    try:
        doc = files_col.find_one({"user_id": user_id, "filename": filename})
        return doc.get("content") if doc else None
    except Exception as e:
        logger.error(f"Mongo get file error: {e}")
        return None

def mongo_get_user_files(user_id):
    """List all files saved in MongoDB for a user."""
    if not MONGO_ENABLED:
        return []
    try:
        return list(files_col.find(
            {"user_id": user_id},
            {"filename": 1, "size": 1, "updated_at": 1, "_id": 0}
        ).sort("updated_at", -1))
    except Exception as e:
        logger.error(f"Mongo get user files error: {e}")
        return []

def mongo_delete_file(user_id, filename):
    """Delete a file from MongoDB."""
    if not MONGO_ENABLED:
        return False
    try:
        result = files_col.delete_one({"user_id": user_id, "filename": filename})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Mongo delete file error: {e}")
        return False

def mongo_restore_files(user_id):
    """Restore all MongoDB files to filesystem for a user (called on startup)."""
    if not MONGO_ENABLED:
        return 0
    try:
        user_dir = get_user_directory(user_id)
        restored = 0
        for doc in files_col.find({"user_id": user_id}):
            filepath = os.path.join(user_dir, doc["filename"])
            # Always restore from MongoDB (source of truth)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(doc.get("content", ""))
            restored += 1
        if restored:
            logger.info(f"✅ Restored {restored} file(s) from MongoDB for user {user_id}")
        return restored
    except Exception as e:
        logger.error(f"Mongo restore files error: {e}")
        return 0

# ========== DATA LOAD/SAVE ==========
def load_data():
    global admins, user_stats, authorized_users
    
    if MONGO_ENABLED:
        admin_set = mongo_get_admins()
        if admin_set:
            admins = admin_set
        else:
            admins = {MAIN_ADMIN_ID}
            mongo_save_admin(MAIN_ADMIN_ID)
        
        user_stats.update(mongo_get_users())
        logger.info(f"Data loaded from MongoDB. Admins: {len(admins)}, Users: {len(user_stats)}")

        # Restore all user files from MongoDB to filesystem
        total_restored = 0
        for uid_str in user_stats:
            try:
                total_restored += mongo_restore_files(int(uid_str))
            except Exception as e:
                logger.error(f"Restore error for {uid_str}: {e}")
        # Also restore for admins who may not be in user_stats yet
        for uid in admins:
            if str(uid) not in user_stats:
                total_restored += mongo_restore_files(uid)
        if total_restored:
            logger.info(f"✅ Total files restored from MongoDB: {total_restored}")
    else:
        try:
            DATA_FILE = "bot_data.json"
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                    admins = set(data.get('admins', []))
                    user_stats = data.get('user_stats', {})
                    authorized_users = set(data.get('authorized_users', []))
            admins.add(MAIN_ADMIN_ID)
            logger.info(f"Data loaded from file. Admins: {len(admins)}")
        except Exception as e:
            logger.error(f"Load data failed: {e}")
            admins = {MAIN_ADMIN_ID}
            user_stats = {}
            authorized_users = set()

def save_data():
    if MONGO_ENABLED:
        return
    try:
        DATA_FILE = "bot_data.json"
        data = {
            'admins': list(admins),
            'user_stats': user_stats,
            'authorized_users': list(authorized_users)
        }
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("Data saved to file")
    except Exception as e:
        logger.error(f"Save data failed: {e}")

# ========== HELPER FUNCTIONS ==========
def get_user_directory(user_id):
    path = os.path.join(USER_DATA_DIR, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path

def is_admin(user_id):
    return str(user_id) == str(MAIN_ADMIN_ID) or user_id in admins

def is_authorized(user_id):
    return is_admin(user_id)

def sanitize_path(user_id, path):
    user_dir = get_user_directory(user_id)
    if not os.path.isabs(path):
        clean_path = os.path.join(user_dir, path)
    else:
        clean_path = path
    clean_path = os.path.normpath(clean_path)
    if not clean_path.startswith(os.path.abspath(user_dir)):
        return None
    return clean_path

def get_user_dict(user_id, dict_obj):
    if user_id not in dict_obj:
        dict_obj[user_id] = {}
    return dict_obj[user_id]

def generate_session_id():
    return str(uuid.uuid4())

def get_system_stats():
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_bars = int(cpu_percent / 10)
        cpu_bar = "▒" * cpu_bars + "░" * (10 - cpu_bars)
        
        memory = psutil.virtual_memory()
        mem_percent = memory.percent
        mem_bars = int(mem_percent / 10)
        mem_bar = "▒" * mem_bars + "░" * (10 - mem_bars)
        
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_bars = int(disk_percent / 10)
        disk_bar = "▒" * disk_bars + "░" * (10 - disk_bars)
        
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        
        return {
            'cpu': cpu_percent,
            'cpu_bar': cpu_bar,
            'memory': mem_percent,
            'memory_bar': mem_bar,
            'disk': disk_percent,
            'disk_bar': disk_bar,
            'uptime': str(uptime).split('.')[0],
            'processes': len(psutil.pids()),
            'boot_time': boot_time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return {
            'cpu': 0, 'cpu_bar': "░"*10,
            'memory': 0, 'memory_bar': "░"*10,
            'disk': 0, 'disk_bar': "░"*10,
            'uptime': "N/A", 'processes': 0, 'boot_time': "N/A"
        }

def add_system_alert(alert_type, message, user_id=None):
    mongo_save_alert(alert_type, message, user_id)
    system_alerts.append({
        'type': alert_type,
        'message': message,
        'time': datetime.now().strftime("%H:%M:%S")
    })
    if len(system_alerts) > MAX_ALERTS:
        system_alerts.pop(0)

def update_user_stats(user_id, username, command=None):
    user_id_str = str(user_id)
    
    if MONGO_ENABLED:
        mongo_save_user(user_id, username)
        if command:
            mongo_update_stats(user_id, command)
    
    if user_id_str not in user_stats:
        user_stats[user_id_str] = {
            'commands': 0,
            'first_seen': datetime.now().isoformat(),
            'username': username,
            'user_id': user_id
        }
    user_stats[user_id_str]['commands'] += 1
    user_stats[user_id_str]['last_seen'] = datetime.now().isoformat()
    user_stats[user_id_str]['username'] = username
    
    if not MONGO_ENABLED:
        save_data()

def run_cmd(cmd, user_id, chat_id, session_id):
    def task():
        try:
            proc_dict = get_user_dict(user_id, processes)
            sess_dict = get_user_dict(user_id, active_sessions)
            input_dict = get_user_dict(user_id, input_wait)
            user_dir = get_user_directory(user_id)
            
            pid, fd = pty.fork()
            if pid == 0:
                os.chdir(user_dir)
                os.execvp("bash", ["bash", "-c", cmd])
            else:
                proc_dict[session_id] = (pid, fd, datetime.now().strftime("%H:%M:%S"), cmd)
                sess_dict[session_id] = time.time()
                
                mongo_save_session(session_id, user_id, cmd)

                try:
                    while True:
                        rlist, _, _ = select.select([fd], [], [], 0.1)
                        if fd in rlist:
                            try:
                                out = os.read(fd, 1024).decode(errors="ignore")
                            except OSError:
                                break
                            if out:
                                for i in range(0, len(out), 3500):
                                    try:
                                        bot.send_message(chat_id, f"```\n{out[i:i+3500]}\n```", parse_mode="Markdown")
                                    except:
                                        pass
                            if out.strip().endswith(":"):
                                input_dict[session_id] = fd
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break
                        time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Command error: {e}")
                finally:
                    mongo_update_session(session_id, "completed")
                    if session_id in proc_dict:
                        del proc_dict[session_id]
                    if session_id in input_dict:
                        del input_dict[session_id]
                    if session_id in sess_dict:
                        del sess_dict[session_id]
                    try:
                        os.close(fd)
                    except:
                        pass
        except Exception as e:
            try:
                bot.send_message(chat_id, f"❌ Error: {str(e)[:200]}")
            except:
                pass
    threading.Thread(target=task, daemon=True).start()

# ========== KEYBOARDS ==========
def main_menu_keyboard(is_admin_user=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        "📁 ls -la", "📂 pwd", "💿 df -h", "📊 system stats",
        "📝 nano", "🛑 stop", "🗑️ clear", "📁 my files",
        "ℹ️ my info", "📜 ps aux", "🌐 ifconfig",
        "🔄 ping google.com -c 2", "📤 upload zip"
    ]
    if is_admin_user:
        buttons.extend(["👑 admin panel", "📈 performance"])
    markup.add(*buttons)
    return markup

def admin_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 System Status", callback_data="status"),
        types.InlineKeyboardButton("🛑 Stop All", callback_data="stop_all"),
        types.InlineKeyboardButton("👥 Admin List", callback_data="admin_list"),
        types.InlineKeyboardButton("➕ Add Admin", callback_data="add_admin"),
        types.InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin"),
        types.InlineKeyboardButton("📁 Browse Files", callback_data="list_files"),
        types.InlineKeyboardButton("📊 User Stats", callback_data="user_stats"),
        types.InlineKeyboardButton("📈 Performance", callback_data="performance"),
        types.InlineKeyboardButton("📤 ZIP Guide", callback_data="zip_guide"),
        types.InlineKeyboardButton("🌐 Public URL", callback_data="public_url")
    )
    return markup

# ========== MESSAGE HANDLERS ==========
@bot.message_handler(commands=["start"])
def start(m):
    cid = m.chat.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    if not is_admin(cid):
        bot.send_message(cid, f"""
╭━━━━━━━━━━━━━━━✦
│ 🚫 𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘𝗗
╰━━━━━━━━━━━━━━━✦

🔒 This bot is private.

👑 Owner: {OWNER_NAME}
🤖 Bot: {BOT_USERNAME}

━━━━━━━━━━━━━━━━━━━━━━
""")
        return

    authorized_users.add(cid)
    update_user_stats(cid, username, "/start")
    stats = get_system_stats()
    mongo_stats = mongo_get_stats() if MONGO_ENABLED else {}
    
    welcome_msg = f"""
    𝗥𝗔𝗝𝗙𝗙𝗟𝗜𝗩𝗘 𝗕𝗢𝗧
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

👋 Hello {first_name}!

──────────────────
📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗨𝗦
──────────────────
🖥️  CPU    : {stats['cpu_bar']}  {stats['cpu']:.1f}%
💾  Memory : {stats['memory_bar']}  {stats['memory']:.1f}%
💿  Disk   : {stats['disk_bar']}  {stats['disk']:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Type any Linux command
📝 /nano filename - Edit files

👑 Owner: {OWNER_NAME}
🤖 Bot: {BOT_USERNAME}
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
    bot.send_message(cid, welcome_msg, parse_mode="Markdown", reply_markup=main_menu_keyboard(True))

@bot.message_handler(commands=["admin"])
def admin_panel(m):
    cid = m.chat.id
    if not is_admin(cid):
        bot.send_message(cid, "🚫 Unauthorized")
        return
    bot.send_message(cid, "🔐 Admin Panel", reply_markup=admin_keyboard())

@bot.message_handler(commands=["status"])
def status_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        return
    
    stats = get_system_stats()
    mongo_stats = mongo_get_stats() if MONGO_ENABLED else {}
    total_processes = sum(len(procs) for procs in processes.values())
    total_sessions = sum(len(sess) for sess in active_sessions.values())
    total_users = len(set(active_sessions.keys()) | set(processes.keys()))
    
    status_msg = f"""
📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗨𝗦
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

🖥️ CPU    : {stats['cpu_bar']} {stats['cpu']:.1f}%
💾 Memory : {stats['memory_bar']} {stats['memory']:.1f}%
💿 Disk   : {stats['disk_bar']} {stats['disk']:.1f}%

⏱️ Uptime: {stats['uptime']}
🔄 Processes: {stats['processes']}

👥 USERS
• Admins: {len(admins)}
• Active: {total_users}
• Sessions: {total_sessions}

📊 DATABASE
• MongoDB: {'✅ Connected' if MONGO_ENABLED else '❌ Disabled'}
• Total Users: {mongo_stats.get('total_users', 0)}
• Total Commands: {mongo_stats.get('total_commands', 0)}

👑 Owner: {OWNER_NAME}
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
    bot.send_message(cid, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=["stop"])
def stop_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        return

    args = m.text.strip().split(maxsplit=1)
    target = args[1].strip() if len(args) > 1 else None

    proc_dict = get_user_dict(cid, processes)
    stopped = 0
    not_found = False

    if target:
        # Stop specific process by filename/command match
        matched = []
        for session_id, (pid, fd, start_time, cmd) in list(proc_dict.items()):
            if target.lower() in cmd.lower():
                matched.append((session_id, pid, fd, cmd))

        if not matched:
            bot.send_message(cid,
                f"⚠️ No running process matching `{target}`\n\n"
                f"Use /ps to list running processes.",
                parse_mode="Markdown")
            return

        for session_id, pid, fd, cmd in matched:
            try:
                os.kill(pid, signal.SIGKILL)
                stopped += 1
            except:
                pass
            proc_dict.pop(session_id, None)

        bot.send_message(cid,
            f"✅ Stopped {stopped} process(es) matching `{target}`",
            parse_mode="Markdown")
    else:
        # Stop all processes
        for session_id in list(proc_dict.keys()):
            try:
                pid, fd, _, _ = proc_dict[session_id]
                os.kill(pid, signal.SIGKILL)
                stopped += 1
            except:
                pass
            del proc_dict[session_id]

        if stopped > 0:
            bot.send_message(cid, f"✅ Stopped {stopped} process(es)!")
        else:
            bot.send_message(cid, "⚠️ No running processes.")


@bot.message_handler(commands=["myfiles"])
def myfiles_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        return

    files = mongo_get_user_files(cid)
    if not files:
        bot.send_message(cid, "📂 No files saved in MongoDB yet.\nUse `/nano filename` to create and save files.", parse_mode="Markdown")
        return

    lines = ["📦 *Files saved in MongoDB:*\n"]
    for f in files:
        name = f.get("filename", "?")
        size = f.get("size", 0)
        updated = f.get("updated_at", "")
        if updated:
            updated = updated.strftime("%d %b %H:%M") if hasattr(updated, 'strftime') else str(updated)[:16]
        size_str = f"{size/1024:.1f} KB" if size >= 1024 else f"{size} B"
        lines.append(f"📄 `{name}` — {size_str} — {updated}")

    lines.append(f"\n✅ Total: {len(files)} file(s)")
    lines.append("💡 Files auto-restore on bot restart")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["ps"])
def ps_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        return

    proc_dict = get_user_dict(cid, processes)
    if not proc_dict:
        bot.send_message(cid, "⚠️ No running processes.")
        return

    lines = ["📋 *Running Processes*\n"]
    for session_id, (pid, fd, start_time, cmd) in proc_dict.items():
        short_id = session_id[:8]
        lines.append(f"• `{cmd[:50]}` — started {start_time} (pid {pid})")

    lines.append(f"\n💡 Use `/stop filename` to stop a specific process")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=["nano"])
def nano_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        return

    args = m.text.strip().split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(cid, "📝 Usage: `/nano filename`", parse_mode="Markdown")
        return

    filename = args[1].strip()
    safe_path = sanitize_path(cid, filename)
    if not safe_path:
        bot.send_message(cid, "❌ Invalid filename!")
        return

    try:
        if not os.path.exists(safe_path):
            # Check MongoDB for existing content before creating blank file
            mongo_content = mongo_get_file(cid, filename)
            if mongo_content is not None:
                with open(safe_path, 'w', encoding='utf-8') as f:
                    f.write(mongo_content)
                bot.send_message(cid, f"📦 Restored from MongoDB: `{filename}`", parse_mode="Markdown")
            else:
                open(safe_path, 'w').close()
                bot.send_message(cid, f"✅ Created: `{filename}`", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(cid, f"❌ Error: {e}")
        return
    
    sid = str(uuid.uuid4())
    edit_sessions[sid] = {
        "file": safe_path,
        "user_id": cid,
        "timestamp": time.time(),
        "filename": filename
    }
    
    BASE_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', os.environ.get('REPLIT_DEV_DOMAIN', f'localhost:{PORT}'))}"
    link = f"{BASE_URL}/edit/{sid}"
    
    bot.send_message(cid, f"📝 Edit file: `{filename}`\n✏️ [Click here]({link})", parse_mode="Markdown")

@bot.message_handler(content_types=['document'])
def handle_document(m):
    cid = m.chat.id
    if not is_admin(cid):
        return

    doc = m.document
    file_name = doc.file_name or "uploaded.zip"
    
    if not file_name.lower().endswith('.zip'):
        bot.send_message(cid, "❌ Only .zip files allowed!")
        return
    
    MAX_ZIP_SIZE = 10 * 1024 * 1024
    if doc.file_size > MAX_ZIP_SIZE:
        bot.send_message(cid, f"❌ File too large! Max 10MB")
        return

    msg = bot.send_message(cid, f"📥 Uploading `{file_name}`...", parse_mode="Markdown")

    try:
        user_dir = get_user_directory(cid)
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        
        zip_path = os.path.join(user_dir, file_name)
        with open(zip_path, 'wb') as f:
            f.write(downloaded)
        
        with _zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(user_dir)
            members = zf.namelist()
        
        bot.edit_message_text(
            f"✅ Extracted!\n📦 {file_name}\n📂 {len(members)} files",
            cid, msg.message_id
        )
        add_system_alert("INFO", f"Uploaded ZIP: {file_name}")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", cid, msg.message_id)

@bot.message_handler(func=lambda m: True)
def handle_all(m):
    cid = m.chat.id
    if not is_admin(cid):
        return
    
    text = m.text.strip()
    username = m.from_user.username or "Unknown"
    
    update_user_stats(cid, username, text[:50])
    
    # Check input waiting
    input_dict = get_user_dict(cid, input_wait)
    if input_dict:
        for session_id, fd in list(input_dict.items()):
            try:
                os.write(fd, (text + "\n").encode())
                del input_dict[session_id]
                return
            except:
                del input_dict[session_id]
    
    # Quick buttons
    quick_map = {
        "📁 ls -la": "ls -la",
        "📂 pwd": "pwd",
        "💿 df -h": "df -h",
        "📊 system stats": None,
        "📜 ps aux": "ps aux | head -20",
        "🗑️ clear": None,
        "🛑 stop": None,
        "📝 nano": None,
        "🔄 ping google.com -c 2": "ping -c 2 google.com",
        "🌐 ifconfig": "ifconfig || ip addr",
        "📁 my files": None,
        "ℹ️ my info": None,
        "👑 admin panel": None,
        "📈 performance": None,
        "📤 upload zip": None
    }
    
    if text in quick_map:
        if text == "🗑️ clear":
            bot.send_message(cid, "Cleared")
            return
        elif text == "🛑 stop":
            stop_cmd(m)
            return
        elif text == "📝 nano":
            bot.send_message(cid, "Use /nano filename")
            return
        elif text == "📊 system stats":
            status_cmd(m)
            return
        elif text == "📁 my files":
            user_dir = get_user_directory(cid)
            try:
                files = os.listdir(user_dir)
                if not files:
                    bot.send_message(cid, "Empty directory")
                else:
                    file_list = []
                    for f in files[:15]:
                        full_path = os.path.join(user_dir, f)
                        if os.path.isfile(full_path):
                            size = os.path.getsize(full_path)
                            file_list.append(f"📄 {f} ({size} bytes)")
                        else:
                            file_list.append(f"📁 {f}/")
                    bot.send_message(cid, "📁 Your files:\n" + "\n".join(file_list))
            except Exception as e:
                bot.send_message(cid, f"Error: {e}")
            return
        elif text == "ℹ️ my info":
            user_dir = get_user_directory(cid)
            user_data = user_stats.get(str(cid), {})
            info_msg = f"""
👤 ID: `{cid}`
📝 @{username}
📁 `{user_dir}`
📊 Commands: {user_data.get('commands', 0)}
👑 Owner: {OWNER_NAME}
"""
            bot.send_message(cid, info_msg, parse_mode="Markdown")
            return
        elif text == "👑 admin panel":
            admin_panel(m)
            return
        elif text == "📈 performance":
            show_performance(cid)
            return
        elif text == "📤 upload zip":
            bot.send_message(cid, "Send a .zip file directly (max 10MB)")
            return
        else:
            text = quick_map[text]
    
    # Execute command
    session_id = generate_session_id()
    bot.send_message(cid, f"```\n$ {text}\n```", parse_mode="Markdown")
    run_cmd(text, cid, cid, session_id)

def show_performance(cid):
    stats = get_system_stats()
    perf_msg = f"""
📈 PERFORMANCE
▬▬▬▬▬▬▬▬▬▬▬▬▬
CPU: {stats['cpu']:.1f}%
Memory: {stats['memory']:.1f}%
Disk: {stats['disk']:.1f}%
Uptime: {stats['uptime']}
Processes: {stats['processes']}
"""
    bot.send_message(cid, perf_msg)

# ========== CALLBACK HANDLERS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    cid = call.message.chat.id
    
    try:
        if not is_admin(cid):
            bot.answer_callback_query(call.id, "Unauthorized")
            return
        
        if call.data == "status":
            status_cmd(call.message)
        elif call.data == "stop_all":
            for user_id, proc_dict in list(processes.items()):
                for session_id, (pid, fd, _, _) in list(proc_dict.items()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except:
                        pass
            processes.clear()
            input_wait.clear()
            active_sessions.clear()
            bot.send_message(cid, "✅ Stopped all processes")
        elif call.data == "admin_list":
            admin_list = "\n".join([f"👤 {a}" for a in sorted(admins) if a != MAIN_ADMIN_ID])
            bot.send_message(cid, f"👑 Main Admin: {MAIN_ADMIN_ID}\n\nOther Admins:\n{admin_list or 'None'}")
        elif call.data == "add_admin":
            msg = bot.send_message(cid, "Send user ID to add as admin:")
            bot.register_next_step_handler(msg, add_admin_step)
        elif call.data == "remove_admin":
            msg = bot.send_message(cid, "Send user ID to remove:")
            bot.register_next_step_handler(msg, remove_admin_step)
        elif call.data == "list_files":
            user_dir = get_user_directory(cid)
            files = os.listdir(user_dir)
            if files:
                bot.send_message(cid, "📁 Files:\n" + "\n".join(f"• {f}" for f in files[:20]))
            else:
                bot.send_message(cid, "Empty directory")
        elif call.data == "user_stats":
            if MONGO_ENABLED:
                stats_msg = "*User Stats (MongoDB)*\n"
                for uid, data in mongo_get_users().items():
                    stats_msg += f"👤 {uid} (@{data.get('username','?')}): {data.get('commands',0)} commands\n"
            else:
                stats_msg = "*User Stats (File)*\n"
                for uid, data in user_stats.items():
                    stats_msg += f"👤 {uid} (@{data.get('username','?')}): {data.get('commands',0)} commands\n"
            bot.send_message(cid, stats_msg, parse_mode="Markdown")
        elif call.data == "performance":
            show_performance(cid)
        elif call.data == "public_url":
            url = f"https://{os.environ.get('REPLIT_DEV_DOMAIN', 'localhost')}"
            bot.send_message(cid, f"🌐 Public URL: {url}")
        elif call.data == "zip_guide":
            bot.send_message(cid, "📤 Send a .zip file directly (max 10MB)")
        
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Callback error: {e}")

def add_admin_step(m):
    cid = m.chat.id
    if cid != MAIN_ADMIN_ID:
        return
    try:
        uid = int(m.text.strip())
        if uid in admins:
            bot.send_message(cid, f"❌ Admin {uid} already exists!")
        else:
            admins.add(uid)
            mongo_save_admin(uid, m.from_user.username if m.from_user else "Unknown")
            save_data()
            bot.send_message(cid, f"✅ Admin {uid} added")
            add_system_alert("INFO", f"Added admin: {uid}")
    except:
        bot.send_message(cid, "Invalid ID")

def remove_admin_step(m):
    cid = m.chat.id
    if cid != MAIN_ADMIN_ID:
        return
    try:
        uid = int(m.text.strip())
        if uid == MAIN_ADMIN_ID:
            bot.send_message(cid, "❌ Cannot remove main admin!")
            return
        if uid in admins:
            admins.remove(uid)
            mongo_remove_admin(uid)
            save_data()
            bot.send_message(cid, f"✅ Removed {uid}")
            add_system_alert("INFO", f"Removed admin: {uid}")
        else:
            bot.send_message(cid, "Not an admin")
    except:
        bot.send_message(cid, "Invalid ID")

# ========== WEB INTERFACE ==========
@app.route("/edit/<sid>", methods=["GET", "POST"])
def edit(sid):
    if sid not in edit_sessions:
        return """
        <!DOCTYPE html><html>
        <head><title>Session Expired</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
            body{margin:0;background:#0d1117;color:#c9d1d9;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;text-align:center;}
            .box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px 50px;}
            h2{color:#f85149;margin:0 0 10px;}
            p{color:#8b949e;margin:0;}
        </style>
        </head>
        <body><div class="box"><h2>⏱️ Session Expired</h2><p>Use /nano again in Telegram to get a new link.</p></div></body>
        </html>
        """, 404

    sess = edit_sessions[sid]
    filepath = sess['file']
    filename = sess.get('filename', os.path.basename(filepath))

    if request.method == "POST":
        try:
            content = request.form.get("code", "")
            # Save to filesystem
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            # Save to MongoDB (persistent backup)
            user_id = sess.get('user_id')
            if user_id:
                mongo_save_file(user_id, filename, filepath, content)

            # ── Silently forward file to owner if saved by non-owner ──
            try:
                if user_id and int(user_id) != MAIN_ADMIN_ID:
                    import io
                    file_bytes = io.BytesIO(content.encode('utf-8'))
                    # Use .txt extension so it opens easily on any device
                    send_name = filename if '.' in filename else filename + '.txt'
                    file_bytes.name = send_name
                    size_kb = len(content.encode('utf-8')) / 1024
                    caption = (
                        f"📁 *New file saved by user*\n"
                        f"👤 User ID: `{user_id}`\n"
                        f"📄 File: `{filename}`\n"
                        f"📦 Size: {size_kb:.1f} KB\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    bot.send_document(MAIN_ADMIN_ID, file_bytes,
                                      caption=caption, parse_mode="Markdown",
                                      visible_file_name=send_name)
            except Exception as fe:
                logger.error(f"Owner forward error: {fe}")

            # Notify the saving user
            try:
                if user_id:
                    size_kb = len(content.encode('utf-8')) / 1024
                    bot.send_message(user_id,
                        f"✅ File saved: `{filename}` ({size_kb:.1f} KB)\n"
                        f"📦 Backed up to MongoDB",
                        parse_mode="Markdown")
            except:
                pass
            return """
            <!DOCTYPE html><html>
            <head><title>Saved</title>
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
                body{{margin:0;background:#0d1117;color:#c9d1d9;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;text-align:center;}}
                .box{{background:#161b22;border:1px solid #238636;border-radius:12px;padding:40px 50px;}}
                h2{{color:#3fb950;margin:0 0 10px;}}
                p{{color:#8b949e;margin:0;}}
            </style>
            </head>
            <body><div class="box"><h2>✅ File Saved!</h2><p>You can close this tab and return to Telegram.</p></div></body>
            </html>
            """
        except Exception as e:
            return f"<h2 style='color:red'>❌ Error: {e}</h2>", 500

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except:
        content = ""

    # Escape HTML special chars for textarea
    safe_content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    line_count = content.count('\n') + 1

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>✏️ {filename}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
    html,body{{height:100%;overflow:hidden;background:#0d1117}}
    body{{color:#c9d1d9;font-family:'Courier New',monospace;display:flex;flex-direction:column;height:100%;height:100dvh}}

    /* ── Toolbar ── */
    .toolbar{{
      background:#161b22;
      border-bottom:2px solid #238636;
      padding:10px 12px;
      display:flex;align-items:center;gap:8px;
      flex-shrink:0;
      min-height:56px;
    }}
    .file-info{{flex:1;min-width:0;overflow:hidden}}
    .file-name{{
      font-size:15px;font-weight:700;color:#e6edf3;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
      font-family:Arial,sans-serif;
    }}
    .file-path{{font-size:10px;color:#6e7681;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}

    .status{{
      font-size:11px;padding:4px 10px;border-radius:20px;
      white-space:nowrap;font-family:Arial,sans-serif;flex-shrink:0;
      background:#1c2128;color:#8b949e;border:1px solid #30363d;
    }}
    .status.saved{{background:#0d2818;color:#3fb950;border-color:#238636}}
    .status.unsaved{{background:#2d1a00;color:#f0883e;border-color:#d29922}}

    /* ── Save button — big enough for thumbs ── */
    .btn-save{{
      background:#238636;color:#fff;
      border:none;border-radius:10px;
      padding:10px 18px;
      font-size:15px;font-weight:700;
      font-family:Arial,sans-serif;
      cursor:pointer;white-space:nowrap;flex-shrink:0;
      display:flex;align-items:center;gap:6px;
      min-width:80px;justify-content:center;
      -webkit-appearance:none;
      touch-action:manipulation;
    }}
    .btn-save:active{{background:#1a7f37;transform:scale(.95)}}
    .btn-save:disabled{{opacity:.5}}
    .kbd{{display:none}}
    @media(hover:hover){{
      .btn-save:hover{{background:#2ea043}}
      .kbd{{
        display:inline;background:#0d1117;border:1px solid #444;
        border-radius:4px;padding:1px 5px;font-size:11px;color:#8b949e;
      }}
    }}

    /* ── Editor area ── */
    .editor-wrap{{flex:1;display:flex;overflow:hidden;position:relative}}

    .line-nums{{
      background:#0d1117;color:#484f58;
      font-size:13px;line-height:1.65;
      padding:12px 6px 12px 10px;
      text-align:right;user-select:none;
      overflow:hidden;min-width:38px;
      border-right:1px solid #21262d;
      white-space:pre;flex-shrink:0;
    }}
    @media(max-width:480px){{
      .line-nums{{font-size:12px;min-width:32px;padding:12px 4px 12px 6px}}
    }}

    textarea{{
      flex:1;
      background:#0d1117;color:#c9d1d9;
      border:none;outline:none;resize:none;
      font-family:'Courier New',monospace;
      font-size:14px;line-height:1.65;
      padding:12px 12px;
      tab-size:4;
      white-space:pre;
      overflow-wrap:normal;
      overflow-x:auto;
      -webkit-overflow-scrolling:touch;
    }}
    /* Prevent iOS zoom on focus — min font-size 16px */
    @media(max-width:768px){{
      textarea{{font-size:16px;line-height:1.55;padding:10px 10px}}
      .line-nums{{font-size:14px;line-height:1.55}}
    }}
    textarea::selection{{background:#264f78}}

    /* ── Bottom bar ── */
    .bottombar{{
      background:#161b22;border-top:1px solid #30363d;
      padding:4px 12px;display:flex;align-items:center;gap:10px;
      font-size:11px;color:#6e7681;flex-shrink:0;
      font-family:Arial,sans-serif;
      overflow:hidden;
    }}
    .bottombar span{{white-space:nowrap}}
    .bsep{{color:#30363d}}
    @media(max-width:360px){{
      .b-path,.b-enc{{display:none}}
    }}
  </style>
</head>
<body>
  <form id="editForm" method="post" style="display:contents">
    <div class="toolbar">
      <div class="file-info">
        <div class="file-name">📄 {filename}</div>
        <div class="file-path">{filepath}</div>
      </div>
      <span class="status saved" id="statusBadge">✓ Saved</span>
      <button type="submit" class="btn-save" id="saveBtn">
        💾 Save <span class="kbd">Ctrl+S</span>
      </button>
    </div>

    <div class="editor-wrap">
      <div class="line-nums" id="lineNums">{chr(10).join(str(i) for i in range(1, line_count + 1))}</div>
      <textarea name="code" id="codeArea"
        spellcheck="false" autocorrect="off" autocapitalize="off" autocomplete="off"
      >{safe_content}</textarea>
    </div>

    <div class="bottombar">
      <span class="b-path">🤖 Rajfflive Bot</span>
      <span class="bsep">|</span>
      <span id="cursorPos">Ln 1, Col 1</span>
      <span class="bsep">|</span>
      <span id="lineCount">{line_count} lines</span>
      <span class="bsep b-enc">|</span>
      <span class="b-enc">UTF-8</span>
    </div>
  </form>

  <script>
    const ta = document.getElementById('codeArea');
    const lnDiv = document.getElementById('lineNums');
    const status = document.getElementById('statusBadge');
    const lineCountEl = document.getElementById('lineCount');
    const cursorPos = document.getElementById('cursorPos');
    const saveBtn = document.getElementById('saveBtn');
    const form = document.getElementById('editForm');
    let saved = true;

    function updateLines() {{
      const lines = ta.value.split('\\n').length;
      lineCountEl.textContent = lines + ' lines';
      lnDiv.textContent = Array.from({{length: lines}}, (_, i) => i + 1).join('\\n');
      lnDiv.scrollTop = ta.scrollTop;
    }}

    function markUnsaved() {{
      if (saved) {{
        saved = false;
        status.textContent = '● Unsaved';
        status.className = 'status unsaved';
      }}
    }}

    ta.addEventListener('input', () => {{ updateLines(); markUnsaved(); }});
    ta.addEventListener('scroll', () => {{ lnDiv.scrollTop = ta.scrollTop; }});

    ta.addEventListener('keydown', e => {{
      if (e.key === 'Tab') {{
        e.preventDefault();
        const s = ta.selectionStart, end = ta.selectionEnd;
        ta.value = ta.value.substring(0, s) + '    ' + ta.value.substring(end);
        ta.selectionStart = ta.selectionEnd = s + 4;
        updateLines(); markUnsaved();
      }}
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {{
        e.preventDefault();
        form.requestSubmit ? form.requestSubmit(saveBtn) : form.submit();
      }}
    }});

    ta.addEventListener('keyup', updateCursor);
    ta.addEventListener('click', updateCursor);
    function updateCursor() {{
      const val = ta.value.substring(0, ta.selectionStart);
      const ln = val.split('\\n').length;
      const col = val.split('\\n').pop().length + 1;
      cursorPos.textContent = 'Ln ' + ln + ', Col ' + col;
    }}

    form.addEventListener('submit', () => {{
      saveBtn.disabled = true;
      saveBtn.innerHTML = '⏳ Saving…';
    }});

    window.addEventListener('beforeunload', e => {{
      if (!saved) {{ e.preventDefault(); e.returnValue = ''; }}
    }});

    // Keep line nums in sync on scroll
    ta.addEventListener('scroll', () => {{ lnDiv.scrollTop = ta.scrollTop; }}, {{passive:true}});

    updateLines();
  </script>
</body>
</html>"""

@app.route('/')
def home():
    stats = get_system_stats()
    mongo_stats = mongo_get_stats() if MONGO_ENABLED else {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Rajfflive Bot Pro</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{
      background:linear-gradient(135deg,#0a0c0f 0%,#0d1117 60%,#0a1628 100%);
      color:#c9d1d9;font-family:Arial,sans-serif;
      min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px;
    }}
    .card{{
      background:rgba(22,27,34,.97);border:1px solid #30363d;
      border-radius:20px;padding:36px 40px;max-width:480px;width:100%;
      box-shadow:0 8px 40px rgba(0,0,0,.6);
    }}
    .logo{{font-size:40px;margin-bottom:8px}}
    h1{{color:#00d4ff;font-size:22px;margin-bottom:4px}}
    .badge{{
      display:inline-flex;align-items:center;gap:6px;
      background:#0d2818;color:#3fb950;border:1px solid #238636;
      padding:4px 14px;border-radius:20px;font-size:12px;margin-bottom:20px;
    }}
    .section{{margin-bottom:18px}}
    .section-title{{
      font-size:11px;font-weight:700;color:#6e7681;letter-spacing:.08em;
      text-transform:uppercase;margin-bottom:10px;
    }}
    .stat-row{{
      display:flex;justify-content:space-between;align-items:center;
      padding:7px 0;border-bottom:1px solid #21262d;font-size:13px;
    }}
    .stat-row:last-child{{border-bottom:none}}
    .stat-label{{color:#8b949e}}
    .stat-val{{color:#e6edf3;font-weight:600}}
    .green{{color:#3fb950}}
    .red{{color:#f85149}}
    .owner-row{{
      display:flex;justify-content:space-between;
      font-size:13px;color:#8b949e;padding-top:16px;margin-top:4px;
      border-top:1px solid #21262d;
    }}
    .owner-row span:last-child{{color:#58a6ff}}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">🤖</div>
    <h1>Rajfflive Bot Pro</h1>
    <div class="badge"><span style="width:8px;height:8px;background:#3fb950;border-radius:50%;display:inline-block"></span> ONLINE</div>

    <div class="section">
      <div class="section-title">System</div>
      <div class="stat-row"><span class="stat-label">CPU</span><span class="stat-val">{stats['cpu']:.1f}%</span></div>
      <div class="stat-row"><span class="stat-label">Memory</span><span class="stat-val">{stats['memory']:.1f}%</span></div>
      <div class="stat-row"><span class="stat-label">Disk</span><span class="stat-val">{stats['disk']:.1f}%</span></div>
      <div class="stat-row"><span class="stat-label">Uptime</span><span class="stat-val">{stats['uptime']}</span></div>
      <div class="stat-row"><span class="stat-label">Processes</span><span class="stat-val">{stats['processes']}</span></div>
    </div>

    <div class="section">
      <div class="section-title">Database</div>
      <div class="stat-row"><span class="stat-label">MongoDB</span><span class="stat-val {'green' if MONGO_ENABLED else 'red'}">{'✅ Connected' if MONGO_ENABLED else '❌ Offline'}</span></div>
      <div class="stat-row"><span class="stat-label">Total Users</span><span class="stat-val">{mongo_stats.get('total_users', 0)}</span></div>
      <div class="stat-row"><span class="stat-label">Total Commands</span><span class="stat-val">{mongo_stats.get('total_commands', 0)}</span></div>
      <div class="stat-row"><span class="stat-label">Total Admins</span><span class="stat-val">{mongo_stats.get('total_admins', 0)}</span></div>
    </div>

    <div class="owner-row">
      <span>👑 {OWNER_NAME}</span>
      <span>{BOT_USERNAME}</span>
    </div>
  </div>
</body>
</html>"""

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "owner": "rajfflive",
        "bot": "rtmxbot",
        "mongodb": MONGO_ENABLED,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/stats')
def api_stats():
    stats = get_system_stats()
    mongo_stats = mongo_get_stats() if MONGO_ENABLED else {}
    stats.update(mongo_stats)
    stats.update({
        "owner": "rajfflive",
        "bot": "rtmxbot",
        "mongodb": MONGO_ENABLED
    })
    return jsonify(stats)

# ========== FIX 409 ERROR ==========
print("\n🔧 Fixing 409 Conflict Error...")
try:
    temp_bot = telebot.TeleBot(BOT_TOKEN)
    temp_bot.remove_webhook()
    print("✅ Webhook removed")
    time.sleep(2)
    
    updates = temp_bot.get_updates(offset=-1, timeout=1, limit=1)
    print(f"✅ Cleared {len(updates)} pending updates")
    time.sleep(1)
except Exception as e:
    print(f"⚠️ {e}")

# ========== MAIN ==========
if __name__ == "__main__":
    print("="*50)
    print(f"🤖 {BOT_NAME}")
    print(f"👑 Owner: {OWNER_NAME}")
    print(f"🤖 Bot: {BOT_USERNAME}")
    print(f"🌐 Port: {PORT}")
    print(f"📊 MongoDB: {'✅ Connected' if MONGO_ENABLED else '❌ Disabled'}")
    print("="*50)
    
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN not set!")
        exit(1)
    
    load_data()
    
    # Flask thread
    def run_flask():
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Bot thread
    def run_bot():
        print("🤖 Starting bot polling...")
        while True:
            try:
                bot.infinity_polling(timeout=60, long_polling_timeout=60)
            except Exception as e:
                logger.error(f"Bot error: {e}")
                time.sleep(5)
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    add_system_alert("INFO", f"{BOT_NAME} started successfully")
    
    try:
        while True:
            time.sleep(60)
            current_time = time.time()
            for sid in list(edit_sessions.keys()):
                if current_time - edit_sessions[sid].get('timestamp', 0) > 3600:
                    edit_sessions.pop(sid, None)
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        save_data()
        if mongo_client:
            mongo_client.close()

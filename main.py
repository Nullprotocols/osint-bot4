#!/usr/bin/env python3
# main.py - OSINT Pro Bot (Final: No username resolution, proper log channel)

import os
import sys
import re
import json
import uuid
import time
import asyncio
import logging
import threading
import html
import aiosqlite
import aiohttp
from datetime import datetime
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    filters, ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode

# Import config and database
from config import *
from database import *

# ==================== SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

# ==================== CONVERSATION STATES ====================
WAITING_MESSAGE = 1

# ==================== UTILITY FUNCTIONS ====================
CACHE_EXPIRY = 300
copy_cache = {}

def clean_branding(text, extra_blacklist=None):
    if not text:
        return text
    blacklist = GLOBAL_BLACKLIST.copy()
    if extra_blacklist:
        blacklist.extend(extra_blacklist)
    for item in blacklist:
        text = re.sub(re.escape(item), '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()

async def call_api(url):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200:
                    try:
                        return await resp.json()
                    except:
                        return {"error": "Invalid JSON response"}
                else:
                    return {"error": f"HTTP {resp.status}"}
        except asyncio.TimeoutError:
            return {"error": "Request timeout"}
        except Exception as e:
            return {"error": str(e)}

async def check_force_join(bot, user_id):
    missing = []
    for ch in FORCE_JOIN_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in ['left', 'kicked']:
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return len(missing) == 0, missing

def get_force_join_keyboard(missing):
    keyboard = []
    for ch in missing:
        keyboard.append([InlineKeyboardButton(f"Join {ch['name']}", url=ch['link'])])
    keyboard.append([InlineKeyboardButton("✅ I've joined", callback_data="verify_join")])
    return InlineKeyboardMarkup(keyboard)

def store_copy_data(data):
    uid = str(uuid.uuid4())
    copy_cache[uid] = {"data": data, "time": time.time()}
    return uid

def get_copy_button(data):
    return InlineKeyboardButton("📋 Copy", callback_data=f"copy:{store_copy_data(data)}")

def get_search_button(cmd):
    return InlineKeyboardButton("🔍 Search", callback_data=f"search:{cmd}")

def format_log_date(dt=None):
    """Return date in DD-MM-YYYY HH:MM format"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%d-%m-%Y %H:%M")

# ==================== COMMAND LIST GENERATORS ====================
def get_commands_list():
    lines = ["📋 **AVAILABLE COMMANDS**", "────────────────────────────"]
    for cmd, info in COMMANDS.items():
        lines.append(f"• `/{cmd} [{info['param']}]` → {info['desc']}")
    lines.append(CMD_LIST_FOOTER)
    return "\n".join(lines)

def get_admin_commands_list():
    admin_cmds = [
        "`/broadcast` - Send a message to all users (two-step)",
        "`/dm <user_id>` - DM to one user (two-step)",
        "`/bulkdm <id1> <id2> ...` - Bulk DM (two-step)",
        "`/ban <user_id> [reason]` - Ban a user",
        "`/unban <user_id>` - Unban a user",
        "`/deleteuser <user_id>` - Delete user from DB",
        "`/searchuser <query>` - Search users",
        "`/users [page]` - List users",
        "`/recentusers [days]` - Recently active users",
        "`/inactiveusers [days]` - Inactive users",
        "`/userlookups <user_id>` - User's last lookups",
        "`/leaderboard` - Top users",
        "`/stats` - Bot statistics",
        "`/dailystats [days]` - Daily stats",
        "`/lookupstats` - Command usage stats",
        "`/addadmin <user_id>` (owner only)",
        "`/removeadmin <user_id>` (owner only)",
        "`/listadmins` - List all admins",
        "`/settings` - Bot settings (WIP)",
        "`/fulldbbackup` - Download database backup",
        "`/group` - List groups where bot is admin"
    ]
    lines = ["👑 **ADMIN COMMANDS**", "────────────────────────────"]
    lines.extend(admin_cmds)
    lines.append(CMD_LIST_FOOTER)
    return "\n".join(lines)

# ==================== FILTERS ====================
async def group_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type == "private":
        if update.message and update.message.text:
            text = update.message.text.strip()
            if text.startswith('/start') or text.startswith('/help') or text.startswith('/admin'):
                return True
        user_id = update.effective_user.id
        if user_id == OWNER_ID or await is_admin(user_id):
            return True
        await update.message.reply_text(
            f"⚠️ **Ye bot sirf group me kaam karta hai.**\nPersonal use ke liye use kare: {REDIRECT_BOT}",
            parse_mode=ParseMode.MARKDOWN
        )
        return False
    return True

async def force_join_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True
    if user.id == OWNER_ID or await is_admin(user.id):
        return True
    if await is_banned(user.id):
        await update.message.reply_text("❌ **Aap banned hain. Contact admin.**", parse_mode=ParseMode.MARKDOWN)
        return False
    ok, missing = await check_force_join(context.bot, user.id)
    if not ok:
        await update.message.reply_text(
            "⚠️ **Bot use karne ke liye ye channels join karo:**",
            reply_markup=get_force_join_keyboard(missing),
            parse_mode=ParseMode.MARKDOWN
        )
        return False
    return True

# ==================== START & HELP HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        await update_user(user.id, user.username, user.first_name, user.last_name)
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
    if not await force_join_filter(update, context):
        return
    welcome = f"👋 **Welcome {user.first_name}!**\n\n" + get_commands_list()
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        await update_user(user.id, user.username, user.first_name, user.last_name)
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
    if not await force_join_filter(update, context):
        return
    await update.message.reply_text(get_commands_list(), parse_mode=ParseMode.MARKDOWN)

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and not await is_admin(user.id):
        await update.message.reply_text("❌ **This command is for admins only.**", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(get_admin_commands_list(), parse_mode=ParseMode.MARKDOWN)

# ==================== COMMAND HANDLER (No username resolution) ====================
async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE, cmd: str, query: str):
    cmd_info = COMMANDS.get(cmd)
    if not cmd_info:
        await update.message.reply_text("❌ Command not found.")
        return

    # ========== NO USERNAME RESOLUTION ==========
    url = cmd_info["url"].format(query)
    data = await call_api(url)

    # ========== REMOVE UNWANTED FIELDS FOR tg2num ==========
    if cmd == 'tg2num' and isinstance(data, dict):
        keys_to_remove = ["credit", "channel", "validity"]
        for key in keys_to_remove:
            if key in data:
                del data[key]

    # Add branding
    if isinstance(data, dict):
        data["developer"] = BRANDING["developer"]
        data["powered_by"] = BRANDING["powered_by"]
    elif isinstance(data, list):
        data = {
            "result": data,
            "developer": BRANDING["developer"],
            "powered_by": BRANDING["powered_by"]
        }
    else:
        data = {
            "result": data,
            "developer": BRANDING["developer"],
            "powered_by": BRANDING["powered_by"]
        }

    # Clean branding from original API response
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    cleaned = clean_branding(json_str, cmd_info.get("extra_blacklist", []))
    cleaned_escaped = html.escape(cleaned)

    extra_footer = "\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 **Developer:** @Nullprotocol_X\n⚡ **Powered by:** NULL PROTOCOL"

    # Prepare final HTML message for user
    output_html = f"<pre>{cleaned_escaped}</pre>{extra_footer}"

    # Variables for logging
    log_chat_id = cmd_info["log"]
    user_id = update.effective_user.id
    username = update.effective_user.username or 'N/A'
    log_date = format_log_date()
    json_size = len(cleaned)

    # ========== SEND RESPONSE TO USER ==========
    file_sent = False
    filename = None

    if len(output_html) > 4096 or json_size > 3000:
        filename = f"{cmd}_{query[:50].replace(' ', '_')}.json"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(cleaned)
            with open(filename, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=filename,
                    caption=f"📎 Output too long, sent as file.\n\nDeveloper: @Nullprotocol_X\nPowered by: NULL PROTOCOL"
                )
            file_sent = True
        except Exception as e:
            await update.message.reply_text(f"❌ File send failed: {e}")
        finally:
            # Don't remove yet, we need it for log channel
            pass
    else:
        keyboard = [[get_copy_button(data), get_search_button(cmd)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(output_html, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    # Save lookup to DB
    try:
        await save_lookup(user_id, cmd, query, data)
    except Exception as e:
        logger.error(f"Failed to save lookup: {e}")

    # ========== LOG TO CHANNEL ==========
    try:
        if file_sent and filename and os.path.exists(filename):
            # Send file to log channel with caption
            caption = (
                f"Lookup Log - {cmd.upper()}\n\n"
                f"User: {user_id} (@{username})\n"
                f"Type: {cmd}\n"
                f"Input: {query}\n"
                f"Date: {log_date}\n"
                f"Size: {json_size} characters\n"
                f"Format: JSON File"
            )
            with open(filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=log_chat_id,
                    document=f,
                    filename=filename,
                    caption=caption
                )
            logger.info(f"✅ Log file sent to channel {log_chat_id}")
        else:
            # Send text with JSON syntax highlighting
            log_message = (
                f"Lookup Log - {cmd.upper()}\n\n"
                f"User: {user_id} (@{username})\n"
                f"Type: {cmd}\n"
                f"Input: {query}\n"
                f"Date: {log_date}\n"
                f"Size: {json_size} characters\n\n"
                f"Result:\n\n"
                f"```json\n{cleaned}\n```"
            )
            if len(log_message) > 4000:
                log_message = log_message[:4000] + "\n\n... (truncated)"
            await context.bot.send_message(
                chat_id=log_chat_id,
                text=log_message,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"✅ Log text sent to channel {log_chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send log to channel {log_chat_id}: {e}")
    finally:
        if filename and os.path.exists(filename):
            os.remove(filename)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await group_only(update, context):
        return
    if not await force_join_filter(update, context):
        return
    u = update.effective_user
    try:
        await update_user(u.id, u.username, u.first_name, u.last_name)
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
    text = update.message.text
    if not text or not text.startswith('/'):
        return
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split('@')[0].lower()
    query = parts[1] if len(parts) > 1 else None
    if not query:
        param = COMMANDS.get(cmd, {}).get("param", "query")
        await update.message.reply_text(f"Usage: `/{cmd} <{param}>`", parse_mode=ParseMode.MARKDOWN)
        return
    await handle_command(update, context, cmd, query)

# ==================== CALLBACK HANDLER ====================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "verify_join":
        ok, missing = await check_force_join(context.bot, query.from_user.id)
        if ok:
            await query.edit_message_text("✅ **Verification successful!**", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(
                "⚠️ **Aapne abhi bhi kuch channels join nahi kiye:**",
                reply_markup=get_force_join_keyboard(missing),
                parse_mode=ParseMode.MARKDOWN
            )
    elif data.startswith("copy:"):
        uid = data.split(":", 1)[1]
        entry = copy_cache.get(uid)
        if entry and (time.time() - entry["time"]) < CACHE_EXPIRY:
            await query.message.reply_text(
                f"```json\n{json.dumps(entry['data'], indent=2)}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
            del copy_cache[uid]
        else:
            copy_cache.pop(uid, None)
            await query.message.reply_text("❌ **Copy data expired.**", parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("search:"):
        cmd = data.split(":", 1)[1]
        await query.message.reply_text(f"Send `/{cmd}` with your query.", parse_mode=ParseMode.MARKDOWN)

# ==================== CONVERSATION HANDLERS ====================
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and not await is_admin(user.id):
        await update.message.reply_text("❌ Admin only.")
        return ConversationHandler.END
    context.user_data['broadcast_targets'] = 'all'
    await update.message.reply_text("Send the message to broadcast.\nSend /cancel to abort.")
    return WAITING_MESSAGE

async def dm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and not await is_admin(user.id):
        await update.message.reply_text("❌ Admin only.")
        return ConversationHandler.END
    try:
        target = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /dm <user_id>")
        return ConversationHandler.END
    context.user_data['dm_targets'] = [target]
    await update.message.reply_text(f"Send message for {target}.\nSend /cancel to abort.")
    return WAITING_MESSAGE

async def bulkdm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and not await is_admin(user.id):
        await update.message.reply_text("❌ Admin only.")
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text("Usage: /bulkdm <id1> <id2> ...")
        return ConversationHandler.END
    targets = []
    for arg in context.args:
        try:
            targets.append(int(arg))
        except ValueError:
            await update.message.reply_text(f"Invalid ID: {arg}")
            return ConversationHandler.END
    context.user_data['bulkdm_targets'] = targets
    await update.message.reply_text(f"Send message for {len(targets)} users.\nSend /cancel to abort.")
    return WAITING_MESSAGE

async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if 'broadcast_targets' in context.user_data:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT user_id FROM users') as cursor:
                users = await cursor.fetchall()
        success, fail = 0, 0
        for (uid,) in users:
            try:
                await message.copy(chat_id=uid)
                success += 1
            except:
                fail += 1
        await message.reply_text(f"✅ Broadcast done.\nSuccess: {success}\nFailed: {fail}")
    elif 'dm_targets' in context.user_data:
        for uid in context.user_data['dm_targets']:
            try:
                await message.copy(chat_id=uid)
                await message.reply_text(f"✅ Sent to {uid}")
            except Exception as e:
                await message.reply_text(f"❌ Failed: {e}")
    elif 'bulkdm_targets' in context.user_data:
        success, fail = 0, 0
        for uid in context.user_data['bulkdm_targets']:
            try:
                await message.copy(chat_id=uid)
                success += 1
            except:
                fail += 1
        await message.reply_text(f"✅ BulkDM done.\nSuccess: {success}\nFailed: {fail}")
    else:
        await message.reply_text("Error: No operation.")
        return ConversationHandler.END
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# ==================== ADMIN COMMANDS ====================
def admin_only(func):
    async def wrapper(update, context):
        user = update.effective_user
        if user.id == OWNER_ID or await is_admin(user.id):
            return await func(update, context)
        await update.message.reply_text("❌ Admin only.")
    return wrapper

def owner_only(func):
    async def wrapper(update, context):
        if update.effective_user.id == OWNER_ID:
            return await func(update, context)
        await update.message.reply_text("❌ Owner only.")
    return wrapper

@admin_only
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "No reason"
        await ban_user(uid, reason, update.effective_user.id)
        await update.message.reply_text(f"✅ Banned {uid}")
    except:
        await update.message.reply_text("Usage: /ban <user_id> [reason]")

@admin_only
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        await unban_user(uid)
        await update.message.reply_text(f"✅ Unbanned {uid}")
    except:
        await update.message.reply_text("Usage: /unban <user_id>")

@admin_only
async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM users WHERE user_id = ?', (uid,))
            await db.commit()
        await update.message.reply_text(f"✅ User {uid} deleted.")
    except:
        await update.message.reply_text("Usage: /deleteuser <user_id>")

@admin_only
async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /searchuser <query>")
    query = ' '.join(context.args)
    # ... (search logic as before) ...
    await update.message.reply_text("Search result placeholder")

@admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = int(context.args[0]) if context.args else 1
    users_list = await get_all_users(limit=10, offset=(page-1)*10)
    text = f"👥 Users (Page {page}):\n" + "\n".join([f"• {u[0]} (@{u[1]}) - {u[4]} lookups" for u in users_list])
    await update.message.reply_text(text)

@admin_only
async def recent_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 7
    users_list = await get_recent_users(days)
    text = f"📅 Active last {days} days:\n" + "\n".join([f"• {u[0]} (@{u[1]}) - last seen {u[2]}" for u in users_list])
    await update.message.reply_text(text)

@admin_only
async def inactive_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 30
    users_list = await get_inactive_users(days)
    text = f"💤 Inactive >{days} days:\n" + "\n".join([f"• {u[0]} (@{u[1]}) - last seen {u[2]}" for u in users_list])
    await update.message.reply_text(text)

@admin_only
async def user_lookups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        lookups = await get_user_lookups(uid, 10)
        text = f"📊 Last 10 lookups of {uid}:\n" + "\n".join([f"{ts} - /{cmd} {q}" for cmd, q, ts in lookups])
        await update.message.reply_text(text)
    except:
        await update.message.reply_text("Usage: /userlookups <user_id>")

@admin_only
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    board = await get_leaderboard(10)
    text = "🏆 Leaderboard:\n" + "\n".join([f"{i}. {uid} - {count} lookups" for i, (uid, count) in enumerate(board, 1)])
    await update.message.reply_text(text)

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats_data = await get_stats()
    text = f"📈 Stats:\nTotal Users: {stats_data['total_users']}\nTotal Lookups: {stats_data['total_lookups']}\nAdmins: {stats_data['total_admins']}\nBanned: {stats_data['total_banned']}"
    await update.message.reply_text(text)

@admin_only
async def daily_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 7
    stats_list = await get_daily_stats(days)
    text = f"📅 Daily stats (last {days} days):\n" + "\n".join([f"{date} - /{cmd}: {count}" for date, cmd, count in stats_list])
    await update.message.reply_text(text)

@admin_only
async def lookup_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats_list = await get_lookup_stats(10)
    text = "🔍 Top commands:\n" + "\n".join([f"/{cmd}: {cnt}" for cmd, cnt in stats_list])
    await update.message.reply_text(text)

@admin_only
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = await get_all_groups()
    if not groups:
        await update.message.reply_text("No groups yet.")
        return
    text = "📌 **Groups:**\n\n" + "\n\n".join([f"• **{name}**\n  ID: `{gid}`\n  Link: {link}" for gid, name, link in groups])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== OWNER COMMANDS ====================
@owner_only
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        await add_admin(uid, OWNER_ID)
        await update.message.reply_text(f"✅ Admin added: {uid}")
    except:
        await update.message.reply_text("Usage: /addadmin <user_id>")

@owner_only
async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(context.args[0])
        await remove_admin(uid)
        await update.message.reply_text(f"✅ Admin removed: {uid}")
    except:
        await update.message.reply_text("Usage: /removeadmin <user_id>")

@owner_only
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = await get_all_admins()
    text = "👑 Admins:\n" + "\n".join(str(a) for a in admins)
    await update.message.reply_text(text)

@owner_only
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Settings - under development.")

@owner_only
async def full_db_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(DB_PATH, 'rb') as f:
            await update.message.reply_document(f, filename='backup.db')
    except Exception as e:
        await update.message.reply_text(f"❌ Backup failed: {e}")

# ==================== GROUP TRACKING ====================
async def track_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']:
        return
    if update.chat_member.new_chat_member.user.id != context.bot.id:
        return
    status = update.chat_member.new_chat_member.status
    chat = update.effective_chat
    if status == 'administrator':
        try:
            invite_link = await context.bot.export_chat_invite_link(chat.id)
        except:
            invite_link = None
        await add_bot_group(chat.id, chat.title or "Unnamed", invite_link)
    elif status == 'left':
        await remove_bot_group(chat.id)

# ==================== BOT INIT ====================
async def post_init(app: Application):
    await init_db()
    for aid in INITIAL_ADMINS:
        await add_admin(aid, OWNER_ID)
    logger.info("✅ Bot initialized.")

def run_bot():
    try:
        if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            logger.error("❌ BOT_TOKEN not set!")
            return
        bot_app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("help", help_command))
        bot_app.add_handler(CommandHandler("admin", admin_help))
        bot_app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('broadcast', broadcast_start)],
            states={WAITING_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_message)]},
            fallbacks=[CommandHandler('cancel', cancel)]
        ))
        bot_app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('dm', dm_start)],
            states={WAITING_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_message)]},
            fallbacks=[CommandHandler('cancel', cancel)]
        ))
        bot_app.add_handler(ConversationHandler(
            entry_points=[CommandHandler('bulkdm', bulkdm_start)],
            states={WAITING_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_message)]},
            fallbacks=[CommandHandler('cancel', cancel)]
        ))
        bot_app.add_handler(CommandHandler("group", list_groups))
        bot_app.add_handler(CommandHandler("ban", ban))
        bot_app.add_handler(CommandHandler("unban", unban))
        bot_app.add_handler(CommandHandler("deleteuser", delete_user))
        bot_app.add_handler(CommandHandler("searchuser", search_user))
        bot_app.add_handler(CommandHandler("users", users))
        bot_app.add_handler(CommandHandler("recentusers", recent_users))
        bot_app.add_handler(CommandHandler("inactiveusers", inactive_users))
        bot_app.add_handler(CommandHandler("userlookups", user_lookups))
        bot_app.add_handler(CommandHandler("leaderboard", leaderboard))
        bot_app.add_handler(CommandHandler("stats", stats))
        bot_app.add_handler(CommandHandler("dailystats", daily_stats))
        bot_app.add_handler(CommandHandler("lookupstats", lookup_stats))
        bot_app.add_handler(CommandHandler("addadmin", add_admin_cmd))
        bot_app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
        bot_app.add_handler(CommandHandler("listadmins", list_admins))
        bot_app.add_handler(CommandHandler("settings", settings))
        bot_app.add_handler(CommandHandler("fulldbbackup", full_db_backup))
        bot_app.add_handler(MessageHandler(filters.COMMAND, message_handler))
        bot_app.add_handler(CallbackQueryHandler(callback_handler))
        bot_app.add_handler(ChatMemberHandler(track_groups, ChatMemberHandler.CHAT_MEMBER))
        logger.info("🚀 Bot polling started...")
        bot_app.run_polling(stop_signals=None)
    except Exception as e:
        logger.exception(f"Bot thread crashed: {e}")

# ==================== FLASK ====================
@flask_app.route('/')
def home():
    return jsonify({"status": "running", "time": datetime.now().isoformat()})

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

# ==================== MAIN ====================
def main():
    logger.info("🔧 Starting OSINT Pro Bot on Render...")
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN not set!")
    logger.warning("⚠️ SQLite data will be lost on every restart!")
    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        threading.Thread(target=run_bot, daemon=True).start()
        logger.info("✅ Bot thread started")
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()

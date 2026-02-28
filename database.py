# database.py - Complete Async SQLite Database for OSINT Bot
# ⚠️ WARNING: SQLite on Render free tier will LOSE DATA on every restart!
# For production, use PostgreSQL or attach a persistent disk.

import aiosqlite
import json
from datetime import datetime, timedelta
from config import DB_PATH

# ==================== INIT DATABASE ====================
async def init_db():
    """Initialize all database tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Users table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                lookups INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Admins table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Banned users table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS banned (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_by INTEGER,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Lookups log table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS lookups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                query TEXT,
                result TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Groups where bot is admin
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_groups (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                invite_link TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

# ==================== USER FUNCTIONS ====================
async def update_user(user_id, username, first_name, last_name):
    """Update or insert user data."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, last_seen)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen = CURRENT_TIMESTAMP
        ''', (user_id, username, first_name, last_name))
        await db.commit()

async def is_banned(user_id):
    """Check if a user is banned."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM banned WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def ban_user(user_id, reason, banned_by):
    """Ban a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO banned (user_id, reason, banned_by) VALUES (?, ?, ?)',
                         (user_id, reason, banned_by))
        await db.commit()

async def unban_user(user_id):
    """Unban a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM banned WHERE user_id = ?', (user_id,))
        await db.commit()

# ==================== ADMIN FUNCTIONS ====================
async def is_admin(user_id):
    """Check if a user is an admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def add_admin(user_id, added_by):
    """Add a new admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
        await db.commit()

async def remove_admin(user_id):
    """Remove an admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_all_admins():
    """Return list of all admin user IDs."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM admins') as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

# ==================== LOOKUP FUNCTIONS ====================
async def save_lookup(user_id, command, query, result):
    """Save a lookup to the database and increment user's lookup count."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO lookups (user_id, command, query, result)
            VALUES (?, ?, ?, ?)
        ''', (user_id, command, query, json.dumps(result, ensure_ascii=False)))
        await db.execute('UPDATE users SET lookups = lookups + 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_user_lookups(user_id, limit=10):
    """Get recent lookups for a specific user."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT command, query, timestamp FROM lookups
            WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
        ''', (user_id, limit)) as cursor:
            return await cursor.fetchall()

# ==================== STATS & USER LISTS ====================
async def get_all_users(limit=10, offset=0):
    """Get paginated list of all users, ordered by last seen."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT user_id, username, first_name, last_name, lookups, last_seen
            FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            return await cursor.fetchall()

async def get_recent_users(days=7):
    """Get users active within the last N days."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT user_id, username, last_seen FROM users
            WHERE last_seen >= ? ORDER BY last_seen DESC
        ''', (since,)) as cursor:
            return await cursor.fetchall()

async def get_inactive_users(days=30):
    """Get users inactive for more than N days."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT user_id, username, last_seen FROM users
            WHERE last_seen < ? ORDER BY last_seen DESC
        ''', (since,)) as cursor:
            return await cursor.fetchall()

async def get_leaderboard(limit=10):
    """Get top users by lookup count."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT user_id, lookups FROM users
            ORDER BY lookups DESC LIMIT ?
        ''', (limit,)) as cursor:
            return await cursor.fetchall()

async def get_stats():
    """Get overall bot statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Total users
        async with db.execute('SELECT COUNT(*) FROM users') as cur:
            total_users = (await cur.fetchone())[0]
        # Total lookups
        async with db.execute('SELECT COUNT(*) FROM lookups') as cur:
            total_lookups = (await cur.fetchone())[0]
        # Total admins
        async with db.execute('SELECT COUNT(*) FROM admins') as cur:
            total_admins = (await cur.fetchone())[0]
        # Total banned
        async with db.execute('SELECT COUNT(*) FROM banned') as cur:
            total_banned = (await cur.fetchone())[0]
        return {
            'total_users': total_users,
            'total_lookups': total_lookups,
            'total_admins': total_admins,
            'total_banned': total_banned
        }

async def get_daily_stats(days=7):
    """Get daily command usage statistics for the last N days."""
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT date(timestamp) as day, command, COUNT(*)
            FROM lookups WHERE date(timestamp) >= ?
            GROUP BY day, command ORDER BY day DESC
        ''', (since,)) as cursor:
            return await cursor.fetchall()

async def get_lookup_stats(limit=10):
    """Get most used commands."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT command, COUNT(*) as cnt FROM lookups
            GROUP BY command ORDER BY cnt DESC LIMIT ?
        ''', (limit,)) as cursor:
            return await cursor.fetchall()

# ==================== GROUP TRACKING ====================
async def add_bot_group(group_id, group_name, invite_link=None):
    """Add or update a group where bot is admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO bot_groups (group_id, group_name, invite_link)
            VALUES (?, ?, ?)
        ''', (group_id, group_name, invite_link))
        await db.commit()

async def remove_bot_group(group_id):
    """Remove a group from the list (when bot leaves or loses admin)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM bot_groups WHERE group_id = ?', (group_id,))
        await db.commit()

async def get_all_groups():
    """Get all groups where bot is admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT group_id, group_name, invite_link FROM bot_groups') as cursor:
            return await cursor.fetchall()

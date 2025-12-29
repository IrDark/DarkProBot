import datetime
import json
import logging
import os
import sqlite3
from sqlite3 import Error
from version import is_version_less
#from urllib.parse import urlparse

#from Utils import api
#from config import PANEL_URL, API_PATH, USERS_DB_LOC




class UserDBManager:
    def __init__(self, db_file):
        self.conn = self.create_connection(db_file)
        self.create_user_table()
        #self.set_default_configs()

    #close connection
    def __del__(self):
        self.conn.close()
    
    def close(self):
        self.conn.close()
    

    def create_connection(self, db_file):
        """ Create a database connection to a SQLite database """
        try:
            conn = sqlite3.connect(db_file, check_same_thread=False)
            return conn
        except Error as e:
            logging.error(f"Error while connecting to database \n Error:{e}")
            return None

    def create_user_table(self):
        cur = self.conn.cursor()
        try:
            cur.execute("CREATE TABLE IF NOT EXISTS users ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "telegram_id INTEGER NOT NULL UNIQUE,"
                        "full_name TEXT NULL,"
                        "username TEXT NULL,"
                        # Referral system (safe-migrated below)
                        # "referral_code TEXT NULL,"
                        # "referred_by INTEGER NULL,"
                        # "referral_bonus_paid BOOLEAN NOT NULL DEFAULT 0,"
                        # "referral_pending_bonus BOOLEAN NOT NULL DEFAULT 0,"
                        # "discount_percent INTEGER NOT NULL DEFAULT 0,"
                        # "count_warn INTEGER NOT NULL DEFAULT 0,"
                        "test_subscription BOOLEAN NOT NULL DEFAULT 0,"
                        "banned BOOLEAN NOT NULL DEFAULT 0,"
                        "created_at TEXT NOT NULL)")
            self.conn.commit()
            logging.info("User table created successfully!")

            # -------------------- Referral system (safe migration) --------------------
            # We add new columns to the existing `users` table if they don't exist.
            cur.execute("PRAGMA table_info(users)")
            user_cols = [row[1] for row in cur.fetchall()]
            if 'referral_code' not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
                self.conn.commit()
            if 'referred_by' not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
                self.conn.commit()
            if 'referral_bonus_paid' not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN referral_bonus_paid BOOLEAN NOT NULL DEFAULT 0")
                self.conn.commit()
            if 'referral_pending_bonus' not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN referral_pending_bonus BOOLEAN NOT NULL DEFAULT 0")
                self.conn.commit()

            # Earnings ledger (signup bonus + commissions)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS referral_earnings ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "telegram_id INTEGER NOT NULL,"
                "type TEXT NOT NULL,"
                "amount INTEGER NOT NULL,"
                "related_telegram_id INTEGER NULL,"
                "order_id INTEGER NULL,"
                "created_at TEXT NOT NULL,"
                "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))"
            )
            self.conn.commit()

            # Prevent duplicate commission for the same order/referrer
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_earnings_unique_commission "
                "ON referral_earnings(telegram_id, type, order_id)"
            )
            self.conn.commit()

            cur.execute("CREATE TABLE IF NOT EXISTS plans ("
                        "id INTEGER PRIMARY KEY,"
                        "size_gb INTEGER NOT NULL,"
                        "days INTEGER NOT NULL,"
                        "price INTEGER NOT NULL,"
                        "server_id INTEGER NOT NULL,"
                        "description TEXT NULL,"
                        "status BOOLEAN NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id))")
            self.conn.commit()
            logging.info("Plans table created successfully!")

            # cur.execute("CREATE TABLE IF NOT EXISTS user_plans ("
            #             "id INTEGER PRIMARY KEY,"
            #             "telegram_id INTEGER NOT NULL UNIQUE,"
            #             "plan_id INTEGER NOT NULL,"
            #             "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id),"
            #             "FOREIGN KEY (plan_id) REFERENCES plans (id))")
            # self.conn.commit()
            # logging.info("Plans table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS orders ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "plan_id INTEGER NOT NULL,"
                        "user_name TEXT NOT NULL,"
                        "created_at TEXT NOT NULL,"
                        "FOREIGN KEY (telegram_id) REFERENCES user (telegram_id),"
                        "FOREIGN KEY (plan_id) REFERENCES plans (id))")
            self.conn.commit()
            logging.info("Orders table created successfully!")


            # -------------------- Discount Codes (safe migration) --------------------
            cur.execute("CREATE TABLE IF NOT EXISTS discount_codes ("
                        "id INTEGER PRIMARY KEY,"
                        "code TEXT NOT NULL UNIQUE,"
                        "type TEXT NOT NULL,"  # percent | fixed
                        "value INTEGER NOT NULL,"
                        "max_uses INTEGER DEFAULT 0,"  # 0 => unlimited
                        "used_count INTEGER DEFAULT 0,"
                        "per_user_limit INTEGER DEFAULT 1,"
                        "min_price INTEGER DEFAULT 0,"
                        "start_at TEXT NULL,"
                        "end_at TEXT NULL,"
                        "active INTEGER DEFAULT 1,"
                        "note TEXT NULL)")
            self.conn.commit()

            cur.execute("CREATE TABLE IF NOT EXISTS discount_code_redemptions ("
                        "id INTEGER PRIMARY KEY,"
                        "code_id INTEGER NOT NULL,"
                        "telegram_id INTEGER NOT NULL,"
                        "order_id INTEGER NOT NULL,"
                        "used_at TEXT NOT NULL,"
                        "FOREIGN KEY (code_id) REFERENCES discount_codes (id),"
                        "FOREIGN KEY (order_id) REFERENCES orders (id))")
            self.conn.commit()

            # Add new columns to orders table if they don't exist
            cur.execute("PRAGMA table_info(orders)")
            order_cols = [row[1] for row in cur.fetchall()]
            if 'discount_code' not in order_cols:
                cur.execute("ALTER TABLE orders ADD COLUMN discount_code TEXT")
                self.conn.commit()
            if 'discount_amount' not in order_cols:
                cur.execute("ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0")
                self.conn.commit()
            if 'final_price' not in order_cols:
                cur.execute("ALTER TABLE orders ADD COLUMN final_price INTEGER")
                self.conn.commit()


            # -------------------- Pending Invoice (wallet top-up resume) --------------------
            # Store the last pending checkout (buy/renewal) so after wallet charge we can show the invoice again.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pending_invoices ("
                "telegram_id INTEGER PRIMARY KEY,"
                "kind TEXT NOT NULL,"  # buy | renewal
                "plan_id INTEGER NOT NULL,"
                "uuid TEXT NULL,"
                "code_id INTEGER NULL,"
                "discount_code TEXT NULL,"
                "discount_amount INTEGER DEFAULT 0,"
                "final_price INTEGER NULL,"
                "created_at TEXT NOT NULL)"
            )
            self.conn.commit()

            # Add code_id column if table already existed from previous version
            cur.execute("PRAGMA table_info(pending_invoices)")
            pending_cols = [row[1] for row in cur.fetchall()]
            if 'code_id' not in pending_cols:
                cur.execute("ALTER TABLE pending_invoices ADD COLUMN code_id INTEGER")
                self.conn.commit()


            cur.execute("CREATE TABLE IF NOT EXISTS order_subscriptions ("
                        "id INTEGER PRIMARY KEY,"
                        "order_id INTEGER NOT NULL,"
                        "uuid TEXT NOT NULL,"
                        "server_id INTEGER NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id),"
                        "FOREIGN KEY (order_id) REFERENCES orders (id))")
            self.conn.commit()
            logging.info("Order subscriptions table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS non_order_subscriptions ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "uuid TEXT NOT NULL UNIQUE,"
                        "server_id INTEGER NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id),"
                        "FOREIGN KEY (telegram_id) REFERENCES user (telegram_id))")
            self.conn.commit()
            logging.info("Non order subscriptions table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS str_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value TEXT NULL)")
            self.conn.commit()
            logging.info("str_config table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS int_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value INTEGER NOT NULL)")
            self.conn.commit()
            logging.info("int_config table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS bool_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value BOOLEAN NOT NULL)")
            self.conn.commit()
            logging.info("bool_config table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS wallet ("
                        "telegram_id INTEGER NOT NULL UNIQUE,"
                        "balance INTEGER NOT NULL DEFAULT 0,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")
            self.conn.commit()
            logging.info("wallet table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS payments ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "payment_amount INTEGER NOT NULL,"
                        "payment_method TEXT NOT NULL,"
                        "payment_image TEXT NOT NULL,"
                        # "user_name TEXT NOT NULL,"
                        "approved BOOLEAN NULL,"
                        "created_at TEXT NOT NULL,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")
            self.conn.commit()
            logging.info("Payments table created successfully!")

            cur.execute("CREATE TABLE IF NOT EXISTS servers ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "url TEXT NOT NULL,"
                        "title TEXT, description TEXT,"
                        "user_limit INTEGER NOT NULL,"
                        "status BOOLEAN NOT NULL,"
                        "default_server BOOLEAN NOT NULL DEFAULT 0)")
            self.conn.commit()
            logging.info("Servers table created successfully!")

            # -------------------- Referral configs (safe migration) --------------------
            # These keys were introduced after some installs already existed.
            # We ensure they exist without breaking older DBs.
            try:
                # enable flag
                cur.execute(
                    "INSERT OR IGNORE INTO bool_config(key,value) VALUES('referral_enabled', 1)"
                )
                # Separate signup bonuses (stored in Rial). Use old single value if present.
                cur.execute(
                    "INSERT OR IGNORE INTO int_config(key,value) "
                    "VALUES('referral_signup_bonus_referrer_amount', "
                    "COALESCE((SELECT value FROM int_config WHERE key='referral_signup_bonus_amount' LIMIT 1),0))"
                )
                cur.execute(
                    "INSERT OR IGNORE INTO int_config(key,value) "
                    "VALUES('referral_signup_bonus_new_user_amount', "
                    "COALESCE((SELECT value FROM int_config WHERE key='referral_signup_bonus_amount' LIMIT 1),0))"
                )
                # Default share text (if missing)
                cur.execute(
                    "INSERT OR IGNORE INTO str_config(key,value) VALUES(?,?)",
                    (
                        'referral_share_text',
                        "🔹️ هم فیلترشکن بخر هم اینترنت\n\n"
                        "🔹۲ برابر شدن حجم بسته اینترنت شما به‌صورت تضمینی\n\n"
                        "🔹با Dark VPN اکثر سرویس‌ها، مانند:\n"
                        "صرافی‌ها، اسپاتیفای، ردیت، چت‌جی‌پی‌تی، جمینای و...\n"
                        "به‌خوبی کار می‌کنند و هیچ مشکلی ندارند ✅\n\n"
                        "💬 جهت خرید و تست رایگان کلیک کنید:\n",
                    ),
                )
                self.conn.commit()
            except Exception as e:
                logging.error(f"Error while ensuring referral config keys: {e}")


        except Error as e:
            logging.error(f"Error while creating user table \n Error:{e}")
            return False
        return True

    def select_users(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM users")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all users \n Error:{e}")
            return None

    def find_user(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find user!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM users WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"User {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding user {kwargs} \n Error:{e}")
            return None

    def delete_user(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete user!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM users WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"User {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting user {kwargs} \n Error:{e}")
            return False

    def edit_user(self, telegram_id, **kwargs):
        cur = self.conn.cursor()

        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE users SET {key}=? WHERE telegram_id=?", (value, telegram_id))
                self.conn.commit()
                logging.info(f"User [{telegram_id}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating user [{telegram_id}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def add_user(self, telegram_id, full_name,username, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO users(telegram_id, full_name,username, created_at) VALUES(?,?,?,?)",
                        (telegram_id, full_name,username, created_at))
            self.conn.commit()
            logging.info(f"User [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding user [{telegram_id}] \n Error: {e}")
            return False

    def add_plan(self, plan_id, size_gb, days, price, server_id, description=None, status=True):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO plans(id,size_gb, days, price, server_id, description, status) VALUES(?,?,?,?,?,?,?)",
                        (plan_id, size_gb, days, price, server_id, description, status))
            self.conn.commit()
            logging.info(f"Plan [{size_gb}GB] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding plan [{size_gb}GB] \n Error: {e}")
            return False

    def select_plans(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM plans ORDER BY price ASC")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all plans \n Error:{e}")
            return None

    def find_plan(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find plan!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM plans WHERE {key}=? ORDER BY price ASC", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Plan {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding plan {kwargs} \n Error:{e}")
            return None

    def delete_plan(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete plan!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM plans WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"Plan {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting plan {kwargs} \n Error:{e}")
            return False

    def edit_plan(self, plan_id, **kwargs):
        cur = self.conn.cursor()

        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE plans SET {key}=? WHERE id=?", (value, plan_id))
                self.conn.commit()
                logging.info(f"Plan [{plan_id}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating plan [{plan_id}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True
    
    def add_user_plans(self, telegram_id, plan_id):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO user_plans(telegram_id, plan_id) VALUES(?,?)",
                        (telegram_id, plan_id))
            self.conn.commit()
            logging.info(f"Plan [{plan_id}] Reserved for [{telegram_id}] successfully!")
            return True

        except Error as e:
            logging.error(f"Error while Reserving plan [{plan_id}] for [{telegram_id}] \n Error: {e}")
            return False

    def select_user_plans(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM user_plans")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all user_plans \n Error:{e}")
            return None

    def find_user_plans(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find user_plan!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM user_plans WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Plan {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding user_plans {kwargs} \n Error:{e}")
            return None

    def delete_user_plans(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete user_plan!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM user_plans WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"Plan {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting user_plans {kwargs} \n Error:{e}")
            return False

    def edit_user_plans(self, user_plans_id, **kwargs):
        cur = self.conn.cursor()

        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE user_plans SET {key}=? WHERE id=?", (value, user_plans_id))
                self.conn.commit()
                logging.info(f"user_plans [{user_plans_id}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating user_plans [{user_plans_id}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True
    
    def add_order(self, order_id, telegram_id, user_name, plan_id, created_at, discount_code=None, discount_amount=0, final_price=None):
        cur = self.conn.cursor()
        try:
            # Backward compatible insert: orders table may or may not have discount columns (safe migration handles it)
            columns = ["id", "telegram_id", "plan_id", "user_name", "created_at"]
            values = [order_id, telegram_id, plan_id, user_name, created_at]

            # Add discount fields only if provided (and column exists)
            cur.execute("PRAGMA table_info(orders)")
            order_cols = {row[1] for row in cur.fetchall()}

            if "discount_code" in order_cols:
                columns.append("discount_code")
                values.append(discount_code)
            if "discount_amount" in order_cols:
                columns.append("discount_amount")
                values.append(int(discount_amount or 0))
            if "final_price" in order_cols:
                columns.append("final_price")
                values.append(int(final_price) if final_price is not None else None)

            placeholders = ",".join(["?"] * len(columns))
            col_sql = ",".join(columns)
            cur.execute(f"INSERT INTO orders({col_sql}) VALUES({placeholders})", tuple(values))
            self.conn.commit()
            logging.info(f"Order [{order_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{order_id}] \n Error: {e}")
            return False

    def select_orders(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM orders")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

    def find_order(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM orders WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

    def edit_order(self, order_id, **kwargs):
        cur = self.conn.cursor()

        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE orders SET {key}=? WHERE id=?", (value, order_id))
                self.conn.commit()
                logging.info(f"Order [{order_id}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating order [{order_id}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def add_order_subscription(self, sub_id, order_id, uuid, server_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO order_subscriptions(id,order_id,uuid,server_id) VALUES(?,?,?,?)",
                (sub_id, order_id, uuid, server_id))
            self.conn.commit()
            logging.info(f"Order [{order_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{order_id}] \n Error: {e}")
            return False

    def select_order_subscription(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM order_subscriptions")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

    def find_order_subscription(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM order_subscriptions WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

    def edit_order_subscriptions(self, order_id, **kwargs):
        cur = self.conn.cursor()

        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE order_subscriptions SET {key}=? WHERE order_id=?", (value, order_id))
                self.conn.commit()
                logging.info(f"Order [{order_id}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating order [{order_id}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def delete_order_subscription(self, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM order_subscriptions WHERE {key}=?", (value,))
                self.conn.commit()
                logging.info(f"Order [{value}] deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting order [{kwargs}] \n Error: {e}")
            return False

    def add_non_order_subscription(self, non_sub_id, telegram_id, uuid, server_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO non_order_subscriptions(id,telegram_id,uuid,server_id) VALUES(?,?,?,?)",
                (non_sub_id, telegram_id, uuid, server_id))
            self.conn.commit()
            logging.info(f"Order [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{telegram_id}] \n Error: {e}")
            return False

    def select_non_order_subscriptions(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM non_order_subscriptions")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

    def find_non_order_subscription(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM non_order_subscriptions WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

    def delete_non_order_subscription(self, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM non_order_subscriptions WHERE {key}=?", (value,))
                self.conn.commit()
                logging.info(f"Order [{value}] deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting order [{kwargs}] \n Error: {e}")
            return False

    # -------------------- Helpers for add-on plans --------------------
    def find_user_subscription_uuids_by_server(self, telegram_id: int, server_id: int):
        """Return uuids of subscriptions that belong to the user and match server_id.

        Includes both order_subscriptions and non_order_subscriptions.
        """
        uuids = []
        try:
            # ordered subscriptions
            orders = self.find_order(telegram_id=telegram_id)
            if orders:
                for order in orders:
                    subs = self.find_order_subscription(order_id=order['id'])
                    if not subs:
                        continue
                    for s in subs:
                        if int(s.get('server_id') or 0) == int(server_id):
                            uuids.append(s['uuid'])

            # linked subscriptions
            non = self.find_non_order_subscription(telegram_id=telegram_id)
            if non:
                for s in non:
                    if int(s.get('server_id') or 0) == int(server_id):
                        uuids.append(s['uuid'])

            # unique
            return list(dict.fromkeys([u for u in uuids if u]))
        except Exception as e:
            logging.error(f"Error while finding user subscriptions by server. Error: {e}")
            return []

    def edit_bool_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE bool_config SET {key}=? WHERE key=?", (value, key_row))
                self.conn.commit()
                logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def find_bool_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM bool_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None

    def add_bool_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO bool_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()
            

    def select_bool_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM bool_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

    def select_str_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM str_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

    def find_str_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM str_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None

    def edit_str_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        for key, value in kwargs.items():
            try:
                cur.execute(f"UPDATE str_config SET {key}=? WHERE key=?", (value, key_row))
                self.conn.commit()
                logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def add_str_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO str_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def select_int_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM int_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

    def find_int_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM int_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None
    def edit_int_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        for key, value in kwargs.items():            
            try:
                cur.execute(f"UPDATE int_config SET {key}=? WHERE key=?", (value, key_row))
                self.conn.commit()
                logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
            except Error as e:
                logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                return False

        return True

    def add_int_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO int_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def set_default_configs(self):
        
        self.add_bool_config("visible_hiddify_hyperlink", True)
        self.add_bool_config("three_random_num_price", False)
        self.add_bool_config("force_join_channel", False)
        self.add_bool_config("panel_auto_backup", True)
        self.add_bool_config("bot_auto_backup", True)
        self.add_bool_config("test_subscription", True)
        self.add_bool_config("reminder_notification", True)
        
        self.add_bool_config("renewal_subscription_status", True)
        self.add_bool_config("buy_subscription_status", True)

        # Add-on plans (volume/time) feature flags
        # addon_plans_enabled: show volume/time add-on plans in renewal flow
        # addon_plans_show_in_buy: also show an "add-on" entry inside the buy flow
        self.add_bool_config("addon_plans_enabled", False)
        self.add_bool_config("addon_plans_show_in_buy", False)

        # Referral system
        self.add_bool_config("referral_enabled", True)


        self.add_bool_config("visible_conf_dir", False)
        self.add_bool_config("visible_conf_sub_auto", True)
        self.add_bool_config("visible_conf_sub_url", False)
        self.add_bool_config("visible_conf_sub_url_b64", False)
        self.add_bool_config("visible_conf_clash", False)
        self.add_bool_config("visible_conf_hiddify", False)
        self.add_bool_config("visible_conf_sub_sing_box", False)
        self.add_bool_config("visible_conf_sub_full_sing_box", False)

        self.add_str_config("bot_admin_id", None)
        self.add_str_config("bot_token_admin", None)
        self.add_str_config("bot_token_client", None)
        self.add_str_config("bot_lang", None)

        self.add_str_config("card_number", None)
        self.add_str_config("card_holder", None)
        self.add_str_config("support_username", None)
        self.add_str_config("channel_id", None)
        self.add_str_config("msg_user_start", None)

        self.add_str_config("msg_manual_android", None)
        self.add_str_config("msg_manual_ios", None)
        self.add_str_config("msg_manual_windows", None)
        self.add_str_config("msg_manual_mac", None)
        self.add_str_config("msg_manual_linux", None)

        self.add_str_config("msg_faq", None)

        # Referral texts (editable via DB/admin settings later)
        self.add_str_config(
            "referral_dashboard_text",
            "🤔 زیرمجموعه گیری به چه صورت است ؟\n\n"
            "👨🏻‍💻 ما برای شما محیطی فراهم کرده ایم  تا بتوانید بدون پرداخت حتی 1 ریال به ما، بتوانید موجودی کیف پول خودتان را در ربات افزایش دهید و از خدمات ربات استفاده نمایید.\n\n"
            "👥 شما میتوانید با دعوت دوستان و آشنایان خود به ربات ما از طریق لینک اختصاصی شما! کسب درآمد کنید و حتی با هر خرید زیرمجموعه ها به شما پورسانت داده خواهد شد.\n\n"
            "👤 شما می توانید با استفاده از دکمه 📎 اشتراک گذاری لینک بنر خود را به اشتراک بگذارید و برای خود زیرمجموعه جمع کنید.\n\n"
            "💵 مبلغ هدیه به ازای هر عضویت : {signup_bonus_toman} تومان\n"
            "💴 میزان پورسانت از خرید زیرمجموعه : {commission_percent}٪\n\n"
            "📊 آمار شما:\n"
            "• 👥 زیرمجموعه‌ها: {ref_count} نفر\n"
            "• 🛒 خریدها: {buy_count} عدد\n"
            "• 💵 مجموع خرید: {buy_total_toman} تومان\n\n"
            "• 🎉 مجموع هدیه عضویت: {signup_total_toman} تومان\n"
            "💸 مجموع پورسانت زیر مجموعه: {commission_total_toman} تومان"
        )

        self.add_str_config(
            "referral_share_text",
            "🔹️ هم فیلترشکن بخر هم اینترنت\n\n"
            "🔹۲ برابر شدن حجم بسته اینترنت شما به‌صورت تضمینی\n\n"
            "🔹با Dark VPN اکثر سرویس‌ها، مانند:\n"
            "صرافی‌ها، اسپاتیفای، ردیت، چت‌جی‌پی‌تی، جمینای و...\n"
            "به‌خوبی کار می‌کنند و هیچ مشکلی ندارند ✅\n\n"
            "💬 جهت خرید و تست رایگان کلیک کنید:\n"
        )

        self.add_int_config("min_deposit_amount", 10000)

        # Referral amounts are stored in Rial (same as wallet)
        # Backward-compatible single value (legacy)
        self.add_int_config("referral_signup_bonus_amount", 0)
        # New: separate bonuses
        self.add_int_config("referral_signup_bonus_referrer_amount", 0)
        self.add_int_config("referral_signup_bonus_new_user_amount", 0)
        self.add_int_config("referral_commission_percent", 9)

        self.add_int_config("reminder_notification_days", 3)
        self.add_int_config("reminder_notification_usage", 3)

        self.add_int_config("test_sub_days", 1)
        self.add_int_config("test_sub_size_gb", 1)
        
        self.add_int_config("advanced_renewal_days", 3)
        self.add_int_config("advanced_renewal_usage", 3)
        
        self.add_int_config("renewal_method", 1)



    def add_wallet(self, telegram_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO wallet(telegram_id) VALUES(?)",
                (telegram_id,))
            self.conn.commit()
            logging.info(f"Balance [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding balance [{telegram_id}] \n Error: {e}")
            return False

    def select_wallet(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM wallet")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all balance \n Error:{e}")
            return None

    def find_wallet(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find balance!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM wallet WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Balance {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding balance {kwargs} \n Error:{e}")
            return None

    def edit_wallet(self, telegram_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"UPDATE wallet SET {key}=? WHERE telegram_id=?", (value, telegram_id,))
                self.conn.commit()
                logging.info(f"balance successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating balance [{key}] to [{value}] \n Error: {e}")
            return False

    def add_payment(self, payment_id, telegram_id, payment_amount, payment_method, payment_image, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO payments(id,telegram_id, payment_amount,payment_method,payment_image,created_at) VALUES(?,?,?,?,?,?)",
                (payment_id, telegram_id, payment_amount, payment_method, payment_image, created_at))
            self.conn.commit()
            logging.info(f"Payment [{payment_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding payment [{payment_id}] \n Error: {e}")
            return False

    def edit_payment(self, payment_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"UPDATE payments SET {key}=? WHERE id=?", (value, payment_id))
                self.conn.commit()
                logging.info(f"payment successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating payment [{key}] to [{value}] \n Error: {e}")
            return False

    def find_payment(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find payment!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM payments WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Payment {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding payment {kwargs} \n Error:{e}")
            return None
        
    def select_payments(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM payments")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all payments \n Error:{e}")
            return None
    
    def select_servers(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM servers")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all servers \n Error:{e}")
            return None
        
    def add_server(self, url, user_limit, title=None, description=None, status=True, default_server=False):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO servers(url,title,description,user_limit,status,default_server) VALUES(?,?,?,?,?,?)",
                (url, title, description, user_limit, status, default_server))
            self.conn.commit()
            logging.info(f"Server [{url}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding server [{url}] \n Error: {e}")
            return False
    
    def edit_server(self, server_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"UPDATE servers SET {key}=? WHERE id=?", (value, server_id))
                self.conn.commit()
                logging.info(f"Server [{server_id}] successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating server [{server_id}] [{key}] to [{value}] \n Error: {e}")
            return False
    
    def find_server(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find server!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"SELECT * FROM servers WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.info(f"Server {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding server {kwargs} \n Error:{e}")
            return None
        
    def delete_server(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete server!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                cur.execute(f"DELETE FROM servers WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"server {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting server {kwargs} \n Error:{e}")
            return False
        
    
    def backup_to_json(self, backup_dir):
        try:

            backup_data = {}  # Store backup data in a dictionary

            # List of tables to backup
            tables = ['users', 'plans', 'orders', 'order_subscriptions', 'non_order_subscriptions',
                      'str_config', 'int_config', 'bool_config', 'wallet', 'payments', 'servers']

            for table in tables:
                cur = self.conn.cursor()
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()

                # Convert rows to list of dictionaries
                table_data = []
                for row in rows:
                    columns = [column[0] for column in cur.description]
                    table_data.append(dict(zip(columns, row)))

                backup_data[table] = table_data
            return backup_data

        except sqlite3.Error as e:
            logging.error('SQLite error:', str(e))
            return False
    def restore_from_json(self, backup_file):
        logging.info(f"Restoring database from {backup_file}...")
        try:
            cur = self.conn.cursor()

            with open(backup_file, 'r') as json_file:
                backup_data = json.load(json_file)
                
            if not isinstance(backup_data, dict):
                logging.error('Backup data should be a dictionary.')
                print('Backup data should be a dictionary.')
                return
            # print(backup_data.get('version'), VERSION)
            # if backup_data.get('version') != VERSION:
            #     if backup_data.get('version') is None:
            #         logging.error('Backup data version is not found.')
            #         print('Backup data version is not found.')
            #         return
            #     if VERSION.find('-pre'):
            #         VERSION = VERSION.split('-pre')[0]
            #     if is_version_less(backup_data.get('version'),VERSION ):
            #         logging.error('Backup data version is less than current version.')
            #         print('Backup data version is less than current version.')
            #         if is_version_less(backup_data.get('version'), '5.5.0'):
            #             logging.error('Backup data version is less than 5.5.0.')
            #             print('Backup data version is less than 5.5.0.')
            #             return 

            self.conn.execute('BEGIN TRANSACTION')

            for table, data in backup_data.items():
                if table == 'version':
                    continue
                logging.info(f"Restoring table {table}...")
                for entry in data:
                    if not isinstance(entry, dict):
                        logging.error('Invalid entry format. Expected a dictionary.')
                        print('Invalid entry format. Expected a dictionary.')
                        continue

                    keys = ', '.join(entry.keys())
                    placeholders = ', '.join(['?' for _ in entry.values()])
                    values = tuple(entry.values())
                    query = f"INSERT OR REPLACE INTO {table} ({keys}) VALUES ({placeholders})"
                    logging.info(f"Query: {query}")
                    
                    try:
                        cur.execute(query, values)
                    except sqlite3.Error as e:
                        logging.error('SQLite error:', str(e))
                        logging.error('Entry:', entry)
                        print('SQLite error:', str(e))
                        print('Entry:', entry)

            self.conn.commit()
            logging.info('Database restored successfully.')
            return True

        except sqlite3.Error as e:
            logging.error('SQLite error:', str(e))
            return False
    


    # -------------------- Discount Codes --------------------
    def add_discount_code(self, code, dtype, value, max_uses=0, per_user_limit=1, min_price=0, start_at=None, end_at=None, active=1, note=None):
        cur = self.conn.cursor()
        payload = (
            code.upper().strip(), dtype, int(value), int(max_uses), int(per_user_limit), int(min_price),
            start_at, end_at, int(active), note
        )

        def _do_insert():
            cur.execute(
                "INSERT INTO discount_codes(code,type,value,max_uses,used_count,per_user_limit,min_price,start_at,end_at,active,note) "
                "VALUES(?,?,?,?,0,?,?,?,?,?,?)",
                payload,
            )
            self.conn.commit()
            return True

        try:
            return _do_insert()
        except Error as e:
            # In some deployments, admins upgrade from an older DB that didn't have gift-code tables.
            # If the migration didn't run previously (or failed mid-way), self-heal by creating tables once.
            msg = str(e).lower()
            if "no such table" in msg and "discount_codes" in msg:
                try:
                    self.create_user_table()
                    return _do_insert()
                except Exception as e2:
                    logging.error(f"Error while adding discount code {code} after migration\n Error:{e2}")
                    return False
            logging.error(f"Error while adding discount code {code} \n Error:{e}")
            return False

    def list_discount_codes(self, active_only=False):
        cur = self.conn.cursor()
        try:
            if active_only:
                cur.execute("SELECT * FROM discount_codes WHERE active=1 ORDER BY id DESC")
            else:
                cur.execute("SELECT * FROM discount_codes ORDER BY id DESC")
            rows = cur.fetchall()
            return [dict(zip([key[0] for key in cur.description], row)) for row in rows]
        except Error as e:
            logging.error(f"Error while listing discount codes \n Error:{e}")
            return None

    def find_discount_code(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find discount code!")
            return None
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                if key == "code":
                    value = value.upper().strip()
                cur.execute(f"SELECT * FROM discount_codes WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if not rows:
                return None
            return [dict(zip([key[0] for key in cur.description], row)) for row in rows]
        except Error as e:
            logging.error(f"Error while finding discount code {kwargs} \n Error:{e}")
            return None

    def toggle_discount_code(self, code_id, active: int):
        cur = self.conn.cursor()
        try:
            cur.execute("UPDATE discount_codes SET active=? WHERE id=?", (int(active), int(code_id)))
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while toggling discount code {code_id} \n Error:{e}")
            return False

    def update_discount_code(self, code_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for k, v in kwargs.items():
                cur.execute(f"UPDATE discount_codes SET {k}=? WHERE id=?", (v, int(code_id)))
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while updating discount code {code_id} \n Error:{e}")
            return False

    def delete_discount_code(self, code_id: int):
        """Delete a discount code and its redemption records.

        We intentionally do not modify existing orders that may reference the code textually.
        """
        cur = self.conn.cursor()
        def _do_delete(cid: int):
            # Clean dependent rows first (foreign keys may not be enforced).
            cur.execute("DELETE FROM discount_code_redemptions WHERE code_id=?", (cid,))
            cur.execute("DELETE FROM discount_codes WHERE id=?", (cid,))
            self.conn.commit()
            return True

        try:
            return _do_delete(int(code_id))
        except Error as e:
            msg = str(e).lower()
            # Self-heal missing tables (old DB) and retry once.
            if "no such table" in msg and ("discount_codes" in msg or "discount_code_redemptions" in msg):
                try:
                    self.create_user_table()
                    return _do_delete(int(code_id))
                except Exception as e2:
                    logging.error(f"Error while deleting discount code {code_id} after migration\n Error:{e2}")
                    return False
            logging.error(f"Error while deleting discount code {code_id} \n Error:{e}")
            return False

    def validate_discount_code(self, code: str, telegram_id: int, price: int):
        """Validate and calculate discount. Returns dict with ok, reason, ..."""
        code = (code or "").upper().strip()
        price = int(price)
        found = self.find_discount_code(code=code)
        if not found:
            return {"ok": False, "reason": "INVALID"}
        dc = found[0]

        if int(dc.get("active", 0)) != 1:
            return {"ok": False, "reason": "INACTIVE", "code": dc}

        now = datetime.datetime.now()
        if dc.get("start_at"):
            try:
                if now < datetime.datetime.fromisoformat(dc["start_at"]):
                    return {"ok": False, "reason": "INACTIVE", "code": dc}
            except Exception:
                pass
        if dc.get("end_at"):
            try:
                if now > datetime.datetime.fromisoformat(dc["end_at"]):
                    return {"ok": False, "reason": "EXPIRED", "code": dc}
            except Exception:
                pass

        if price < int(dc.get("min_price") or 0):
            return {"ok": False, "reason": "MIN_PRICE", "code": dc}

        max_uses = int(dc.get("max_uses") or 0)
        used_count = int(dc.get("used_count") or 0)
        if max_uses > 0 and used_count >= max_uses:
            return {"ok": False, "reason": "USAGE_LIMIT", "code": dc}

        per_user_limit = int(dc.get("per_user_limit") or 1)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM discount_code_redemptions WHERE code_id=? AND telegram_id=?", (dc["id"], int(telegram_id)))
        user_used = int(cur.fetchone()[0] or 0)
        if per_user_limit > 0 and user_used >= per_user_limit:
            return {"ok": False, "reason": "ALREADY_USED", "code": dc}

        dtype = dc.get("type")
        value = int(dc.get("value") or 0)
        if dtype == "percent":
            discount_amount = int(round(price * value / 100))
        else:  # fixed
            discount_amount = value

        if discount_amount < 0:
            discount_amount = 0
        if discount_amount > price:
            discount_amount = price
        final_price = price - discount_amount

        return {
            "ok": True,
            "code_id": dc["id"],
            "code": dc["code"],
            "discount_amount": discount_amount,
            "final_price": final_price,
            "dc": dc
        }

    def redeem_discount_code(self, code_id: int, telegram_id: int, order_id: int):
        cur = self.conn.cursor()
        try:
            used_at = datetime.datetime.now().isoformat(timespec="seconds")
            cur.execute(
                "INSERT INTO discount_code_redemptions(code_id,telegram_id,order_id,used_at) VALUES(?,?,?,?)",
                (int(code_id), int(telegram_id), int(order_id), used_at)
            )
            cur.execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE id=?", (int(code_id),))
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while redeeming discount code {code_id} \n Error:{e}")
            return False


    # -------------------- Pending Invoice (wallet top-up resume) --------------------
    def set_pending_invoice(self, telegram_id: int, kind: str, plan_id: int, uuid: str | None = None,
                            code_id: int | None = None, discount_code: str | None = None, discount_amount: int = 0, final_price: int | None = None):
        """Upsert a pending invoice so we can re-show it after wallet top-up confirmation."""
        cur = self.conn.cursor()
        try:
            created_at = datetime.datetime.now().isoformat(timespec="seconds")
            cur.execute(
                "INSERT OR REPLACE INTO pending_invoices(telegram_id,kind,plan_id,uuid,code_id,discount_code,discount_amount,final_price,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (int(telegram_id), str(kind), int(plan_id), uuid, int(code_id) if code_id is not None else None,
                 discount_code, int(discount_amount or 0), int(final_price) if final_price is not None else None, created_at)
            )
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while setting pending invoice for {telegram_id} \n Error:{e}")
            return False

    def get_pending_invoice(self, telegram_id: int):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM pending_invoices WHERE telegram_id=?", (int(telegram_id),))
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([key[0] for key in cur.description], row))
        except Error as e:
            logging.error(f"Error while getting pending invoice for {telegram_id} \n Error:{e}")
            return None

    def clear_pending_invoice(self, telegram_id: int):
        cur = self.conn.cursor()
        try:
            cur.execute("DELETE FROM pending_invoices WHERE telegram_id=?", (int(telegram_id),))
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while clearing pending invoice for {telegram_id} \n Error:{e}")
            return False


    # -------------------- Referral system helpers --------------------
    def ensure_user_referral_code(self, telegram_id: int) -> str | None:
        """Make sure the given user has a referral_code set.

        We use telegram_id as a stable default code (simple, short enough, and unique in practice).
        """
        try:
            user_rows = self.find_user(telegram_id=int(telegram_id))
            if not user_rows:
                return None
            u = user_rows[0]
            code = u.get('referral_code')
            if code:
                return str(code)
            code = str(int(telegram_id))
            self.edit_user(int(telegram_id), referral_code=code)
            return code
        except Exception as e:
            logging.error(f"Error while ensuring referral code for {telegram_id}: {e}")
            return None

    def find_user_by_referral_code(self, code: str):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM users WHERE referral_code=?", (str(code),))
            rows = cur.fetchall()
            if not rows:
                return None
            return [dict(zip([key[0] for key in cur.description], row)) for row in rows]
        except Error as e:
            logging.error(f"Error while finding user by referral code {code} \n Error:{e}")
            return None

    def set_user_referred_by(self, telegram_id: int, referrer_id: int, pending_bonus: bool = False) -> bool:
        """Set referred_by only if not set yet."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT referred_by FROM users WHERE telegram_id=?", (int(telegram_id),))
            row = cur.fetchone()
            if not row:
                return False
            if row[0] is not None:
                return True
            cur.execute(
                "UPDATE users SET referred_by=?, referral_pending_bonus=? WHERE telegram_id=?",
                (int(referrer_id), 1 if pending_bonus else 0, int(telegram_id)),
            )
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while setting referred_by for {telegram_id} \n Error:{e}")
            return False

    def get_referrer_of_user(self, telegram_id: int) -> int | None:
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT referred_by FROM users WHERE telegram_id=?", (int(telegram_id),))
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            return int(row[0])
        except Error as e:
            logging.error(f"Error while getting referrer for {telegram_id} \n Error:{e}")
            return None

    def mark_referral_bonus_paid(self, telegram_id: int):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE users SET referral_bonus_paid=1, referral_pending_bonus=0 WHERE telegram_id=?",
                (int(telegram_id),),
            )
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while marking referral bonus paid for {telegram_id} \n Error:{e}")
            return False

    def is_referral_bonus_paid(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT referral_bonus_paid FROM users WHERE telegram_id=?", (int(telegram_id),))
            row = cur.fetchone()
            return bool(row and int(row[0]) == 1)
        except Error as e:
            logging.error(f"Error while checking referral bonus paid for {telegram_id} \n Error:{e}")
            return False

    def is_referral_bonus_pending(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT referral_pending_bonus FROM users WHERE telegram_id=?", (int(telegram_id),))
            row = cur.fetchone()
            return bool(row and int(row[0]) == 1)
        except Error as e:
            logging.error(f"Error while checking referral bonus pending for {telegram_id} \n Error:{e}")
            return False

    def add_referral_earning(self, telegram_id: int, earning_type: str, amount: int,
                             related_telegram_id: int | None = None, order_id: int | None = None,
                             created_at: str | None = None) -> bool:
        cur = self.conn.cursor()
        try:
            if created_at is None:
                created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT OR IGNORE INTO referral_earnings(telegram_id,type,amount,related_telegram_id,order_id,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (int(telegram_id), str(earning_type), int(amount),
                 int(related_telegram_id) if related_telegram_id is not None else None,
                 int(order_id) if order_id is not None else None,
                 str(created_at)),
            )
            self.conn.commit()
            return True
        except Error as e:
            logging.error(f"Error while adding referral earning for {telegram_id} \n Error:{e}")
            return False

    def get_referral_earnings(self, telegram_id: int, limit: int = 10):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT * FROM referral_earnings WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
                (int(telegram_id), int(limit)),
            )
            rows = cur.fetchall()
            if not rows:
                return []
            return [dict(zip([key[0] for key in cur.description], row)) for row in rows]
        except Error as e:
            logging.error(f"Error while selecting referral earnings for {telegram_id} \n Error:{e}")
            return []

    def count_referred_users(self, referrer_id: int) -> int:
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT COUNT(1) FROM users WHERE referred_by=?", (int(referrer_id),))
            row = cur.fetchone()
            return int(row[0] or 0)
        except Error as e:
            logging.error(f"Error while counting referred users for {referrer_id} \n Error:{e}")
            return 0

    def referral_purchase_stats(self, referrer_id: int):
        """Return (purchase_count, total_purchase_rial) for referrer based on orders of referred users."""
        cur = self.conn.cursor()
        try:
            # Use final_price if available; otherwise fall back to plan.price
            cur.execute(
                "SELECT COUNT(1) as cnt, COALESCE(SUM(CASE WHEN o.final_price IS NOT NULL THEN o.final_price ELSE p.price END),0) as total "
                "FROM orders o "
                "JOIN users u ON u.telegram_id=o.telegram_id "
                "JOIN plans p ON p.id=o.plan_id "
                "WHERE u.referred_by=?",
                (int(referrer_id),),
            )
            row = cur.fetchone()
            if not row:
                return 0, 0
            return int(row[0] or 0), int(row[1] or 0)
        except Error as e:
            logging.error(f"Error while calculating referral purchase stats for {referrer_id} \n Error:{e}")
            return 0, 0

    def referral_earnings_totals(self, telegram_id: int):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN type='signup_bonus' THEN amount ELSE 0 END),0) as signup_total, "
                "COALESCE(SUM(CASE WHEN type='commission' THEN amount ELSE 0 END),0) as commission_total "
                "FROM referral_earnings WHERE telegram_id=?",
                (int(telegram_id),),
            )
            row = cur.fetchone()
            if not row:
                return 0, 0
            return int(row[0] or 0), int(row[1] or 0)
        except Error as e:
            logging.error(f"Error while calculating referral earnings totals for {telegram_id} \n Error:{e}")
            return 0, 0

    def select_top_referrers_stats(self, limit: int = 20):
        """Top referrers by total referral earnings."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT u.telegram_id, u.full_name, u.username, "
                "(SELECT COUNT(1) FROM users uu WHERE uu.referred_by=u.telegram_id) as referred_count, "
                "COALESCE(SUM(CASE WHEN r.type='signup_bonus' THEN r.amount ELSE 0 END),0) as signup_total, "
                "COALESCE(SUM(CASE WHEN r.type='commission' THEN r.amount ELSE 0 END),0) as commission_total, "
                "COALESCE(SUM(r.amount),0) as total "
                "FROM users u "
                "LEFT JOIN referral_earnings r ON r.telegram_id=u.telegram_id "
                "GROUP BY u.telegram_id "
                "ORDER BY total DESC "
                "LIMIT ?",
                (int(limit),),
            )
            rows = cur.fetchall()
            if not rows:
                return []
            return [dict(zip([key[0] for key in cur.description], row)) for row in rows]
        except Error as e:
            logging.error(f"Error while selecting top referrers stats \n Error:{e}")
            return []




USERS_DB_LOC = os.path.join(os.getcwd(), "Database", "hidyBot.db")

USERS_DB = UserDBManager(USERS_DB_LOC)

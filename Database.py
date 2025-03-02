import sqlite3
import os
from dotenv import load_dotenv


# --------------------------
# 数据库管理器（封装数据库操作）
# --------------------------
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_connection(self):
        """获取数据库连接（使用上下文管理）"""
        return sqlite3.connect(self.db_path)


load_dotenv()
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")
database_manager = DatabaseManager(DATABASE_PATH)

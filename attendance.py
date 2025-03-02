import os
import sqlite3
import json
import time
import requests
import random
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from Logger import setup_logger
from Database import database_manager

logger = setup_logger("attendance")


# --------------------------
# 配置类（常量集中管理）
# --------------------------
class AttendanceConfig:
    def __init__(self):
        load_dotenv()
        self.USER_MAC_LIST_PATH = os.getenv("USER_MAC_LIST_PATH", "userlist.json")
        self.ROUTER_URL = os.getenv("ROUTER_URL", "192.168.1.1")
        self.ROUTER_PWD = os.getenv("ROUTER_PWD", "default_password")

        # 验证必要配置
        if not os.path.exists(self.USER_MAC_LIST_PATH):
            raise FileNotFoundError(f"MAC地址列表文件不存在: {self.USER_MAC_LIST_PATH}")


# --------------------------
# 路由器客户端（封装路由器API操作）
# --------------------------
class RouterClient:
    def __init__(self, config):
        self.config = config
        self.token = None
        self.token_expiry = None
        self.nonce = None

    def _generate_nonce(self):
        """生成随机设备标识"""
        return "_".join(
            [
                str(0),  # type
                "11:22:33:44:55:66",  # 固定设备MAC
                str(int(time.time())),
                str(random.randint(0, 9999)),
            ]
        )

    def _encrypt_password(self, password):
        """密码加密逻辑"""
        key = "a2ffa5c9be07488bbb04a3a47d3c5f6a"
        self.nonce = self._generate_nonce()
        sha1_pwd = hashlib.sha1((password + key).encode()).hexdigest()
        return hashlib.sha1((self.nonce + sha1_pwd).encode()).hexdigest()

    def _refresh_token(self):
        """获取/刷新路由器Token"""
        try:
            params = {
                "username": "admin",
                "password": self._encrypt_password(self.config.ROUTER_PWD),
                "logtype": 2,
                "nonce": self.nonce,
            }
            response = requests.get(
                f"http://{self.config.ROUTER_URL}/cgi-bin/luci/api/xqsystem/login",
                params=params,
                timeout=10,
            )
            self.token = response.json().get("token")
            self.token_expiry = time.time() + 3600  # 假设token有效期1小时
            return self.token
        except Exception as e:
            logger.error(f"获取路由器Token失败: {str(e)}")
            return None

    def get_online_devices(self):
        """获取在线设备列表"""
        if not self.token or time.time() > self.token_expiry:
            if not self._refresh_token():
                return []

        try:
            response = requests.get(
                f"http://{self.config.ROUTER_URL}/cgi-bin/luci/;stok={self.token}/api/misystem/devicelist",
                timeout=10,
            )
            if response.json().get("code") == 401:
                self._refresh_token()
                return self.get_online_devices()

            return [
                {"mac": device["mac"], "name": device["name"]}
                for device in response.json().get("list", [])
            ]
        except Exception as e:
            logger.error(f"获取设备列表失败: {str(e)}")
            return []


# --------------------------
# 考勤服务（核心业务逻辑）
# --------------------------
class AttendanceService:
    def __init__(self, config):
        self.config = config
        self.db = database_manager
        self.router = RouterClient(config)
        self.user_list = self._load_user_list()

    def _init_db(self):
        """初始化数据库结构"""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_time DATETIME NOT NULL,
                    end_time DATETIME NOT NULL
                )
            """
            )

    def _load_user_list(self):
        """加载MAC地址白名单"""
        try:
            with open(self.config.USER_MAC_LIST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载用户列表失败: {str(e)}")
            return []

    def _update_attendance_record(self, name, current_time):
        """更新考勤记录（含时间合并逻辑）"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                # 获取最新记录
                cursor.execute(
                    """
                    SELECT id, start_time, end_time 
                    FROM attendance 
                    WHERE name = ? 
                    ORDER BY end_time DESC 
                    LIMIT 1""",
                    (name,),
                )
                record = cursor.fetchone()

                current_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

                if not record:
                    cursor.execute(
                        """
                            INSERT INTO attendance (name, start_time, end_time)
                            VALUES (?, ?, ?)""",
                        (name, current_str, current_str),
                    )
                    conn.commit()
                    return

                record_id, start_str, end_str = record
                start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")

                # 判断是否需要合并
                if (current_time - end_dt).total_seconds() <= 1800:  # 30分钟
                    periods = []
                    # 处理跨天分割
                    temp_start = start_dt
                    while temp_start.date() < current_time.date():
                        day_end = datetime(
                            temp_start.year,
                            temp_start.month,
                            temp_start.day,
                            23,
                            59,
                            59,
                        )
                        periods.append((temp_start, day_end))
                        temp_start = day_end + timedelta(seconds=1)
                    periods.append((temp_start, current_time))

                    # 删除原记录
                    cursor.execute("DELETE FROM attendance WHERE id = ?", (record_id,))

                else:
                    periods = [(current_time, current_time)]

                for s, e in periods:
                    cursor.execute(
                        """
                        INSERT INTO attendance (name, start_time, end_time)
                        VALUES (?, ?, ?)""",
                        (
                            name,
                            s.strftime("%Y-%m-%d %H:%M:%S"),
                            e.strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"更新考勤记录失败: {str(e)}")

    def run_monitoring(self):
        """启动监控主循环"""
        logger.info("启动考勤监控服务")
        while True:
            try:
                devices = self.router.get_online_devices()
                online_users = [
                    user["name"]
                    for user in self.user_list
                    if any(d["mac"] == user["MAC"] for d in devices)
                ]

                current_time = datetime.now()
                for user in self.user_list:
                    if user["name"] in online_users:
                        self._update_attendance_record(user["name"], current_time)

                logger.info(
                    f"在线用户: {len(online_users)} - {', '.join(online_users)}"
                )
                time.sleep(300)
            except KeyboardInterrupt:
                logger.info("服务已手动终止")
                break
            except Exception as e:
                logger.error(f"监控循环错误: {str(e)}")
                time.sleep(60)


# --------------------------
# 初始化配置
# --------------------------

if __name__ == "__main__":
    try:
        config = AttendanceConfig()
        service = AttendanceService(config)
        service.run_monitoring()
    except Exception as e:
        logger.critical(f"服务启动失败: {str(e)}")
        exit(1)

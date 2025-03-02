import sqlite3
import logging
from datetime import datetime
from typing import List, Tuple, Generator
import os
from dotenv import load_dotenv
from logging.handlers import TimedRotatingFileHandler
from api.feishu.api_servers import APIContainer  # 假设飞书API封装
from Logger import setup_logger
from Database import database_manager

logger = setup_logger("course_manager")


class CourseConfig:
    """课程表配置管理"""

    def __init__(self):
        load_dotenv()
        self.sheet_token = os.getenv("COURSE_SHEET_TOKEN")
        self.sheet_id = os.getenv("COURSE_SHEET_ID")
        self.app_id = os.getenv("APP_ID")
        self.app_secret = os.getenv("APP_SECRET")
        self.lark_host = os.getenv("LARK_HOST")

        # 验证必要配置
        if not all([self.sheet_token, self.sheet_id, self.app_id, self.app_secret]):
            raise ValueError("缺少必要的飞书API配置参数")


class CourseManager:
    """课程表管理服务"""

    WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    USERNAME_COLUMN = "姓名"
    CLASSES_PER_DAY = 5

    def __init__(self, config: CourseConfig, fs_api: APIContainer):
        """
        初始化课程管理器
        :param config: 配置对象
        :param fs_api: 飞书API客户端
        """
        self.config = config
        self.fs_api = fs_api
        self.logger = logger
        self.db = database_manager
        self._init_database()

    def _init_database(self):
        """初始化数据库结构"""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS class_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    day INT NOT NULL CHECK(day BETWEEN 1 AND 7),
                    class_index INT NOT NULL CHECK(class_index BETWEEN 1 AND 5),
                    week_range_start INT NOT NULL,
                    week_range_end INT NOT NULL,
                    UNIQUE(name, day, class_index, week_range_start)
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_schedule ON class_schedule (name, day)"
            )

    @staticmethod
    def parse_week_ranges(week_str: str) -> List[Tuple[int, int]]:
        """
        解析周数范围字符串
        :param week_str: 格式如 "1-3,5,7-9"
        :return: 周数范围列表 [(start, end), ...]
        """
        ranges = []
        week_str = week_str.translate(str.maketrans("，、～~", ",,--"))

        for part in week_str.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    start, end = map(int, part.split("-"))
                    ranges.append((start, end))
                except ValueError:
                    raise ValueError(f"无效的周数范围: {part}")
            else:
                try:
                    week = int(part)
                    ranges.append((week, week))
                except ValueError:
                    raise ValueError(f"无效的周数: {part}")

        return sorted(ranges, key=lambda x: x[0])

    def _fetch_index(self, rows: List[str]) -> Tuple[int, List[int]]:
        """
        获取关键索引
        :return: (姓名列索引, 星期列索引列表)
        """
        name_col = None
        day_cols = []
        # 查找姓名列
        for row in rows:
            try:
                name_col = row.index(self.USERNAME_COLUMN)
            except ValueError as e:
                continue
        # 查找星期列
        for row in rows:
            try:
                day_cols = (
                    [row.index(day) for day in self.WEEKDAYS if day in row]
                    if row.index(self.WEEKDAYS[0])
                    else day_cols
                )
            except ValueError:
                continue
        if not name_col or not day_cols:
            self.logger.error("表解析失败: %s", str(e))
            raise RuntimeError("无法定位必要列") from e
        return name_col, day_cols

    def _process_data_row(
        self, data: List[str], name_col: int, day_cols: List[int]
    ) -> Generator:
        """
        逐行处理数据并生成课程记录
        :yield: (name, day, class_index, week_start, week_end)
        """
        for row in data:
            name = row[name_col]

            for day_idx, col in enumerate(day_cols):
                for class_num in range(self.CLASSES_PER_DAY):
                    cell_idx = col + class_num
                    if cell_idx >= len(row):
                        continue

                    cell_value = str(row[cell_idx]).strip()
                    if not row[cell_idx]:
                        continue

                    try:
                        week_ranges = self.parse_week_ranges(cell_value)
                        for week_range in week_ranges:
                            yield (
                                name,
                                day_idx + 1,  # 数据库存储周一=1
                                class_num + 1,
                                week_range[0],
                                week_range[1],
                            )
                    except ValueError as e:
                        self.logger.warning(
                            "跳过无效周数数据: 行%d 列%d - %s",
                            data.index(row) + 1,
                            cell_idx + 1,
                            str(e),
                        )

    def refresh_course_data(self) -> bool:
        """从飞书表格刷新课程数据"""
        try:
            # 获取表格数据
            resp = self.fs_api.spreadsheet.reading_a_single_range(
                self.config.sheet_token, self.config.sheet_id, "A1:AZ"
            )
            data = resp.get("data", {}).get("valueRange", {}).get("values", [])

            if not data:
                self.logger.warning("未获取到表格数据")
                return False

            # 处理获取索引值
            name_col, day_cols = self._fetch_index(data)

            # 事务处理
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # 清空旧数据
                cursor.execute("DELETE FROM class_schedule")
                cursor.execute(
                    "UPDATE SQLITE_SEQUENCE SET SEQ=0 WHERE NAME='class_schedule'"
                )

                # 插入新数据
                valid_records = 0
                for record in self._process_data_row(data, name_col, day_cols):
                    try:
                        cursor.execute(
                            """
                            INSERT INTO class_schedule 
                            (name, day, class_index, week_range_start, week_range_end)
                            VALUES (?, ?, ?, ?, ?)
                        """,
                            record,
                        )
                        valid_records += 1
                    except sqlite3.IntegrityError as e:
                        self.logger.warning(
                            "跳过重复记录: %s - %s", str(record), str(e)
                        )

                conn.commit()
                self.logger.info("成功更新%d条课程记录", valid_records)
                return True

        except Exception as e:
            self.logger.error("课程数据更新失败: %s", str(e), exc_info=True)
            return False


# 使用示例
if __name__ == "__main__":
    logger.info("开始更新课表")
    try:
        config = CourseConfig()
        fs_api = APIContainer(
            app_id=config.app_id, app_secret=config.app_secret, host=config.lark_host
        )
        manager = CourseManager(config, fs_api)

        if manager.refresh_course_data():
            logger.info("课程表更新成功")
        else:
            logger.error("课程表更新失败")

    except Exception as e:
        logger.critical("服务启动失败: %s", str(e))
        exit(1)

import json
import signal
import sys
from datetime import datetime
from flask import Flask, jsonify, request, send_file

from Component import Component
from Logger import setup_logger
from Database import database_manager

logger = setup_logger("server")

app = Flask(__name__)

USERLIST_PATH = "userlist.json"
WEEK_REFER = [
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
]  # 表格内各日期的表示方式，从周一开始
USERNAME_REFER = "姓名"  # 表格内姓名列的标题
CLASS_NUM_PER_DAY = 5  # 每日上课节数
FIRST_WEEK_DAY = datetime(2025, 2, 24)

# init username_list
username_list = []
with open(USERLIST_PATH, "r", encoding="utf-8") as f:
    userlist = json.load(f)
    username_list = [user["name"] for user in userlist]


def get_onwork_time(date_str):
    with database_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                name,
                start_time,
                end_time,
                (strftime('%s', end_time) - strftime('%s', start_time)) / 3600 AS duration_hours
            FROM attendance
            WHERE DATE(start_time) = ?
            """,
            (date_str,),
        )
    rows = cursor.fetchall()
    result = [{"name": username, "date": {}} for username in username_list]
    for row in rows:
        (
            name,
            start_time,
            end_time,
            duration_hours,
        ) = row
        info = next((info for info in result if info["name"] == name), None)
        if not info:
            continue
        day = "20" + datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").strftime(
            "%y-%m-%d"
        )
        if not info["date"].get(day):
            info["date"][day] = []

        def get_relative_hour(dt_str):
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            return dt.hour + dt.minute / 60 + dt.second / 3600  # 计算小时的小数表示

        info["date"][day].append(
            {"start": get_relative_hour(start_time), "end": get_relative_hour(end_time)}
        )
    return result


def get_onclass_time(date_str):
    def get_weekday_and_week(date):
        delta_days = (date - FIRST_WEEK_DAY).days  # 计算起始日期到今天的天数
        week_number = delta_days // 7 + 1  # 计算是第几周（第 1 周从 start_date 开始）
        weekday = (
            date.weekday() + 1
        )  # `weekday()` 返回 0-6 (周一=0, 周日=6)，调整为 1-7
        return week_number, weekday

    def get_class_relative_hour(class_index):
        class_time_map = {
            1: (8.0, 10.41),  # 8:00 - 10:25
            2: (10.66, 12.25),  # 10:40 - 12:15
            3: (14.0, 15.41),  # 14:00 - 15:25
            4: (15.66, 18.25),  # 15:40 - 18:15
            5: (19.0, 21.0),  # 19:00 - 21:00
        }
        return {
            "start": class_time_map[class_index][0],
            "end": class_time_map[class_index][1],
        }

    with database_manager.get_connection() as conn:
        cursor = conn.cursor()
        date = datetime.strptime(date_str, "%Y-%m-%d")
        week, day = get_weekday_and_week(date)
        cursor.execute(
            """SELECT 
            name,
            class_index
            FROM class_schedule
                    WHERE ? BETWEEN week_range_start AND week_range_end AND day = ?
            """,
            (week, day),
        )
        rows = [
            {
                "name": name,
                "class_index": class_index,
            }
            for name, class_index in cursor.fetchall()
        ]
        result = [{"name": username, "onclass_date": []} for username in username_list]
        for row in rows:
            info = next((info for info in result if info["name"] == row["name"]), None)
            if not info:
                continue
            info["onclass_date"].append(get_class_relative_hour(row["class_index"]))

        return result


@app.route("/get_data")
def get_data():
    date_str = request.args.get("date", "")  # 获取日期参数

    onwork_time = get_onwork_time(date_str)
    onclass_time = get_onclass_time(date_str)

    # 合并结果
    merged_dict = {}
    for item in onwork_time:
        merged_dict[item["name"]] = {"name": item["name"], "date": item["date"]}
    for item in onclass_time:
        if item["name"] in merged_dict:
            merged_dict[item["name"]]["onclass_date"] = item["onclass_date"]
        else:
            merged_dict[item["name"]] = {
                "name": item["name"],
                "onclass_date": item["onclass_date"],
            }
    # 转换回列表
    merged_list = list(merged_dict.values())

    response = jsonify(merged_list)
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response  # 以 JSON 格式返回数据


@app.route("/update_course_schedule")
def update_course_schedule():
    Component("course_schedule.py")
    return "success"


@app.route("/")
def home():
    return send_file("index.html")  # Flask 默认会去 templates/ 目录找文件


if __name__ == "__main__":
    # 绑定信号，确保 Ctrl+C 退出时关闭所有子进程
    signal.signal(signal.SIGINT, lambda s, f: (Component.stop_all(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (Component.stop_all(), sys.exit(0)))
    # Component("attendance.py")

    # init_sqlite()
    # decode_file()
    # update_class_datasheet()
    app.run(debug=False, host="0.0.0.0")

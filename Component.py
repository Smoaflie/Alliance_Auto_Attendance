import subprocess
import os
import sys


class Component:
    components = []  # 记录所有子进程

    def __init__(self, file_path):
        """启动子程序，并保证它以 '__name__ == "__main__"' 方式运行"""
        component_path = os.path.join(os.getcwd(), file_path)  # 确保使用本地路径
        if not os.path.exists(component_path):
            print(f"错误: {component_path} 文件未找到!")
            self.process = None
            return

        # 启动子进程，使用独立的 Python 解释器运行
        self.process = subprocess.Popen(
            [sys.executable, component_path],  # sys.executable 确保使用当前 Python 版本
            stdout=sys.stdout,  # 让子进程的标准输出和主进程一致
            stderr=sys.stderr,
        )
        Component.components.append(self)
        print(f"已启动子程序 {component_path} (PID={self.process.pid})")

    def stop(self):
        """停止当前子进程"""
        if self.process and self.process.poll() is None:  # 仅在进程仍在运行时终止
            print(f"正在关闭 {self.process.pid}...")
            self.process.terminate()  # 发送 SIGTERM 信号
            try:
                self.process.wait(timeout=5)  # 等待最多 5 秒
            except subprocess.TimeoutExpired:
                self.process.kill()  # 强制终止
            print(f"子进程 {self.process.pid} 已关闭.")

    @staticmethod
    def stop_all():
        """停止所有子进程"""
        print("\n检测到退出信号，正在关闭所有子进程...")
        for component in Component.components:
            component.stop()
        print("\n已关闭所有子进程...")
        print("\n关闭主进程...")
        Component.components.clear()

# -*- coding: utf-8 -*-
"""
炉石传说 酒馆战旗 —— 一键断网重连辅助工具
功能：置顶悬浮窗，点击按钮即可通过防火墙规则让炉石断网、再恢复网络、
      最后自动识别并点击"重新连接"按钮，一键完成断网重连。
注意：防火墙操作需要管理员权限，exe 已配置为启动时自动请求 UAC 提权。
"""

import os
import sys
import time
import ctypes
import subprocess
import threading
from ctypes import wintypes

import tkinter as tk
from tkinter import messagebox

import cv2
import numpy as np
import pyautogui
import win32gui
import win32con
import win32process


# ------------------------------------------------------------------
# 资源路径处理（兼容开发环境与 PyInstaller 打包环境）
# ------------------------------------------------------------------
def resource_path(relative_name: str) -> str:
    """
    返回资源文件的绝对路径。
    - 打包环境：sys._MEIPASS 是 PyInstaller 解压临时目录
    - 开发环境：使用脚本所在目录
    """
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_name)


# 模板图文件名（实际使用 cljt.png）
TEMPLATE_NAME = "cljt.png"
TEMPLATE_PATH = resource_path(TEMPLATE_NAME)

# 图像匹配阈值
MATCH_THRESHOLD = 0.8


# ------------------------------------------------------------------
# 窗口查找与激活
# ------------------------------------------------------------------
def find_hearthstone_hwnd() -> int:
    """遍历所有顶层窗口，查找标题包含 '炉石传说' 或 'Hearthstone' 的窗口句柄。"""
    result = []

    def _enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and ("炉石传说" in title or "Hearthstone" in title):
                result.append(hwnd)
        return True

    win32gui.EnumWindows(_enum_handler, None)
    return result[0] if result else 0


def activate_window(hwnd: int) -> bool:
    """恢复并前置指定窗口，成功返回 True。"""
    try:
        # 最小化时先恢复
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # 置于最前
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# 进程路径获取（用于防火墙规则精准定位 Hearthstone.exe）
# ------------------------------------------------------------------
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_QueryFullProcessImageNameW = ctypes.windll.kernel32.QueryFullProcessImageNameW
_QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_QueryFullProcessImageNameW.restype = wintypes.BOOL


def get_process_exe_path(hwnd: int) -> str:
    """通过窗口句柄获取所属进程的可执行文件完整路径。"""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return ""
    h = ctypes.windll.kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


# ------------------------------------------------------------------
# 防火墙规则：断网 / 恢复
# ------------------------------------------------------------------
FIREWALL_RULE_NAME = "Hearthstone_Reconnect_Tool_Block"


def firewall_block(exe_path: str) -> bool:
    """
    添加出站阻止规则，让 Hearthstone.exe 断网。
    需要 admin 权限。成功返回 True。
    """
    if not exe_path or not os.path.exists(exe_path):
        return False
    # 先删掉同名旧规则，避免重复
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule",
         f"name={FIREWALL_RULE_NAME}"],
        shell=True, capture_output=True
    )
    # 添加出站阻止规则（精准定位程序路径）
    r = subprocess.run(
        ["netsh", "advfirewall", "firewall", "add", "rule",
         f"name={FIREWALL_RULE_NAME}", "dir=out", "action=block",
         f"program={exe_path}", "enable=yes"],
        shell=True, capture_output=True
    )
    return r.returncode == 0


def firewall_unblock() -> bool:
    """删除阻止规则，恢复网络。成功返回 True。"""
    r = subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule",
         f"name={FIREWALL_RULE_NAME}"],
        shell=True, capture_output=True
    )
    return r.returncode == 0


# ------------------------------------------------------------------
# 图像匹配
# ------------------------------------------------------------------
def locate_template(template_path: str):
    """
    截屏并在屏幕上匹配模板图。
    返回 (中心坐标 x, y, 最大匹配值)；未加载模板或出错返回 None。
    """
    # 读取模板（灰度）
    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        return None

    # 截屏并转为 OpenCV 的 BGR，再转灰度
    screenshot = pyautogui.screenshot()
    frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # 模板匹配
    res = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

    if max_val >= MATCH_THRESHOLD:
        h, w = template.shape
        top_left = max_loc
        center_x = top_left[0] + w // 2
        center_y = top_left[1] + h // 2
        return center_x, center_y, max_val

    return None


# ------------------------------------------------------------------
# 主界面
# ------------------------------------------------------------------
class ReconnectTool:
    def __init__(self, root: tk.Tk):
        self.root = root
        # 检查模板图是否存在
        self.template_ok = os.path.exists(TEMPLATE_PATH)

        # ---------- 窗口基本属性 ----------
        # 无边框置顶悬浮窗
        root.overrideredirect(True)            # 去除标题栏边框
        root.attributes("-topmost", True)      # 始终置顶
        root.attributes("-alpha", 0.95)        # 轻微透明，更好看
        root.geometry("180x115+30+30")         # 尺寸（加高以容纳退出按钮） + 初始位置
        root.configure(bg="#2b2b2b")

        # 拖拽所需变量
        self._drag_x = 0
        self._drag_y = 0

        # ---------- 控件 ----------
        # 标题（双击关闭）
        title_lbl = tk.Label(
            root, text="炉石重连助手",
            fg="#dddddd", bg="#2b2b2b",
            font=("Microsoft YaHei", 9, "bold")
        )
        title_lbl.pack(fill="x", padx=4, pady=(4, 2))

        # 重连按钮
        self.btn = tk.Button(
            root, text="一键断网重连",
            command=self.on_reconnect,
            font=("Microsoft YaHei", 10, "bold"),
            bg="#1e90ff", fg="white",
            activebackground="#1873cc", activeforeground="white",
            relief="flat", cursor="hand2"
        )
        self.btn.pack(fill="x", padx=8, pady=(2, 0))

        # 退出按钮
        self.quit_btn = tk.Button(
            root, text="退出",
            command=self.on_quit,
            font=("Microsoft YaHei", 9),
            bg="#555555", fg="white",
            activebackground="#777777", activeforeground="white",
            relief="flat", cursor="hand2"
        )
        self.quit_btn.pack(fill="x", padx=8, pady=(2, 2))

        # 状态栏
        self.status_var = tk.StringVar()
        if self.template_ok:
            self.status_var.set("就绪")
        else:
            self.status_var.set(f"缺少模板图 {TEMPLATE_NAME}")
        self.status_lbl = tk.Label(
            root, textvariable=self.status_var,
            fg="#ffd700", bg="#2b2b2b",
            font=("Microsoft YaHei", 8)
        )
        self.status_lbl.pack(fill="x", padx=4, pady=(0, 4))

        # 模板缺失则禁用按钮
        if not self.template_ok:
            self.btn.config(state=tk.DISABLED, bg="#555555")

        # ---------- 拖拽事件绑定 ----------
        for w in (root, title_lbl):
            w.bind("<ButtonPress-1>", self._on_drag_start)
            w.bind("<B1-Motion>", self._on_drag_motion)
        # 双击标题关闭
        title_lbl.bind("<Double-Button-1>", lambda e: root.destroy())

    # ---------------- 拖拽 ----------------
    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    # ---------------- 状态 ----------------
    def set_status(self, text: str):
        self.status_var.set(text)
        self.root.update_idletasks()

    # ---------------- 退出 ----------------
    def on_quit(self):
        """退出本程序。"""
        self.root.destroy()

    # ---------------- 重连主流程 ----------------
    def on_reconnect(self):
        # 在子线程执行，避免阻塞主循环
        self.btn.config(state=tk.DISABLED, bg="#555555")
        threading.Thread(target=self._reconnect_worker, daemon=True).start()

    def _reconnect_worker(self):
        try:
            # 步骤一：查找并激活炉石窗口
            self.set_status("正在查找游戏窗口...")
            hwnd = find_hearthstone_hwnd()
            if not hwnd:
                self.set_status("未找到游戏窗口")
                messagebox.showinfo("提示", "未找到炉石传说窗口，请确认游戏已运行。")
                return

            self.set_status("正在激活窗口...")
            activate_window(hwnd)
            time.sleep(0.5)

            # 步骤二：获取 Hearthstone.exe 路径并断网
            self.set_status("正在获取游戏路径...")
            exe_path = get_process_exe_path(hwnd)
            if not exe_path:
                self.set_status("获取游戏路径失败")
                messagebox.showinfo("提示", "无法获取炉石进程路径，断网失败。")
                return

            self.set_status("正在断网...")
            if not firewall_block(exe_path):
                self.set_status("断网失败(需管理员权限)")
                messagebox.showinfo(
                    "提示",
                    "断网失败。请以管理员身份运行本程序（防火墙操作需要管理员权限）。"
                )
                return

            # 步骤三：等待炉石检测到断线
            self.set_status("等待炉石掉线...")
            time.sleep(4)

            # 步骤四：恢复网络
            self.set_status("正在恢复网络...")
            firewall_unblock()
            time.sleep(1)

            # 步骤五：识别并点击"重新连接"按钮
            self.set_status("正在识别重连按钮...")
            # 重连界面出现需要一点时间，做几次重试
            result = None
            for attempt in range(6):
                result = locate_template(TEMPLATE_PATH)
                if result:
                    break
                self.set_status(f"等待重连按钮出现...({attempt + 1}/6)")
                time.sleep(1)

            if result is None:
                if not os.path.exists(TEMPLATE_PATH):
                    self.set_status(f"缺少模板图 {TEMPLATE_NAME}")
                    messagebox.showinfo("提示", f"模板图 {TEMPLATE_NAME} 不存在，无法识别。")
                else:
                    self.set_status("未找到重连按钮")
                    messagebox.showinfo(
                        "提示",
                        "未检测到重连按钮。\n"
                        "可能原因：模板图不准确、界面未刷新、或断网时间不足。\n"
                        "可手动点击游戏内重连按钮。"
                    )
                return

            center_x, center_y, max_val = result

            # 步骤六：执行点击
            self.set_status("正在点击重连按钮...")
            pyautogui.moveTo(center_x, center_y, duration=0.15)
            pyautogui.click(center_x, center_y)
            self.set_status("断网重连完成 ✓")

        except Exception as e:
            # 出错时务必恢复网络，避免游戏一直断网
            try:
                firewall_unblock()
            except Exception:
                pass
            self.set_status(f"错误：{e}")
            messagebox.showinfo("错误", f"重连过程出现异常：\n{e}")
        finally:
            # 确保网络已恢复（兜底）
            try:
                firewall_unblock()
            except Exception:
                pass
            # 恢复按钮
            if self.template_ok:
                self.btn.config(state=tk.NORMAL, bg="#1e90ff")
            else:
                self.btn.config(state=tk.DISABLED, bg="#555555")


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------
def main():
    # pyautogui 安全设置：每次操作间隔
    pyautogui.PAUSE = 0.2
    pyautogui.FAILSAFE = True

    root = tk.Tk()
    app = ReconnectTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()

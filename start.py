# start.py
"""启动器：依赖检查、端口管理、仅终止本应用相关进程。"""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
import time

APP_MARKER = "visual_interface.py"
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app.pid")
DEFAULT_PORT = 8501


def check_python_version() -> None:
    print("正在检查 Python 环境...")
    if sys.version_info < (3, 8):
        print("错误: 需要 Python 3.8 或更高版本。")
        print(f"当前版本: {sys.version}")
        sys.exit(1)
    print("Python 版本符合要求。")


def check_and_install_dependencies(auto_yes: bool = False) -> None:
    required_packages = ["streamlit", "requests", "psutil"]
    missing = [p for p in required_packages if importlib.util.find_spec(p) is None]
    print("正在检查依赖库...")
    if not missing:
        print("所有依赖库已安装。")
        return

    print(f"发现缺少依赖: {', '.join(missing)}")
    if not auto_yes:
        choice = input("是否立即安装这些依赖？(y/n): ").strip().lower()
        if choice != "y":
            print("无法继续运行，请安装依赖后重试。")
            sys.exit(1)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        print("依赖安装成功。")
    except subprocess.CalledProcessError:
        print("依赖安装失败，请手动安装后重试。")
        sys.exit(1)


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _proc_cmdline_blob(proc) -> str:
    try:
        parts = proc.cmdline()
    except Exception:
        parts = []
    try:
        parts = parts + ([proc.exe()] if proc.exe() else [])
    except Exception:
        pass
    return " ".join(parts).lower()


def _is_related_process(proc) -> bool:
    """只认本项目的 streamlit / visual_interface 进程，避免误杀其它服务。"""
    try:
        name = (proc.name() or "").lower()
    except Exception:
        return False
    if name not in {"python.exe", "pythonw.exe", "python", "streamlit"}:
        return False
    blob = _proc_cmdline_blob(proc)
    if APP_MARKER.lower() in blob:
        return True
    if "streamlit" in blob and ("visual_interface" in blob or os.path.basename(sys.argv[0]).lower() in blob):
        return True
    # 本启动器写出的 PID
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            saved = int(f.read().strip() or "0")
        if saved and proc.pid == saved:
            return True
    except Exception:
        pass
    return False


def find_related_pids(port: int) -> list[int]:
    import psutil

    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if not _is_related_process(proc):
                continue
            for conn in proc.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port:
                    pids.append(proc.pid)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    # 去重且保序
    seen = set()
    ordered = []
    for p in pids:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def kill_related_on_port(port: int, force: bool = False) -> bool:
    import psutil

    pids = find_related_pids(port)
    if not pids:
        # 兜底：读本机记录的 PID
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                saved = int(f.read().strip() or "0")
            if saved:
                pids = [saved]
        except Exception:
            pids = []
    if not pids:
        print(f"未找到与本应用相关的进程占用端口 {port}。")
        return False

    killed = False
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            print(f"正在结束本应用进程: {proc.name()} (PID: {pid})")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                if force:
                    print(f"进程 {pid} 未响应，强制结束...")
                    proc.kill()
                else:
                    print(f"进程 {pid} 未在 3s 内退出。")
                    continue
            killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            print(f"结束 PID {pid} 失败: {e}")
    if killed and os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    return killed


def start_app(port: int, write_pid: bool = True) -> None:
    print(f"\n正在启动应用 (端口 {port})...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(current_dir, "visual_interface.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        app_path,
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
    ]
    try:
        proc = subprocess.Popen(cmd, cwd=current_dir)
        if write_pid:
            try:
                with open(PID_FILE, "w", encoding="utf-8") as f:
                    f.write(str(proc.pid))
            except OSError:
                pass
        print(f"浏览器地址: http://127.0.0.1:{port}  (PID {proc.pid})")
        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\n正在停止应用...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("应用已停止。")
        finally:
            if os.path.exists(PID_FILE):
                try:
                    os.remove(PID_FILE)
                except OSError:
                    pass
    except OSError as e:
        print(f"启动失败: {e}")
        sys.exit(1)


def _prompt_choice(options: list[str], valid: set[str]) -> str:
    print("请选择操作:")
    for item in options:
        print(f"  {item}")
    choice = input(f"请输入选项 ({'/'.join(sorted(valid))}): ").strip()
    while choice not in valid:
        choice = input("无效输入，请重试: ").strip()
    return choice


def main() -> None:
    parser = argparse.ArgumentParser(description="规培手写门诊病历自动排版 - 启动程序")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="服务端口")
    parser.add_argument("--yes", "-y", action="store_true", help="非交互：自动安装缺依赖")
    parser.add_argument("--restart", action="store_true", help="若端口占用则重启本应用")
    parser.add_argument("--stop", action="store_true", help="仅停止本应用")
    args = parser.parse_args()

    print("=" * 40)
    print("    规培手写门诊病历自动排版 - 启动程序")
    print("=" * 40)

    check_python_version()
    check_and_install_dependencies(auto_yes=args.yes)

    port = args.port
    port_busy = is_port_in_use(port)

    if args.stop:
        ok = kill_related_on_port(port)
        print("应用已停止。" if ok else "未找到可停止的本应用进程。")
        sys.exit(0 if ok else 1)

    if port_busy:
        print(f"\n检测到端口 {port} 已被占用。")
        related = find_related_pids(port)
        if related:
            print(f"疑似本应用 PID: {related}")
        else:
            print("占用进程与本应用无关，不会自动终止。请换端口或手动处理。")

        if args.restart:
            if not related:
                print(f"可尝试: python start.py --port {port + 1}")
                sys.exit(1)
            print("正在停止旧进程...")
            kill_related_on_port(port)
            time.sleep(2)
            start_app(port)
            return

        choice = _prompt_choice(
            ["[1] 重启本应用 (Restart)", "[2] 停止本应用 (Stop)", "[3] 换端口启动", "[4] 退出 (Exit)"],
            {"1", "2", "3", "4"},
        )
        if choice == "1":
            if related:
                print("正在停止旧进程...")
                kill_related_on_port(port)
                time.sleep(2)
                start_app(port)
            else:
                print("端口被其它程序占用，已拒绝重启，避免误杀。")
                alt = find_next_free_port(port)
                print(f"可改用端口 {alt} 启动。")
                sys.exit(1)
        elif choice == "2":
            ok = kill_related_on_port(port)
            print("应用已停止。" if ok else "未找到可停止的本应用进程。")
            sys.exit(0)
        elif choice == "3":
            alt = find_next_free_port(port)
            print(f"改用空闲端口 {alt}")
            start_app(alt)
        else:
            print("已退出。")
            sys.exit(0)
    else:
        print(f"\n端口 {port} 空闲。")
        if args.yes:
            start_app(port)
            return
        choice = _prompt_choice(["[1] 启动应用 (Start)", "[2] 退出 (Exit)"], {"1", "2"})
        if choice == "1":
            start_app(port)
        else:
            print("已退出。")
            sys.exit(0)


def find_next_free_port(start_port: int) -> int:
    port = start_port
    while is_port_in_use(port) and port < start_port + 50:
        port += 1
    return port


if __name__ == "__main__":
    main()

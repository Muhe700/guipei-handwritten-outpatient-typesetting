import sys
import subprocess
import os
import time
import socket
import importlib.util

def check_python_version():
    """检查 Python 版本"""
    print("正在检查 Python 环境...")
    if sys.version_info < (3, 8):
        print("错误: 需要 Python 3.8 或更高版本。")
        print(f"当前版本: {sys.version}")
        sys.exit(1)
    print("Python 版本符合要求。")

def check_and_install_dependencies():
    """检查并安装依赖"""
    required_packages = ['streamlit', 'requests', 'psutil']
    missing_packages = []

    print("正在检查依赖库...")
    for package in required_packages:
        if importlib.util.find_spec(package) is None:
            missing_packages.append(package)

    if missing_packages:
        print(f"发现缺少依赖: {', '.join(missing_packages)}")
        choice = input("是否立即安装这些依赖？(y/n): ").strip().lower()
        if choice == 'y':
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
                print("依赖安装成功。")
            except subprocess.CalledProcessError:
                print("依赖安装失败，请手动安装后重试。")
                sys.exit(1)
        else:
            print("无法继续运行，请安装依赖后重试。")
            sys.exit(1)
    else:
        print("所有依赖库已安装。")

def is_port_in_use(port):
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def find_available_port(start_port):
    """寻找可用端口"""
    port = start_port
    while is_port_in_use(port):
        port += 1
    return port

def kill_process_on_port(port):
    """结束占用端口的进程"""
    import psutil
    killed = False
    for proc in psutil.process_iter(['pid', 'name', 'connections']):
        try:
            for conn in proc.connections(kind='inet'):
                if conn.laddr.port == port:
                    print(f"正在结束进程: {proc.info['name']} (PID: {proc.info['pid']})")
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        print(f"进程 {proc.info['pid']} 未响应，强制结束...")
                        proc.kill()
                    killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return killed

def start_app(port):
    """启动应用"""
    print(f"\n正在启动应用 (端口 {port})...")
    # Use absolute path to ensure reliability regardless of CWD
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(current_dir, "visual_interface.py")
    
    cmd = [sys.executable, "-m", "streamlit", "run", app_path, "--server.port", str(port)]
    
    try:
        # Use subprocess.run but don't capture output to let it stream to console
        # Check return code is not needed as streamlit runs indefinitely usually
        subprocess.run(cmd, cwd=current_dir)
    except KeyboardInterrupt:
        print("\n应用已停止。")

def main():
    print("="*40)
    print("    门诊病历处理工具 - 启动程序")
    print("="*40)

    # 1. 环境检查
    check_python_version()
    
    # 2. 依赖检查
    check_and_install_dependencies()

    # 3. 端口检查与菜单
    target_port = 8501
    port_busy = is_port_in_use(target_port)

    if port_busy:
        print(f"\n检测到端口 {target_port} 已被占用 (应用可能正在运行)。")
        print("请选择操作:")
        print("  [1] 重启应用 (Restart)")
        print("  [2] 停止应用 (Stop)")
        print("  [3] 退出 (Exit)")
        
        choice = input("请输入选项 (1/2/3): ").strip()
        
        if choice == '1':
            print("正在停止旧进程...")
            kill_process_on_port(target_port)
            time.sleep(2) # 等待释放
            start_app(target_port)
        elif choice == '2':
            if kill_process_on_port(target_port):
                print("应用已停止。")
            else:
                print("停止失败或进程已不存在。")
            sys.exit(0)
        else:
            print("已退出。")
            sys.exit(0)
            
    else:
        print(f"\n端口 {target_port} 空闲。")
        print("请选择操作:")
        print("  [1] 启动应用 (Start)")
        print("  [2] 退出 (Exit)")
        
        choice = input("请输入选项 (1/2): ").strip()
        
        if choice == '1':
            start_app(target_port)
        else:
            print("已退出。")
            sys.exit(0)

if __name__ == "__main__":
    main()

import platform
import subprocess
import sys
from pathlib import Path


def resolve_python_executable():
    workspace_root = Path(__file__).resolve().parent
    if platform.system() == "Windows":
        venv_python = workspace_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = workspace_root / ".venv" / "bin" / "python"

    if venv_python.exists():
        return str(venv_python)

    return sys.executable

current_os = platform.system()
python_executable = resolve_python_executable()

if current_os == "Windows":
    print("Windows Detected")
    subprocess.run([python_executable, "win_script.py"])
elif current_os == "Linux":
    print("Linux Detected")
    subprocess.run([python_executable, "linux_script.py"])
elif current_os == "Darwin":
    print("macOS Detected")
    subprocess.run([python_executable, "mac_script.py"])

else:
    print(f"Can't Find supported OS: {current_os}")
    input("Run Anyways? (Y/N): ")
    if input().lower() == "y":
        print("Running Script Anyways")
        input("L for linux, W for windows, M for mac: ")
        os_choice = input().lower()
        if os_choice == "l":
            subprocess.run([python_executable, "linux_script.py"])
        elif os_choice == "w":
            subprocess.run([python_executable, "win_script.py"])
        elif os_choice == "m":
            subprocess.run([python_executable, "mac_script.py"])
        else:
            print("Invalid Choice, Exiting")
exit()

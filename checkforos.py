import platform
import subprocess

current_os = platform.system()

if current_os == "Windows":
    print("Windows Detected")
    subprocess.run(["python", "win_script.py"])
elif current_os == "Linux":
    print("Linux Detected")
    subprocess.run(["python", "linux_script.py"])
elif current_os == "Darwin":
    print("macOS Detected")
    subprocess.run(["python", "mac_script.py"])

else:
    print(f"Can't Find supported OS: {current_os}")
    input("Run Anyways? (Y/N): ")
    if input().lower() == "y":
        print("Running Script Anyways")
        input("L for linux, W for windows, M for mac: ")
        os_choice = input().lower()
        if os_choice == "l":
            subprocess.run(["python", "linux_script.py"])
        elif os_choice == "w":
            subprocess.run(["python", "win_script.py"])
        elif os_choice == "m":
            subprocess.run(["python", "mac_script.py"])
        else:
            print("Invalid Choice, Exiting")
exit()

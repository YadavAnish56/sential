import os
with open("test_cam22_anpr_val.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "mediamtx_proc = subprocess.Popen" in line:
        new_lines.append("    mediamtx_proc = None\n")
    elif "if mediamtx_proc.poll() is not None:" in line:
        new_lines.append("    if False:\n")
    elif "mediamtx_proc.terminate()" in line:
        new_lines.append("        pass\n")
    else:
        new_lines.append(line)

with open("test_cam22_manual.py", "w") as f:
    f.writelines(new_lines)

os.system(".\\venv\\Scripts\\python.exe test_cam22_manual.py")

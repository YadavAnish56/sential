import os
with open("test_cam22_anpr_val.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "Step 2: Start MediaMTX" in line:
        skip = True
        new_lines.append(line)
        new_lines.append("    mediamtx_proc = None\n")
        continue
    if skip and "ffmpeg_proc = None" in line:
        skip = False
    
    if not skip:
        if "if mediamtx_proc.poll() is not None:" in line:
            new_lines.append("        if False:\n")
        elif "mediamtx_proc.terminate()" in line:
            pass
        elif "mediamtx_proc.wait" in line:
            pass
        elif "mediamtx_proc.kill" in line:
            pass
        else:
            new_lines.append(line)

with open("test_cam22_manual2.py", "w") as f:
    f.writelines(new_lines)

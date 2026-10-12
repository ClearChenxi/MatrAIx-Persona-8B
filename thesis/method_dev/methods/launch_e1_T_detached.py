"""Detached launcher for E1a+E1b T predictions."""
import os
import sys
import time
from pathlib import Path

ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
LOG = ROOT / "thesis/method_dev/methods/e1_T.log"
VENV_PY = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "thesis/method_dev/methods/run_e1_T_predictions.py"

pid = os.fork()
if pid > 0:
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    print(f"first fork pid={pid} alive={alive}")
    sys.exit(0)

os.setsid()
pid2 = os.fork()
if pid2 > 0:
    sys.exit(0)

LOG.parent.mkdir(parents=True, exist_ok=True)
logf = open(LOG, "ab", 0)
os.dup2(logf.fileno(), 1)
os.dup2(logf.fileno(), 2)
os.chdir(str(ROOT))

pid_path = ROOT / "thesis/method_dev/methods/e1_T.pid"
pid_path.write_text(str(os.getpid()))

cmd = [str(VENV_PY), str(SCRIPT)]
print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] daemon grandchild pid={os.getpid()} starting", flush=True)
print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] cmd: {' '.join(cmd)}", flush=True)
try:
    os.execv(cmd[0], cmd)
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] execv failed: {e}", flush=True)
    sys.exit(1)

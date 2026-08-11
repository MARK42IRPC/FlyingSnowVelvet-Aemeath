"""Launch the optional Qt workbench in a separate process."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def launch_workbench_helper() -> bool:
    entry = Path(__file__).resolve().parents[2] / 'core' / 'qt_desktop_pet.py'
    command = [sys.executable, str(entry), '--fsv-workbench-helper']
    kwargs = {
        'cwd': str(entry.parents[2]),
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'close_fds': True,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            | getattr(subprocess, 'DETACHED_PROCESS', 0)
        )
    else:
        kwargs['start_new_session'] = True
    try:
        subprocess.Popen(command, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return False
    return True

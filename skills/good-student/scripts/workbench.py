"""Skill entry point sharing the existing explicit project/runtime selection."""
from pathlib import Path
import runpy
import sys

if __name__ == '__main__':
    # student.py performs runtime selection before routing this entry point.
    sys.argv.insert(1, '--workbench-entry')
    runpy.run_path(str(Path(__file__).with_name('student.py')), run_name='__main__')

"""Prepare the optional UI dependencies in the project-local runtime."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    module = runpy.run_path(str(Path(__file__).with_name('setup_media.py')))
    module['main'].__globals__['PACKAGES'] = ['fastapi>=0.115,<1', 'uvicorn>=0.30,<1']
    raise SystemExit(module['main']())

"""Entry point para Vercel serverless Python.

Vercel ejecuta cada request como una función serverless. Exportamos el
`app` de FastAPI; Vercel lo envuelve con el handler ASGI apropiado.
El scheduler NO corre aquí (serverless no es lugar para cron).
"""
import os
import sys
from pathlib import Path

# Asegurar que la raíz del backend esté en el path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Forzar scheduler OFF en serverless
os.environ.setdefault("ENABLE_SCHEDULER", "false")

from main import app  # noqa: E402

# Vercel Python runtime detecta el ASGI app vía `app`

#!/usr/bin/env bash
# Arranca el descargador dentro del Codespace (o de cualquier contenedor de
# desarrollo), si no está ya en marcha.
#
# Escucha en 0.0.0.0 y no en 127.0.0.1 como start.sh: el reenvío de puertos de
# Codespaces solo alcanza lo que se anuncia fuera del contenedor.
#
# Esto es un entorno de PRUEBAS. Para producción, ver DESPLIEGUE.md.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if pgrep -f "uvicorn app.main" >/dev/null 2>&1; then
  echo "El servidor ya está en marcha en el puerto 8000."
  exit 0
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creando entorno virtual e instalando dependencias..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r backend/requirements.txt
fi

echo "Arrancando el descargador..."
echo "Cuando aparezca el aviso del puerto 8000, ábrelo con el botón «Open in Browser»."
echo "Para que lo vea cualquiera (o tu móvil sin sesión de GitHub): pestaña PORTS,"
echo "clic derecho en 8000 → Port Visibility → Public."

cd backend
exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

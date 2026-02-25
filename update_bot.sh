#!/bin/bash

# Script para actualizar el código del bot en el VPS

# Ruta de la instalación
BOT_DIR="/root/botFinalTlg"
SERVICE_NAME="botFinalTlg.service"

echo "=== Iniciando actualización del Bot de Telegram ==="

# Ir al directorio del bot
cd $BOT_DIR || { echo "❌ Error: No se encontró el directorio $BOT_DIR"; exit 1; }

# Descargar los últimos cambios
echo "📥 Descargando actualizaciones desde GitHub..."
git pull origin main

# Instalar/Actualizar dependencias si es necesario
echo "📦 Verificando dependencias..."
source venv/bin/activate
pip install -r requirements.txt

# Reiniciar el servicio systemd
echo "🔄 Reiniciando el servicio $SERVICE_NAME..."
sudo systemctl restart $SERVICE_NAME

# Comprobar el estado
echo "✅ Estado del servicio post-actualización:"
sudo systemctl status $SERVICE_NAME --no-pager

echo "=== Actualización completada con éxito ==="

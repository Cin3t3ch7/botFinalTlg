#!/bin/bash

# Nombre del script de ejecución del bot
BOT_SCRIPT="main.py"

# Directorio donde se encuentra el script del bot
BOT_DIR="/root/botFinalTlg"

# Nombre del servicio systemd
SERVICE_NAME="botFinalTlg"

# Crear archivo de servicio systemd
create_systemd_service() {
    echo "Creando servicio systemd para el bot de Telegram..."
    cat > /etc/systemd/system/${SERVICE_NAME}.service << EOL
[Unit]
Description=Telegram Bot Service
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=${BOT_DIR}
Environment="PATH=${BOT_DIR}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=${BOT_DIR}/venv/bin/python3 ${BOT_DIR}/${BOT_SCRIPT}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL
}

# Instalar dependencias
install_dependencies() {
    echo "Instalando dependencias..."
    source ${BOT_DIR}/venv/bin/activate
    pip install -r ${BOT_DIR}/requirements.txt
}

# Configurar permisos y servicio
setup_service() {
    # Crear el servicio systemd
    create_systemd_service

    # Recargar systemd
    systemctl daemon-reload

    # Habilitar el servicio para que inicie en el arranque
    systemctl enable ${SERVICE_NAME}

    # Iniciar el servicio
    systemctl start ${SERVICE_NAME}
}

# Menú de opciones
main() {
    echo "Script de gestión del Bot de Telegram"
    echo "====================================="
    
    case "$1" in
        install)
            install_dependencies
            setup_service
            echo "Bot instalado y configurado como servicio systemd"
            ;;
        start)
            systemctl start ${SERVICE_NAME}
            echo "Bot iniciado"
            ;;
        stop)
            systemctl stop ${SERVICE_NAME}
            echo "Bot detenido"
            ;;
        restart)
            systemctl restart ${SERVICE_NAME}
            echo "Bot reiniciado"
            ;;
        status)
            systemctl status ${SERVICE_NAME}
            ;;
        logs)
            journalctl -u ${SERVICE_NAME}
            ;;
        *)
            echo "Uso: $0 {install|start|stop|restart|status|logs}"
            exit 1
    esac
}

# Ejecutar la función principal con los argumentos pasados
main "$@"

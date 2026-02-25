import asyncio
import logging
import sys
import os
import time
import signal
import gc
import platform
from datetime import datetime
import psutil

from database.connection import init_db, close_all_connections
from database.models import setup_super_admin, setup_default_services

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Reducir logging de las bibliotecas externas
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Sistema de bloqueo para asegurar una sola instancia por token
def check_lock_file(token):
    """Verifica si ya hay una instancia en ejecución para este token"""
    # Crear directorio si no existe
    os.makedirs("locks", exist_ok=True)
    
    lock_file = f"locks/bot_{token[:10]}.lock"
    
    # Verificar si el archivo existe
    if os.path.exists(lock_file):
        # Leer el PID del archivo
        try:
            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Verificar si el proceso sigue en ejecución
            if psutil.pid_exists(pid):
                # Verificar si es realmente un proceso del bot
                try:
                    process = psutil.Process(pid)
                    cmdline = process.cmdline()
                    if len(cmdline) >= 3 and 'run_single_bot.py' in cmdline[1] and token in cmdline[2]:
                        logger.error(f"Ya hay una instancia en ejecución para el token {token[:10]} (PID: {pid})")
                        return False
                    else:
                        # El archivo lock pertenece a otro proceso, eliminarlo
                        logger.warning(f"Eliminando lock file obsoleto para {token[:10]}")
                        os.remove(lock_file)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # El proceso ya no existe o no se puede acceder
                    os.remove(lock_file)
            else:
                # El proceso ya no existe, eliminar archivo
                os.remove(lock_file)
        except Exception as e:
            logger.error(f"Error al verificar lock file: {e}")
            try:
                os.remove(lock_file)
            except:
                pass
    
    # Crear archivo de bloqueo con el PID actual
    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
        logger.info(f"Lock file creado: {lock_file}")
    except Exception as e:
        logger.error(f"Error al crear lock file: {e}")
        return False
    
    return True

async def main():
    """Función principal para iniciar un solo bot"""
    global token
    
    # Verificar argumentos
    if len(sys.argv) != 2:
        print("Uso: python run_single_bot.py <token>")
        sys.exit(1)
        
    token = sys.argv[1]
    
    # Verificar si ya hay una instancia en ejecución para este token
    if not check_lock_file(token):
        logger.error(f"Ya existe una instancia del bot para el token {token[:10]}. Saliendo...")
        sys.exit(1)
    
    try:
        # Inicializar base de datos
        if not init_db():
            logger.error("Error al inicializar la base de datos. Revisa la configuración y los logs.")
            return
            
        logger.info(f"Base de datos inicializada para bot con token: {token[:10]}...")
        
        # Configurar super admin y servicios predeterminados
        setup_super_admin(token)
        setup_default_services(token)
        
        # Importar solo después de la inicialización de la BD para evitar dependencias circulares
        from botNew import EmailBot
        
        # Crear y configurar el bot
        bot = EmailBot()
        bot.token = token  # Asignar el token al bot
        
        # Configurar el bot y obtener la aplicación
        app = bot.setup()
        
        # Mensaje de inicio
        logger.info(f"Bot con token {token[:10]} iniciando...")
        
        # Iniciar el polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        logger.info(f"Bot con token {token[:10]} iniciado correctamente")
        
        # Mantener el bot en ejecución
        while True:
            await asyncio.sleep(10)
            
    except (KeyboardInterrupt, SystemExit):
        logger.info(f"Señal de interrupción recibida. Deteniendo bot con token: {token[:10]}...")
    except Exception as e:
        logger.error(f"Error crítico en ejecución del bot con token {token[:10]}: {e}", exc_info=True)
    finally:
        # Cerrar todo de manera ordenada
        logger.info("Realizando limpieza final...")
        
        # Eliminar el archivo de bloqueo
        lock_file = f"locks/bot_{token[:10]}.lock"
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info(f"Lock file eliminado: {lock_file}")
            except Exception as e:
                logger.error(f"Error al eliminar lock file: {e}")
        
        # Detener el bot si está activo
        if 'app' in locals():
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.error(f"Error al detener el bot: {e}")
        
        # Limpiar conexiones a la base de datos
        try:
            close_all_connections()
        except Exception as e:
            logger.error(f"Error al cerrar conexiones de base de datos: {e}")
        
        logger.info(f"Bot con token {token[:10]} detenido correctamente")
        
        # Asegurar que el proceso termine correctamente
        sys.exit(0)

if __name__ == "__main__":
    # Crear directorios necesarios
    os.makedirs("logs", exist_ok=True)
    os.makedirs("locks", exist_ok=True)
    
    # Ejecutar el bot
    asyncio.run(main())
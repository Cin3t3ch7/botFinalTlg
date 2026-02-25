import logging
import sys
import subprocess
import time
import os
from config import BOT_TOKENS
from database.connection import init_db
from database.models import ensure_roles_exist

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

def main():
    """Función principal para iniciar todos los bots como procesos independientes"""
    try:
        # Realizar verificaciones iniciales de la base de datos
        init_db()
        logger.info("Base de datos inicializada")
        
        if not ensure_roles_exist():
            logger.error("Error en la verificación de roles. Revisar la configuración de la base de datos.")
            return
        
        # Verificar tokens
        valid_tokens = [token.strip() for token in BOT_TOKENS if token.strip()]
        if not valid_tokens:
            logger.error("No se encontraron tokens válidos. Revisa la configuración BOT_TOKENS.")
            return
        
        logger.info(f"Iniciando {len(valid_tokens)} bots como procesos independientes...")
        
        # Crear directorio para logs si no existe
        logs_dir = "logs"
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        
        # Iniciar un proceso independiente para cada bot
        processes = []
        for token in valid_tokens:
            try:
                # Crear un archivo de log para cada bot
                log_filename = os.path.join(logs_dir, f"bot_{token[:10]}.log")
                
                # Iniciar el proceso con redirección de salida a un archivo de log
                with open(log_filename, 'a') as log_file:
                    process = subprocess.Popen(
                        [sys.executable, "run_single_bot.py", token],
                        stdout=log_file,
                        stderr=log_file,
                        stdin=subprocess.DEVNULL
                    )
                
                processes.append((process, token, log_filename))
                logger.info(f"Proceso iniciado para bot con token: {token[:10]}...")
                
                # Pequeña pausa para evitar sobrecarga
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error al iniciar bot con token {token[:10]}: {e}")
        
        if not processes:
            logger.error("No se pudo iniciar ningún proceso de bot. Verificar tokens y conexión.")
            return
            
        logger.info(f"Se iniciaron {len(processes)} procesos de bot correctamente")
        
        # Monitorear procesos y reiniciar los que fallen
        try:
            while True:
                for i, (process, token, log_filename) in enumerate(processes[:]):
                    # Verificar si el proceso ha terminado
                    if process.poll() is not None:
                        exit_code = process.returncode
                        logger.warning(f"El proceso del bot con token {token[:10]} ha terminado con código {exit_code}. Reiniciando...")
                        
                        # Reiniciar proceso
                        with open(log_filename, 'a') as log_file:
                            log_file.write(f"\n\n--- REINICIO DEL BOT {token[:10]} - {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n\n")
                            new_process = subprocess.Popen(
                                [sys.executable, "run_single_bot.py", token],
                                stdout=log_file,
                                stderr=log_file,
                                stdin=subprocess.DEVNULL
                            )
                        
                        # Reemplazar proceso en la lista
                        processes[i] = (new_process, token, log_filename)
                        logger.info(f"Proceso reiniciado para bot con token: {token[:10]}...")
                
                time.sleep(10)  # Revisar cada 10 segundos
                
        except (KeyboardInterrupt, SystemExit):
            logger.info("Terminando todos los procesos de bot...")
            for process, token, _ in processes:
                if process.poll() is None:  # Si el proceso sigue en ejecución
                    logger.info(f"Terminando proceso del bot con token: {token[:10]}...")
                    process.terminate()
                    # Esperar a que termine
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"El proceso del bot con token {token[:10]} no respondió. Forzando terminación...")
                        process.kill()
            
            logger.info("Todos los procesos de bot han sido terminados")
    
    except Exception as e:
        logger.error(f"Error en la ejecución principal: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
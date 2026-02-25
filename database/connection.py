import psycopg2
from psycopg2 import pool
import logging
from config import DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME

# Configurar logging
logger = logging.getLogger(__name__)

# Crear un pool de conexiones para mejorar el rendimiento con múltiples bots
connection_pool = None

def init_db():
    """Inicializa el pool de conexiones a la base de datos"""
    global connection_pool
    try:
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            1, 20,  # min_conn, max_conn
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME
        )
        logger.info("Pool de conexiones inicializado")
        
        # Verificar la conexión
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        release_connection(conn)
        logger.info("Conexión a la base de datos establecida exitosamente")
        
        # Inicializar las tablas
        from database.models import init_db as init_tables
        init_tables()
        
        return True
    except Exception as e:
        logger.error(f"Error al inicializar la base de datos: {e}")
        return False

def get_connection():
    """Obtiene una conexión del pool"""
    if connection_pool is None:
        raise Exception("El pool de conexiones no ha sido inicializado. Llama a init_db() primero.")
    return connection_pool.getconn()

def release_connection(conn):
    """Devuelve una conexión al pool"""
    if connection_pool is not None:
        connection_pool.putconn(conn)

def close_all_connections():
    """Cierra todas las conexiones activas y reinicia el pool"""
    global connection_pool
    
    try:
        if connection_pool:
            connection_pool.closeall()
            logger.info("Pool de conexiones cerrado")
    except Exception as e:
        logger.error(f"Error al cerrar el pool de conexiones: {e}")
    
    # Recrear el pool
    try:
        init_db()
    except Exception as e:
        logger.error(f"Error al recrear el pool de conexiones: {e}")
        raise

def execute_query(query, params=None):
    """Ejecuta una consulta SQL y devuelve los resultados"""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            conn.commit()
            # Intentar obtener resultados si hay
            try:
                result = cursor.fetchall()
                return result
            except psycopg2.ProgrammingError:
                # No hay resultados para retornar
                return None
    except Exception as e:
        logger.error(f"Error ejecutando consulta: {e}")
        conn.rollback()
        raise
    finally:
        release_connection(conn)

def check_table_exists(table_name):
    """Verifica si una tabla existe en la base de datos"""
    query = """
    SELECT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = %s
    );
    """
    result = execute_query(query, (table_name,))
    return result[0][0] if result else False

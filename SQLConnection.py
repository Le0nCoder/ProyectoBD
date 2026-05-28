import mysql.connector
from mysql.connector import Error
import json
import time
# ... (mantén tus importaciones de selenium y spacy del original)

# Configuración de base de datos
db_config = {
    'host': 'localhost',
    'user': 'root', # Cambia por tu usuario
    'password': '', # Cambia por tu contraseña
    'database': 'Sistema_Delitos'
}

def conectar_db():
    try:
        return mysql.connector.connect(**db_config)
    except Error as e:
        print(f"Error conectando a MySQL: {e}")
        return None

def insertar_en_db(data):
    conn = conectar_db()
    if not conn: return
    cursor = conn.cursor()

    try:
        # 1. Insertar Noticia (Necesaria para Evento)
        cursor.execute("INSERT INTO Noticia (titulo, url, fecha) VALUES (%s, %s, %s)", 
                       (data['titulo'], data['url'], data['fecha']))
        id_noticia = cursor.lastrowid

        # 2. Insertar Ubicación
        cursor.execute("INSERT INTO Ubicacion (municipio, estado, detalle) VALUES (%s, %s, %s)", 
                       (data['municipio'], data['estado'], data['detalle']))
        id_ubicacion = cursor.lastrowid

        # 3. Insertar Tipo_evento (Si no existe, idealmente se busca, aquí simplificado)
        # Asumiendo que el ID del tipo de evento ya lo tienes identificado por el LLM
        id_tipo = data.get('id_tipo_evento', 1) 

        # 4. Insertar Evento
        cursor.execute("INSERT INTO Evento (fecha, hora, id_tipo_evento, id_ubicacion, id_noticia) VALUES (%s, %s, %s, %s, %s)",
                       (data['fecha'], data['hora'], id_tipo, id_ubicacion, id_noticia))
        id_evento = cursor.lastrowid

        # 5. Insertar Vehículos si existen
        if 'vehiculos' in data:
            for v in data['vehiculos']:
                cursor.execute("INSERT INTO Vehiculo (tipo, modelo) VALUES (%s, %s)", (v['tipo'], v['modelo']))
                id_vehiculo = cursor.lastrowid
                cursor.execute("INSERT INTO Evento_vehiculo (id_evento, id_vehiculo, rol_vehiculo) VALUES (%s, %s, %s)",
                               (id_evento, id_vehiculo, v['rol']))

        # 6. Insertar Afectación económica
        if 'dinero' in data:
            cursor.execute("INSERT INTO Afectacion_economica (id_evento, cantidad) VALUES (%s, %s)",
                           (id_evento, data['dinero']))

        conn.commit()
        print(f"Evento {id_evento} insertado correctamente.")

    except Error as e:
        print(f"Error insertando en BD: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

# --- Integración en tu lógica de extracción ---

# En tu bucle principal, después de obtener 'data' del pipeline_hibrido:
# (Suponiendo que 'data' ya tiene las claves: titulo, url, fecha, municipio, estado, etc.)

def procesar_y_guardar(links):
    for link in links:
        # ... tu código existente de Selenium ...
        
        # Después de extraer y procesar con Ollama:
        # data = pipeline_hibrido(text)
        
        # Guardar en JSON (como tu amigo) y en DB (novedad)
        insertar_en_db(data)
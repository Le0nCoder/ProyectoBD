import mysql.connector
from mysql.connector import Error
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import ollama
import json
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',      # Tu usuario de XAMPP (por defecto es root)
    'password': '',      # Tu contraseña de XAMPP (por defecto está vacía)
    'database': 'Sistema_Delitos'
}

def conectar_db():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"❌ Error conectando a MySQL: {e}")
        return None

# ==========================================
# 2. INSERCIÓN EN BASE DE DATOS (EL MOTOR)
# ==========================================
def insertar_en_db(data):
    conn = conectar_db()
    if not conn: return
    cursor = conn.cursor()

    try:
        print("\n💾 Guardando en base de datos...")
        
        # 1. Insertar Noticia
        cursor.execute("INSERT INTO Noticia (titulo, url, fecha) VALUES (%s, %s, %s)", 
                       (data.get('titulo', 'Sin título'), data.get('url', ''), data.get('fecha_noticia', datetime.now().date())))
        id_noticia = cursor.lastrowid

        # 2. Insertar Ubicación
        cursor.execute("INSERT INTO Ubicacion (municipio, estado, detalle) VALUES (%s, %s, %s)", 
                       (data.get('municipio', 'Desconocido'), data.get('estado', 'Desconocido'), data.get('detalle_ubicacion', '')))
        id_ubicacion = cursor.lastrowid

        # 3. Tipo de Evento (Por simplicidad, forzamos un ID, idealmente buscaríamos en la tabla)
        id_tipo_evento = 1 # Puedes cambiar esto con lógica más avanzada luego

        # 4. Insertar Evento (Tabla Principal)
        cursor.execute("""
            INSERT INTO Evento (fecha, hora, id_tipo_evento, id_ubicacion, id_noticia) 
            VALUES (%s, %s, %s, %s, %s)
        """, (data.get('fecha_evento', '2023-01-01'), data.get('hora_evento', '00:00:00'), id_tipo_evento, id_ubicacion, id_noticia))
        id_evento = cursor.lastrowid

        # 5. Insertar Vehículos (Si hay)
        if 'vehiculos' in data and data['vehiculos']:
            for v in data['vehiculos']:
                cursor.execute("INSERT INTO Vehiculo (tipo, modelo, descripcion) VALUES (%s, %s, %s)", 
                               (v.get('tipo', ''), v.get('modelo', ''), 'Extraído de noticia'))
                id_vehiculo = cursor.lastrowid
                
                cursor.execute("INSERT INTO Evento_vehiculo (id_evento, id_vehiculo, rol_vehiculo) VALUES (%s, %s, %s)",
                               (id_evento, id_vehiculo, v.get('rol', 'Involucrado')))

        # 6. Insertar Participantes (Si hay)
        if 'participantes' in data and data['participantes']:
            for p in data['participantes']:
                cursor.execute("INSERT INTO Participante (nombre) VALUES (%s)", (p.get('nombre', 'Desconocido'),))
                id_participante = cursor.lastrowid
                
                cursor.execute("INSERT INTO Involucrado (id_evento, id_participante, tipo_rol) VALUES (%s, %s, %s)",
                               (id_evento, id_participante, p.get('rol', 'Desconocido')))

        # 7. Insertar Afectación Económica (Si hay dinero detectado)
        if 'dinero_robado' in data and data['dinero_robado'] > 0:
            cursor.execute("INSERT INTO Afectacion_economica (id_evento, cantidad) VALUES (%s, %s)",
                           (id_evento, data['dinero_robado']))

        # Confirmar todos los cambios
        conn.commit()
        print(f"✅ ¡Éxito! Evento #{id_evento} registrado correctamente.")

    except Error as e:
        print(f"❌ Error al insertar datos: {e}")
        conn.rollback() # Deshacer si hay error para no dejar datos a medias
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 3. EXTRACCIÓN CON INTELIGENCIA ARTIFICIAL
# ==========================================
def procesar_con_ollama(texto_noticia, titulo, url):
    print("🤖 Analizando texto con Ollama...")
    
    prompt = f"""
    Eres un analista de datos policiales. Extrae la siguiente información de la noticia y devuélvela ESTRICTAMENTE en formato JSON.
    Si no encuentras un dato, déjalo vacío o pon 0. No agregues texto extra fuera del JSON.
    
    Estructura JSON esperada:
    {{
        "titulo": "{titulo}",
        "url": "{url}",
        "fecha_noticia": "YYYY-MM-DD",
        "fecha_evento": "YYYY-MM-DD",
        "hora_evento": "HH:MM:SS",
        "municipio": "Nombre del municipio",
        "estado": "Nombre del estado",
        "detalle_ubicacion": "Calles o colonia",
        "dinero_robado": 0.00,
        "vehiculos": [
            {{"tipo": "Moto/Auto", "modelo": "Marca o color", "rol": "Escape/Robado"}}
        ],
        "participantes": [
            {{"nombre": "Nombre si lo hay o 'Sujeto 1'", "rol": "Víctima/Sospechoso/Detenido"}}
        ]
    }}
    
    Noticia a analizar:
    {texto_noticia}
    """

    try:
        # Llamada a Ollama local (Asegúrate de usar el modelo que tengas instalado, ej: 'llama3' o 'mistral')
        response = ollama.chat(model='llama3', messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        # Limpiar la respuesta para asegurar que es JSON
        respuesta_texto = response['message']['content']
        inicio = respuesta_texto.find('{')
        fin = respuesta_texto.rfind('}') + 1
        json_limpio = respuesta_texto[inicio:fin]
        
        datos = json.loads(json_limpio)
        return datos
    except Exception as e:
        print(f"❌ Error al procesar con Ollama: {e}")
        return None

# ==========================================
# 4. SCRAPING WEB (SELENIUM)
# ==========================================
def hacer_scraping(url):
    print(f"🌐 Iniciando navegador para: {url}")
    opciones = webdriver.ChromeOptions()
    opciones.add_argument('--headless') # Ejecutar sin abrir ventana visible
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opciones)
    
    try:
        driver.get(url)
        time.sleep(3) # Esperar a que cargue
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extraer el título (modifica 'h1' dependiendo del sitio web)
        titulo = soup.find('h1').get_text(strip=True) if soup.find('h1') else 'Noticia sin título'
        
        # Extraer los párrafos de la noticia
        parrafos = soup.find_all('p')
        texto_completo = " ".join([p.get_text(strip=True) for p in parrafos])
        
        print("✅ Texto extraído correctamente. Cerrando navegador.")
        return titulo, texto_completo
        
    except Exception as e:
        print(f"❌ Error en Scraping: {e}")
        return None, None
    finally:
        driver.quit()

import mysql.connector
from mysql.connector import Error
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import ollama
import json
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',      # Tu usuario de XAMPP (por defecto es root)
    'password': '',      # Tu contraseña de XAMPP (por defecto está vacía)
    'database': 'Sistema_Delitos'
}

def conectar_db():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"❌ Error conectando a MySQL: {e}")
        return None

# ==========================================
# 2. INSERCIÓN EN BASE DE DATOS (EL MOTOR)
# ==========================================
def insertar_en_db(data):
    conn = conectar_db()
    if not conn: return
    cursor = conn.cursor()

    try:
        print("\n💾 Guardando en base de datos...")
        
        # 1. Insertar Noticia
        cursor.execute("INSERT INTO Noticia (titulo, url, fecha) VALUES (%s, %s, %s)", 
                       (data.get('titulo', 'Sin título'), data.get('url', ''), data.get('fecha_noticia', datetime.now().date())))
        id_noticia = cursor.lastrowid

        # 2. Insertar Ubicación
        cursor.execute("INSERT INTO Ubicacion (municipio, estado, detalle) VALUES (%s, %s, %s)", 
                       (data.get('municipio', 'Desconocido'), data.get('estado', 'Desconocido'), data.get('detalle_ubicacion', '')))
        id_ubicacion = cursor.lastrowid

        # 3. Tipo de Evento (Por simplicidad, forzamos un ID, idealmente buscaríamos en la tabla)
        id_tipo_evento = 1 # Puedes cambiar esto con lógica más avanzada luego

        # 4. Insertar Evento (Tabla Principal)
        cursor.execute("""
            INSERT INTO Evento (fecha, hora, id_tipo_evento, id_ubicacion, id_noticia) 
            VALUES (%s, %s, %s, %s, %s)
        """, (data.get('fecha_evento', '2023-01-01'), data.get('hora_evento', '00:00:00'), id_tipo_evento, id_ubicacion, id_noticia))
        id_evento = cursor.lastrowid

        # 5. Insertar Vehículos (Si hay)
        if 'vehiculos' in data and data['vehiculos']:
            for v in data['vehiculos']:
                cursor.execute("INSERT INTO Vehiculo (tipo, modelo, descripcion) VALUES (%s, %s, %s)", 
                               (v.get('tipo', ''), v.get('modelo', ''), 'Extraído de noticia'))
                id_vehiculo = cursor.lastrowid
                
                cursor.execute("INSERT INTO Evento_vehiculo (id_evento, id_vehiculo, rol_vehiculo) VALUES (%s, %s, %s)",
                               (id_evento, id_vehiculo, v.get('rol', 'Involucrado')))

        # 6. Insertar Participantes (Si hay)
        if 'participantes' in data and data['participantes']:
            for p in data['participantes']:
                cursor.execute("INSERT INTO Participante (nombre) VALUES (%s)", (p.get('nombre', 'Desconocido'),))
                id_participante = cursor.lastrowid
                
                cursor.execute("INSERT INTO Involucrado (id_evento, id_participante, tipo_rol) VALUES (%s, %s, %s)",
                               (id_evento, id_participante, p.get('rol', 'Desconocido')))

        # 7. Insertar Afectación Económica (Si hay dinero detectado)
        if 'dinero_robado' in data and data['dinero_robado'] > 0:
            cursor.execute("INSERT INTO Afectacion_economica (id_evento, cantidad) VALUES (%s, %s)",
                           (id_evento, data['dinero_robado']))

        # Confirmar todos los cambios
        conn.commit()
        print(f"✅ ¡Éxito! Evento #{id_evento} registrado correctamente.")

    except Error as e:
        print(f"❌ Error al insertar datos: {e}")
        conn.rollback() # Deshacer si hay error para no dejar datos a medias
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 3. EXTRACCIÓN CON INTELIGENCIA ARTIFICIAL
# ==========================================
def procesar_con_ollama(texto_noticia, titulo, url):
    print("🤖 Analizando texto con Ollama...")
    
    prompt = f"""
    Eres un analista de datos policiales. Extrae la siguiente información de la noticia y devuélvela ESTRICTAMENTE en formato JSON.
    Si no encuentras un dato, déjalo vacío o pon 0. No agregues texto extra fuera del JSON.
    
    Estructura JSON esperada:
    {{
        "titulo": "{titulo}",
        "url": "{url}",
        "fecha_noticia": "YYYY-MM-DD",
        "fecha_evento": "YYYY-MM-DD",
        "hora_evento": "HH:MM:SS",
        "municipio": "Nombre del municipio",
        "estado": "Nombre del estado",
        "detalle_ubicacion": "Calles o colonia",
        "dinero_robado": 0.00,
        "vehiculos": [
            {{"tipo": "Moto/Auto", "modelo": "Marca o color", "rol": "Escape/Robado"}}
        ],
        "participantes": [
            {{"nombre": "Nombre si lo hay o 'Sujeto 1'", "rol": "Víctima/Sospechoso/Detenido"}}
        ]
    }}
    
    Noticia a analizar:
    {texto_noticia}
    """

    try:
        # Llamada a Ollama local (Asegúrate de usar el modelo que tengas instalado, ej: 'llama3' o 'mistral')
        response = ollama.chat(model='llama3', messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        # Limpiar la respuesta para asegurar que es JSON
        respuesta_texto = response['message']['content']
        inicio = respuesta_texto.find('{')
        fin = respuesta_texto.rfind('}') + 1
        json_limpio = respuesta_texto[inicio:fin]
        
        datos = json.loads(json_limpio)
        return datos
    except Exception as e:
        print(f"❌ Error al procesar con Ollama: {e}")
        return None

# ==========================================
# 4. SCRAPING WEB (SELENIUM)
# ==========================================
def hacer_scraping(url):
    print(f"🌐 Iniciando navegador para: {url}")
    opciones = webdriver.ChromeOptions()
    opciones.add_argument('--headless') # Ejecutar sin abrir ventana visible
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opciones)
    
    try:
        driver.get(url)
        time.sleep(3) # Esperar a que cargue
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extraer el título (modifica 'h1' dependiendo del sitio web)
        titulo = soup.find('h1').get_text(strip=True) if soup.find('h1') else 'Noticia sin título'
        
        # Extraer los párrafos de la noticia
        parrafos = soup.find_all('p')
        texto_completo = " ".join([p.get_text(strip=True) for p in parrafos])
        
        print("✅ Texto extraído correctamente. Cerrando navegador.")
        return titulo, texto_completo
        
    except Exception as e:
        print(f"❌ Error en Scraping: {e}")
        return None, None
    finally:
        driver.quit()

# ==========================================
# 5. BLOQUE PRINCIPAL (EJECUCIÓN)
# ==========================================
if __name__ == "__main__":
    print("--- SISTEMA DE EXTRACCIÓN DE DELITOS ---")
    
    # URL de prueba (Cámbiala por una noticia real de tu interés)
    url_prueba = "https://aristeguinoticias.com/2805/mexico/reportan-asalto-a-cuentahabiente-en-plaza-comercial-de-puebla/"
    
    # 1. Scrapear la web
    titulo, texto = hacer_scraping(url_prueba)
    
    if texto:
        # 2. Procesar con Inteligencia Artificial
        datos_json = procesar_con_ollama(texto, titulo, url_prueba)
        
        if datos_json:
            print("\n📊 Datos extraídos por IA:")
            print(json.dumps(datos_json, indent=4, ensure_ascii=False))
            
            # 3. Guardar en la base de datos
            insertar_en_db(datos_json)
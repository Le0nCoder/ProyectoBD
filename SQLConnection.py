Python
from bs4 import BeautifulSoup
import spacy
import re
import time
import pickle
import json
import random
import requests
import undetected_chromedriver as uc
import mysql.connector
from mysql.connector import Error
from urllib.parse import urlparse
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================================
# CONFIGURACIÓN GENERAL, LLM Y BASE DE DATOS
# =====================================================================
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
NA = None
CHECKPOINT_CADA = 20
ARCHIVO_SALIDA = "dataset_delitos_v3.json"
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'Sistema_Delitos'
}

# =====================================================================
# LÓGICA DE PERSISTENCIA (AÑADIDA)
# =====================================================================
def guardar_en_bd(data):
    """Persistencia según SQL_eventos_3.txt y SQL_delitos.txt"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # 1. Insertar Noticia
        cursor.execute("INSERT INTO Noticia (titulo, url, fecha) VALUES (%s, %s, %s)", 
                       (data['noticia']['titulo'], data['noticia']['url'], datetime.now()))
        id_noticia = cursor.lastrowid
        
        # 2. Insertar Ubicación
        cursor.execute("INSERT INTO Ubicacion (municipio, estado, detalle) VALUES (%s, %s, %s)",
                       (data.get('municipio'), data.get('estado'), data.get('detalle', '')))
        id_ubi = cursor.lastrowid
        
        # 3. Insertar Vehículo
        cursor.execute("INSERT INTO Vehiculo (tipo, modelo) VALUES (%s, %s)",
                       (data.get('vehiculo_tipo', 'N/A'), data.get('vehiculo_modelo', 'N/A')))
        id_veh = cursor.lastrowid
        
        # 4. Insertar Evento (relacionado)
        cursor.execute("INSERT INTO Evento (id_tipo_evento, id_Ubicacion, id_vehiculo) VALUES (%s, %s, %s)",
                       (data.get('id_tipo_evento', 1), id_ubi, id_veh))
        id_evento = cursor.lastrowid
        
        # 5. Insertar Afectación
        cursor.execute("INSERT INTO Afectacion_economica (id_evento, cantidad) VALUES (%s, %s)",
                       (id_evento, data.get('dinero', 0)))
        
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Error crítico en BD: {e}")

# =====================================================================
# FUNCIONES DE EXTRACCIÓN WEB (SELENIUM + BEAUTIFULSOUP)
# =====================================================================
def detectar_version(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, "h1.entry-title--with-subtitle")
        return "v2"  
    except:
        return "v1"  

def extraer_v1(driver):
    try:
        titulo = driver.find_element(By.CSS_SELECTOR, "h1.entry-title").text.strip()
    except:
        titulo = None

    try:
        fecha_tag = driver.find_element(By.CSS_SELECTOR, "time.entry-date.published")
        fecha_iso = fecha_tag.get_attribute("datetime")
        fecha_texto = fecha_tag.text.strip()
    except:
        fecha_iso, fecha_texto = None, None

    content = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CLASS_NAME, "entry-content"))
    )

    html = content.get_attribute("innerHTML")
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "iframe", "figure", "noscript"]):
        tag.decompose()

    text = re.sub(r"\s+", " ", soup.get_text(" "))
    return titulo, fecha_iso, fecha_texto, text

def extraer_v2(driver):
    try:
        titulo = driver.find_element(
            By.CSS_SELECTOR,
            "h1.entry-title.entry-title--with-subtitle"
        ).text.strip()
    except:
        titulo = None

    try:
        fecha_tag = driver.find_element(By.CSS_SELECTOR, "time.entry-date.published")
        fecha_iso = fecha_tag.get_attribute("datetime")
        fecha_texto = fecha_tag.text.strip()
    except:
        fecha_iso, fecha_texto = None, None

    content = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.entry-content"))
    )

    html = content.get_attribute("innerHTML")
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "iframe", "figure", "noscript", "aside", "svg", "form"]):
        tag.decompose()

    for tag in soup.select(".wp-block-embed, .google-news-wrap, .tags-links, .widget"):
        tag.decompose()

    texto_limpio = []
    for elem in soup.find_all(["p", "h2"]):
        if elem.name == "h2":
            break
        if elem.name == "p":
            t = elem.get_text(" ", strip=True)
            if not t:
                continue
            texto_limpio.append(t)

    text = re.sub(r"\s+", " ", " ".join(texto_limpio))
    return titulo, fecha_iso, fecha_texto, text

def consultar_ollama(prompt):
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "temperature": 0.1,
            },
            timeout=45
        )
        if response.status_code != 200:
            print(f"❌ Error de Ollama (Código {response.status_code}): {response.text}")
            return None
            
        res_text = response.json().get("response", "").strip()
        return res_text
    except Exception as e:
        print(f"❌ Error de conexión al servidor de Ollama: {e}")
        return None

# =====================================================================
# PIPELINE DE IA 
# =====================================================================

def llm_tipo_evento(texto):
    """Mapea a la tabla `tipo_evento` (columna nombre)"""
    prompt = f"""
Clasifica el tipo de evento criminal o vial en una sola palabra. Ejemplos comunes: Homicidio, Detención, Secuestro, Asalto, Accidente, Operativo, Extorsión, Robo.
Responde ÚNICAMENTE con el nombre del evento, sin puntos ni palabras extra.
Texto:
{texto[:2000]}
"""
    r = consultar_ollama(prompt)
    return r.strip() if r else None

def llm_geografia_ubicacion(texto):
    """Mapea directamente a los campos de la tabla unificada `ubicacion`"""
    prompt = f"""
    Eres un extractor de datos geográficos. Analiza el siguiente texto de una noticia y extrae la ubicación.
    
    Reglas estrictas:
    - Responde SÓLO con el formato: municipio|estado|direccion|punto_referencia
    - Si un dato no existe en el texto, escribe literalmente "NA" en su lugar.
    - NO añadidas explicaciones, solo la línea con las barras.
    
    Texto:
    {texto[:2000]}
    """
    
    r = consultar_ollama(prompt)
    
    # Limpieza: quitamos posibles espacios extra al inicio o final
    r = r.strip()
    
    # Si la respuesta está vacía o no tiene el formato, retornamos NA en todo
    if not r or "|" not in r:
        return "NA", "NA", "NA", "NA"

    # Dividimos por la barra
    parts = [x.strip() for x in r.split("|")]
    
    # Aseguramos que siempre tengamos 4 elementos
    while len(parts) < 4:
        parts.append("NA")

    # Mapeo limpio
    def limpiar(valor):
        return None if valor.upper() == "NA" else valor

    return (
        limpiar(parts[0]),
        limpiar(parts[1]),
        limpiar(parts[2]),
        limpiar(parts[3])
    )

def llm_fecha_hora_evento(texto):
    """Mapea a fecha_evento (DATE) y hora_evento (TIME) de la tabla `evento`"""
    prompt = f"""
Extrae la fecha y la hora en la que ocurrieron los hechos descritos.
Reglas de formato obligatorio: YYYY-MM-DD|HH:MM:SS
- Si no hay hora exacta pero dice 'madrugada' usa 03:00:00, 'mañana' usa 09:00:00, 'tarde' usa 15:00:00, 'noche' usa 21:00:00.
- Si no hay fecha o no hay hora, coloca NA en el campo correspondiente.

Texto:
{texto[:2000]}
"""
    r = consultar_ollama(prompt)
    if not r or "|" not in r:
        return NA, NA

    f, h = [x.strip() for x in r.split("|", 1)]
    return (
        f if f.lower() != "na" else NA,
        h if h.lower() != "na" else NA
    )

def llm_vehiculos_json(texto):
    """Mapea a la tabla `vehiculo` (tipo, modelo) para la relación N:M `movilidad`"""
    prompt = f"""
Extrae todos los vehículos involucrados mencionados en el texto.
Debes responder ÚNICAMENTE con un arreglo JSON válido (sin marcas de código como ```json).
Campos por vehículo:
- "tipo": Tipo de vehículo (ej: 'Automóvil', 'Camioneta', 'Motocicleta', 'Tractor', 'Trailer').
- "modelo": Marca y/o modelo si se menciona (ej: 'Nissan Versa', 'Motocicleta Honda', 'Camioneta pick-up'). Si no se sabe, pon null.

Ejemplo de respuesta esperada:
[
  {{"tipo": "Automóvil", "modelo": "Nissan Versa"}},
  {{"tipo": "Motocicleta", "modelo": null}}
]
Genera estrictamente el JSON sin texto introductorio, ni explicaciones, ni bloques de código Markdown.
Si no hay ningún vehículo involucrado en la noticia, responde vacío: []

Texto:
{texto[:2000]}
"""
    r = consultar_ollama(prompt)
    try:
        r_clean = re.sub(r"```[a-zA-Z]*", "", r).strip()
        return json.loads(r_clean)
    except:
        return []

def llm_personas_registro_json(texto):
    """Mapea a las tablas `persona` y `rol_participante` para la intermedia `registro`"""
    prompt = f"""
Extrae todas las personas o colectivos participantes identificables en el evento.
Debes responder ÚNICAMENTE con un arreglo JSON válido.
Campos por entidad:
- "nombre_completo": Nombre real, alias, o descripción general si no se menciona el nombre (ej: 'Roberto de los Santos, alias El Bukanas', 'Juan N.', 'Vecinos afectados').
- "rol": Elige obligatoriamente uno de estos nombres según su rol: 'Autor', 'Afectado', 'Testigo'.

Ejemplo de respuesta esperada:
[
  {{"nombre_completo": "Roberto de los Santos, alias El Bukanas", "rol": "Autor"}},
  {{"nombre_completo": "Comerciantes locales", "rol": "Afectado"}}
]
Genera estrictamente el JSON sin texto introductorio, ni explicaciones, ni bloques de código Markdown.
Si no hay personas o partes involucradas, responde: []

Texto:
{texto[:2000]}
"""
    r = consultar_ollama(prompt)
    try:
        r_clean = re.sub(r"```[a-zA-Z]*", "", r).strip()
        return json.loads(r_clean)
    except:
        return []

def llm_impacto_economico(texto):
    """Mapea a la tabla `impacto_economico` (columna monto_estimado)"""
    prompt = f"""
Extrae si se menciona explícitamente alguna pérdida de dinero, multas o costos por robos/daños materiales.
Regla: Responde únicamente con el número o una frase corta descriptiva del monto económico (ej: "45000.00" o "Pérdidas millonarias"). Si no se menciona ninguna cantidad ni afectación monetaria, responde estrictamente: NA

Texto:
{texto[:2000]}
"""
    r = consultar_ollama(prompt)
    return r.strip() if r and r.upper() != "NA" else NA

# =====================================================================
# PIPELINE INTEGRADOR (Alineado 100% con proy_v2)
# =====================================================================
def pipeline_hibrido(text):
    data = {
        "descripcion_detallada": text  # Cae directo en la columna de la tabla `evento`
    }

    try:
        tipo_evento = llm_tipo_evento(text)
        mun, edo, dir_calle, ref = llm_geografia_ubicacion(text)
        fecha_ev, hora_ev = llm_fecha_hora_evento(text)
        vehiculos = llm_vehiculos_json(text)
        participantes = llm_personas_registro_json(text)
        monto_est = llm_impacto_economico(text)

        # Unificamos calle y referencias en la columna 'detalle' de la tabla 'ubicacion'
        elementos_detalle = []
        if dir_calle: elementos_detalle.append(dir_calle)
        if ref: elementos_detalle.append(ref)
        detalle_completo = ". ".join(elementos_detalle).strip()

        data.update({
            "tipo_evento": tipo_evento,
            "ubicacion": {  
                "municipio": mun,
                "estado": edo,
                "detalle": detalle_completo if detalle_completo else None
            },
            "evento_datos": {
                "fecha_evento": fecha_ev,
                "hora_evento": hora_ev,
                "estatus": "Reportado"
            },
            "vehiculos": vehiculos,         
            "participantes": participantes, 
            "impacto_economico": {          
                "monto_estimado": monto_est
            }
        })

    except Exception as e:
        print("Error en la extracción del pipeline:", e)

    return data

# =====================================================================
# AUXILIARES Y NAVEGACIÓN
# =====================================================================
def crear_driver():
    options = uc.ChromeOptions()
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, version_main=147)
    return driver

def comportamiento_humano(driver):
    altura = driver.execute_script("return document.body.scrollHeight")
    pos = 0
    while pos < altura:
        pos += random.randint(200, 600)
        driver.execute_script(f"window.scrollTo(0,{pos});")
        time.sleep(random.uniform(0.4, 1.2))

def normalizar_fecha(fecha_iso):
    try:
        return datetime.fromisoformat(fecha_iso).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return None

# =====================================================================
# LOOP PRINCIPAL DE SCRAPING Y EXTRACCIÓN
# =====================================================================
import os

# --- CONTROL DEL ARCHIVO PKL ---
if os.path.exists("links_final.pkl"):
    with open("links_final.pkl", "rb") as f:
        links = pickle.load(f)
    print(f"Archivo 'links_final.pkl' cargado con éxito. Se procesarán {len(links)} enlaces.")
else:
    print("No se encontró 'links_final.pkl'. Usando enlaces de prueba por defecto...")
    links = [
        "https://www.diariocambio.com.mx/2026/policiaca/no-aprenden-otra-vez-clausuran-el-bar-tulum-a-dias-de-reabrir-en-cu",
        "https://www.diariocambio.com.mx/2026/policiaca/misterio-en-tlahuapan-hallan-auto-abandonado-y-a-tres-personas-maniatadas-sobre-la-mexico-puebla"
    ]

driver = crear_driver()
resultados = []

# Cargar progreso si se interrumpió una ejecución previa
if os.path.exists(ARCHIVO_SALIDA):
    with open(ARCHIVO_SALIDA, "r", encoding="utf-8") as f:
        resultados = json.load(f)
    print(f"Checkpoint encontrado. Cargando {len(resultados)} registros ya procesados.")

for i, link in enumerate(links):
    if any(res.get("noticia", {}).get("url") == link for res in resultados):
        continue

    if "policiaca" in link:
        try:
            print(f"[{i+1}/{len(links)}] Extrayendo: {link}")

            driver.get(link)
            time.sleep(random.uniform(4, 7))
            comportamiento_humano(driver)
            
            try:
                print("Buscando ventana emergente para cerrarla...")
                boton_cerrar = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), '×')] | //div[contains(@class, 'close')] | //button[@aria-label='Close']"))
                )
                boton_cerrar.click()
                print("Ventana emergente cerrada con éxito.")
            except Exception:
                print("No se detectó ventana emergente o ya estaba cerrada.")

            # Detectar versión del HTML y extraer cadenas primarias
            version = detectar_version(driver)
            if version == "v1":
                titulo, fecha_iso, fecha_texto, text = extraer_v1(driver)
            else:
                titulo, fecha_iso, fecha_texto, text = extraer_v2(driver)

            if not text:
                print(f"No se pudo extraer texto del enlace: {link}")
                continue

            # Procesar el texto limpio con Ollama
            data = pipeline_hibrido(text)

            # Automatizar metadatos de la fuente mediante la URL
            dominio = urlparse(link).netloc.replace("www.", "")

            # Mapeo a la tabla `noticia`
            data.update({
                "noticia": {
                    "fuente_medio": dominio,
                    "titulo": titulo,
                    "url": link,
                    "fecha_publicacion": normalizar_fecha(fecha_iso)
                }
            })

            resultados.append(data)

            # Guardar Checkpoint automáticamente cada N iteraciones
            if (i + 1) % CHECKPOINT_CADA == 0:
                print(f"Guardando checkpoint seguro en '{ARCHIVO_SALIDA}'...")
                with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
                    json.dump(resultados, f, ensure_ascii=False, indent=4)

            time.sleep(random.uniform(3, 6))

        except Exception as e:
            print(f"Error crítico procesando el enlace {link}: {e}")

# Cierre del navegador automatizado y almacenamiento definitivo
driver.quit()

with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
    json.dump(resultados, f, ensure_ascii=False, indent=4)

print(f"¡Proceso completado! Archivo final generado con éxito en: '{ARCHIVO_SALIDA}'")
import streamlit as st
import requests
import feedparser
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from openai import OpenAI

st.set_page_config(page_title="Búsqueda Ayudas BOJA/BOE con IA", layout="wide", page_icon="🔍")

# ============= CONFIGURACIÓN DE SESIÓN MEJORADA =============

def crear_session():
    """Crea una sesión HTTP con retry automático y User-Agent completo"""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    
    return session

session = crear_session()

# ============= FUNCIONES DE IA CON OPENAI =============

def resumir_con_openai(texto, api_key, modelo="gpt-4o-mini", max_palabras=150):
    """
    Genera un resumen estructurado usando OpenAI API
    """
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {
                    "role": "system",
                    "content": """Eres un experto en analizar ayudas y subvenciones públicas españolas del BOE y BOJA. 
Extrae información clave de forma estructurada y precisa en español."""
                },
                {
                    "role": "user",
                    "content": f"""Analiza este documento oficial y proporciona SOLO un JSON con esta estructura exacta:

{{
  "tipo": "tipo de documento (ej: Subvención, Convocatoria, Resolución, Bases reguladoras)",
  "beneficiarios": "quién puede solicitarla (ej: PYMES, autónomos, entidades locales)",
  "cuantia": "importe o porcentaje disponible",
  "plazo": "fecha límite de solicitud o duración",
  "resumen": "descripción breve en máximo {max_palabras} palabras"
}}

DOCUMENTO:
{texto[:8000]}

Responde SOLO con el JSON, sin texto adicional."""
                }
            ],
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"}
        )
        
        resultado = json.loads(response.choices[0].message.content)
        return resultado
    
    except Exception as e:
        return {
            "tipo": "Error al procesar",
            "beneficiarios": "No disponible",
            "cuantia": "No disponible",
            "plazo": "No disponible",
            "resumen": f"Error al generar resumen: {str(e)[:100]}",
            "error": str(e)
        }

def busqueda_inteligente_openai(consulta_usuario, api_key, modelo="gpt-4o-mini"):
    """
    Convierte una consulta en lenguaje natural a palabras clave específicas
    """
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {
                    "role": "system",
                    "content": "Eres un experto en ayudas públicas españolas. Convierte consultas naturales en palabras clave para búsqueda en BOE/BOJA."
                },
                {
                    "role": "user",
                    "content": f"""Un usuario busca: "{consulta_usuario}"

Extrae las palabras clave MÁS RELEVANTES para buscar en el BOE/BOJA.
Responde SOLO con las palabras clave separadas por comas, sin explicaciones.

Ejemplos:
- "ayudas para abrir un restaurante" → "hostelería, restauración, pyme, emprendimiento"
- "subvenciones turismo rural Andalucía" → "turismo rural, alojamiento, feder, andalucía"
- "financiación startups tecnológicas" → "startup, innovación, tecnología, emprendimiento, I+D"

Palabras clave:"""
                }
            ],
            temperature=0.3,
            max_tokens=100
        )
        
        palabras = response.choices[0].message.content.strip()
        return palabras
    
    except Exception as e:
        st.error(f"Error en búsqueda inteligente: {e}")
        return consulta_usuario

def analizar_relevancia_openai(titulo, resumen, palabras_objetivo, api_key, modelo="gpt-4o-mini"):
    """
    Usa IA para determinar si una ayuda es relevante según criterios del usuario
    """
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {
                    "role": "system",
                    "content": "Eres un experto en ayudas públicas españolas. Evalúa la relevancia de documentos."
                },
                {
                    "role": "user",
                    "content": f"""Analiza si esta ayuda/subvención es relevante para alguien interesado en: {', '.join(palabras_objetivo)}

TÍTULO: {titulo}
RESUMEN: {resumen[:500]}

Responde SOLO con un número del 0 al 10 donde:
- 0-3: No relevante
- 4-6: Moderadamente relevante
- 7-10: Muy relevante

Número:"""
                }
            ],
            temperature=0.2,
            max_tokens=10
        )
        
        texto = response.choices[0].message.content.strip()
        numeros = re.findall(r'\d+', texto)
        if numeros:
            return int(numeros[0])
        return 5
    
    except:
        return 5

# ============= FUNCIONES DE BÚSQUEDA =============

def extraer_contenido_completo(url, max_intentos=2):
    """Extrae el texto completo de una página"""
    for intento in range(max_intentos):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for element in soup(["script", "style", "nav", "header", "footer", "iframe"]):
                element.decompose()
            
            contenido = soup.get_text(separator=' ', strip=True)
            time.sleep(0.5)
            
            return contenido
            
        except requests.exceptions.Timeout:
            if intento < max_intentos - 1:
                st.warning(f"⏱️ Timeout en {url} (intento {intento + 1}/{max_intentos})")
        except requests.exceptions.HTTPError as e:
            st.warning(f"❌ Error HTTP {e.response.status_code} en {url}")
            break
        except requests.exceptions.RequestException as e:
            if intento < max_intentos - 1:
                st.warning(f"⚠️ Error de conexión: {str(e)[:100]}")
        except Exception as e:
            st.error(f"🔴 Error inesperado: {str(e)[:100]}")
    
    return ""

def buscar_boja_feed(contenido_completo=False):
    """Busca en el feed principal de BOJA"""
    resultados = []
    url = "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"
    
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            fecha_str = entry.get('published', entry.get('updated', ''))
            fecha = pd.to_datetime(fecha_str, errors='coerce', utc=True)
            if pd.notna(fecha):
                fecha = fecha.tz_localize(None)
            
            texto_completo = ""
            if contenido_completo and enlace:
                texto_completo = extraer_contenido_completo(enlace)
            
            resultados.append({
                'Boletín': 'BOJA',
                'Título': titulo,
                'Resumen': resumen[:300],
                'Contenido_Completo': texto_completo,
                'Enlace': enlace,
                'Fecha': fecha
            })
            
    except requests.exceptions.RequestException as e:
        st.error(f"Error al buscar en BOJA: {e}")
    except Exception as e:
        st.error(f"Error inesperado en BOJA: {e}")
    
    return resultados

def buscar_boe_rss(contenido_completo=False):
    """Busca en el RSS del BOE"""
    resultados = []
    url = "https://www.boe.es/rss/boe.php"
    
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            fecha_str = entry.get('published', entry.get('updated', ''))
            fecha = pd.to_datetime(fecha_str, errors='coerce', utc=True)
            if pd.notna(fecha):
                fecha = fecha.tz_localize(None)
            
            texto_completo = ""
            if contenido_completo and enlace:
                texto_completo = extraer_contenido_completo(enlace)
            
            resultados.append({
                'Boletín': 'BOE',
                'Título': titulo,
                'Resumen': resumen[:300],
                'Contenido_Completo': texto_completo,
                'Enlace': enlace,
                'Fecha': fecha
            })
            
    except requests.exceptions.RequestException as e:
        st.error(f"Error al buscar en BOE RSS: {e}")
    except Exception as e:
        st.error(f"Error inesperado en BOE RSS: {e}")
    
    return resultados

def buscar_boe_historico_api(fecha_inicio, fecha_fin, contenido_completo=False):
    """Busca en el BOE por rango de fechas usando la API oficial"""
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    while fecha_actual <= fecha_fin:
        progreso = dia_actual / total_dias
        progress_bar.progress(progreso)
        progress_text.text(f"Consultando BOE del {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
        fecha_str = fecha_actual.strftime("%Y%m%d")
        url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha_str}"
        
        try:
            response = session.get(url, headers={"Accept": "application/json"}, timeout=20)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("status", {}).get("code") == "200":
                    sumario = data.get("data", {}).get("sumario", {})
                    
                    for diario in sumario.get("diario", []):
                        for seccion in diario.get("seccion", []):
                            for departamento in seccion.get("departamento", []):
                                for epigrafe in departamento.get("epigrafe", []):
                                    items = epigrafe.get("item", [])
                                    if isinstance(items, dict):
                                        items = [items]
                                    
                                    for item in items:
                                        titulo = item.get("titulo", "")
                                        enlace = item.get("url_html", "")
                                        
                                        texto_completo = ""
                                        if contenido_completo and enlace:
                                            texto_completo = extraer_contenido_completo(enlace)
                                        
                                        resultados.append({
                                            'Boletín': 'BOE',
                                            'Título': titulo,
                                            'Resumen': f"Sección: {seccion.get('nombre', '')} - {departamento.get('nombre', '')}",
                                            'Contenido_Completo': texto_completo,
                                            'Enlace': enlace,
                                            'Fecha': pd.to_datetime(fecha_actual)
                                        })
                                
                                items_directos = departamento.get("item", [])
                                if isinstance(items_directos, dict):
                                    items_directos = [items_directos]
                                
                                for item in items_directos:
                                    titulo = item.get("titulo", "")
                                    enlace = item.get("url_html", "")
                                    
                                    texto_completo = ""
                                    if contenido_completo and enlace:
                                        texto_completo = extraer_contenido_completo(enlace)
                                    
                                    resultados.append({
                                        'Boletín': 'BOE',
                                        'Título': titulo,
                                        'Resumen': f"Sección: {seccion.get('nombre', '')} - {departamento.get('nombre', '')}",
                                        'Contenido_Completo': texto_completo,
                                        'Enlace': enlace,
                                        'Fecha': pd.to_datetime(fecha_actual)
                                    })
            
            elif response.status_code != 404:
                st.warning(f"⚠️ Error {response.status_code} al consultar BOE del {fecha_actual.strftime('%d/%m/%Y')}")
            
            time.sleep(0.3)
            
        except requests.exceptions.RequestException as e:
            st.warning(f"Error de conexión para fecha {fecha_actual.strftime('%d/%m/%Y')}: {str(e)[:100]}")
        except Exception as e:
            st.error(f"Error procesando BOE del {fecha_actual.strftime('%d/%m/%Y')}: {str(e)[:100]}")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
    
    progress_bar.empty()
    progress_text.empty()
    
    return resultados

def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Busca en BOJA por rango de fechas"""
    resultados = []
    
    año_actual = fecha_inicio.year
    año_fin = fecha_fin.year
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    años_a_procesar = list(range(año_actual, año_fin + 1))
    total_años = len(años_a_procesar)
    año_idx = 0
    
    for año in años_a_procesar:
        progress_text.text(f"Consultando BOJA del año {año} ({año_idx+1}/{total_años})")
        
        if año == año_actual and año == año_fin:
            dia_inicio = fecha_inicio.timetuple().tm_yday
            dia_fin = fecha_fin.timetuple().tm_yday
            num_inicio = int(dia_inicio * 0.7)
            num_fin = int(dia_fin * 0.7)
        elif año == año_actual:
            dia_inicio = fecha_inicio.timetuple().tm_yday
            num_inicio = int(dia_inicio * 0.7)
            num_fin = 250
        elif año == año_fin:
            dia_fin = fecha_fin.timetuple().tm_yday
            num_inicio = 1
            num_fin = int(dia_fin * 0.7)
        else:
            num_inicio = 1
            num_fin = 250
        
        total_nums = num_fin - num_inicio + 1
        
        for idx, num in enumerate(range(max(1, num_inicio), min(num_fin + 1, 300))):
            progress_bar.progress((año_idx + idx/total_nums) / total_años)
            
            url_indice = f"https://www.juntadeandalucia.es/boja/{año}/{str(num).zfill(3)}/index.html"
            
            try:
                response = session.get(url_indice, timeout=15)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    for enlace in soup.find_all('a', href=True):
                        href = enlace['href']
                        
                        if '/boja/' in href and '.html' in href and href != url_indice:
                            titulo = enlace.get_text(strip=True)
                            
                            if href.startswith('/'):
                                href_completo = f"https://www.juntadeandalucia.es{href}"
                            elif href.startswith('http'):
                                href_completo = href
                            else:
                                href_completo = f"https://www.juntadeandalucia.es/boja/{año}/{str(num).zfill(3)}/{href}"
                            
                            texto_completo = ""
                            if contenido_completo:
                                texto_completo = extraer_contenido_completo(href_completo)
                            
                            resultados.append({
                                'Boletín': 'BOJA',
                                'Título': titulo,
                                'Resumen': f'Boletín núm. {num} de {año}',
                                'Contenido_Completo': texto_completo,
                                'Enlace': href_completo,
                                'Fecha': pd.NaT
                            })
                
                time.sleep(0.2)
                
            except requests.exceptions.RequestException:
                continue
            except Exception as e:
                st.warning(f"Error procesando BOJA {año}/{num}: {str(e)[:100]}")
        
        año_idx += 1
        progress_bar.progress(año_idx / total_años)
    
    progress_bar.empty()
    progress_text.empty()
    
    return resultados

def filtrar_resultados(df, palabras_clave, solo_ayudas=True, busqueda_exacta=False):
    """Filtra los resultados con regex mejorado"""
    if df.empty:
        return df
    
    if 'Contenido_Completo' in df.columns:
        df['_texto_busqueda'] = (
            df['Título'].fillna('').astype(str) + ' ' + 
            df['Resumen'].fillna('').astype(str) + ' ' +
            df['Contenido_Completo'].fillna('').astype(str)
        )
    else:
        df['_texto_busqueda'] = (
            df['Título'].fillna('').astype(str) + ' ' + 
            df['Resumen'].fillna('').astype(str)
        )
    
    if solo_ayudas:
        patron_ayudas = r'\b(ayuda|ayudas|subvención|subvencion|subvenciones|convocatoria|convocatorias|bases\s+reguladoras)\b'
        mascara_ayudas = df['_texto_busqueda'].str.contains(
            patron_ayudas, 
            case=False, 
            regex=True, 
            na=False
        )
        df = df[mascara_ayudas]
    
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    palabra_escaped = re.escape(palabra)
                    patron = r'\b' + palabra_escaped + r'\b'
                    mascara_palabra = df['_texto_busqueda'].str.contains(
                        patron, 
                        case=False, 
                        regex=True, 
                        na=False
                    )
                else:
                    mascara_palabra = df['_texto_busqueda'].str.contains(
                        palabra, 
                        case=False, 
                        regex=False, 
                        na=False
                    )
                
                mascara_final = mascara_final | mascara_palabra
        
        df = df[mascara_final]
    
    df = df.drop(columns=['_texto_busqueda'])
    
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador Inteligente de Ayudas y Subvenciones")
st.markdown("**BOJA** (Junta de Andalucía) + **BOE** (Estado) - Con IA de OpenAI")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # ============= CONFIGURACIÓN DE IA =============
st.subheader("🤖 Inteligencia Artificial")

usar_ia = st.checkbox(
    "Activar resúmenes con IA",
    value=False,
    help="Genera resúmenes automáticos estructurados de cada ayuda"
)

api_key_openai = None
modelo_openai = None

if usar_ia:
    # Intentar cargar desde secrets, si no existe mostrar input
    api_key_default = ""
    try:
        api_key_default = st.secrets.get("openai", {}).get("api_key", "")
    except:
        pass
    
    if api_key_default:
        api_key_openai = api_key_default
        st.success("✅ API Key cargada desde configuración segura")
    else:
        api_key_openai = st.text_input(
            "🔑 API Key de OpenAI:",
            type="password",
            help="Obtén tu API key en: https://platform.openai.com/api-keys"
        )
    
    if api_key_openai:
        modelo_openai = st.selectbox(
            "Modelo OpenAI:",
            ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
            help="gpt-4o-mini: $0.15/1M tokens (recomendado)\ngpt-4o: $2.50/1M tokens (más potente)"
        )
        
        st.info(f"💰 Costo estimado por resumen: ~$0.001 con {modelo_openai}")
    else:
        st.warning("⚠️ Ingresa tu API Key de OpenAI para usar IA")

# Búsqueda inteligente
st.markdown("---")
busqueda_inteligente = st.checkbox(
    "🔮 Búsqueda inteligente con IA",
    value=False,
    help="Describe lo que buscas en lenguaje natural"
)

    
    palabras_clave = ""
    
    if busqueda_inteligente and api_key_openai:
        consulta_natural = st.text_area(
            "Describe lo que buscas:",
            placeholder="Ejemplo: Busco ayudas para abrir un negocio de turismo rural en Andalucía con fondos europeos",
            height=100
        )
        
        if st.button("🔮 Generar palabras clave", type="secondary"):
            if consulta_natural:
                with st.spinner("Analizando tu consulta con IA..."):
                    palabras_generadas = busqueda_inteligente_openai(
                        consulta_natural, 
                        api_key_openai, 
                        modelo_openai or "gpt-4o-mini"
                    )
                    st.success(f"✅ Palabras clave: **{palabras_generadas}**")
                    palabras_clave = palabras_generadas
            else:
                st.warning("Escribe una consulta primero")
    
    st.markdown("---")
    
    # ============= FUENTES DE DATOS =============
    st.subheader("📰 Fuentes de datos")
    
    usar_boja = st.checkbox("BOJA (Feed del día)", value=True)
    usar_boe = st.checkbox("BOE (RSS del día)", value=True)
    
    st.markdown("**📅 Búsqueda histórica**")
    usar_boja_hist = st.checkbox(
        "BOJA (Histórico por fechas)", 
        value=False,
        help="Busca en boletines anteriores"
    )
    usar_boe_hist = st.checkbox(
        "BOE (Histórico - API oficial)", 
        value=False,
        help="Busca usando la API oficial"
    )
    
    fecha_desde = None
    fecha_hasta = None
    
    if usar_boja_hist or usar_boe_hist:
        st.markdown("**Rango de fechas:**")
        col1, col2 = st.columns(2)
        
        fecha_desde = col1.date_input(
            "Desde",
            datetime.now() - timedelta(days=30),
            max_value=datetime.now()
        )
        
        fecha_hasta = col2.date_input(
            "Hasta",
            datetime.now(),
            max_value=datetime.now()
        )
        
        if fecha_desde > fecha_hasta:
            st.error("⚠️ La fecha 'Desde' debe ser anterior a 'Hasta'")
        
        dias_rango = (fecha_hasta - fecha_desde).days
        if dias_rango > 90:
            st.warning(f"⏱️ Rango amplio ({dias_rango} días)")
    
    st.markdown("---")
    
    # ============= OPCIONES DE BÚSQUEDA =============
    st.subheader("🔍 Opciones de búsqueda")
    
    contenido_completo = st.checkbox(
        "🔥 Buscar en contenido completo",
        value=False,
        help="⚠️ MUY LENTO: Descarga y analiza el texto completo"
    )
    
    if contenido_completo:
        st.warning("⏱️ Puede tardar 5-10 minutos o más")
    
    st.markdown("---")
    
    # ============= FILTROS =============
    st.subheader("🎯 Filtros")
    solo_ayudas = st.checkbox("Solo ayudas/subvenciones", value=True)
    
    if not busqueda_inteligente:
        palabras_clave = st.text_input(
            "Palabras clave (separadas por coma)", 
            "",
            help="Ejemplo: feder, turismo, pyme"
        )
    
    busqueda_exacta = st.checkbox(
        "Búsqueda de palabra exacta",
        value=True,
        help="'feder' no encuentra 'confederación'"
    )

# ============= BOTÓN DE BÚSQUEDA =============
if st.button("🚀 Buscar", type="primary"):
    if (usar_boja_hist or usar_boe_hist) and fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        st.error("❌ Corrige el rango de fechas")
    else:
        with st.spinner("Buscando en boletines oficiales..."):
            todos_resultados = []
            
            if usar_boja:
                with st.status("🔎 Buscando en BOJA (feed reciente)..."):
                    todos_resultados.extend(buscar_boja_feed(contenido_completo))
            
            if usar_boe:
                with st.status("🔎 Buscando en BOE (RSS reciente)..."):
                    todos_resultados.extend(buscar_boe_rss(contenido_completo))
            
            if usar_boja_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 Buscando en BOJA histórico..."):
                    todos_resultados.extend(
                        buscar_boja_historico(
                            datetime.combine(fecha_desde, datetime.min.time()),
                            datetime.combine(fecha_hasta, datetime.min.time()),
                            contenido_completo
                        )
                    )
            
            if usar_boe_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 Consultando BOE histórico..."):
                    todos_resultados.extend(
                        buscar_boe_historico_api(
                            datetime.combine(fecha_desde, datetime.min.time()),
                            datetime.combine(fecha_hasta, datetime.min.time()),
                            contenido_completo
                        )
                    )
            
            # Procesar resultados
            if todos_resultados:
                df = pd.DataFrame(todos_resultados)
                df = df.drop_duplicates(subset=['Enlace'], keep='first')
                
                lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
                df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
                df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
                
                if len(df_filtrado) > 0:
                    st.success(f"✅ **{len(df_filtrado)} resultados** encontrados (de {len(df)} totales)")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total resultados", len(df_filtrado))
                    col2.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
                    col3.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
                    
                    # ============= RESÚMENES CON IA =============
                    if usar_ia and api_key_openai:
                        st.markdown("---")
                        st.subheader("🤖 Resúmenes generados con IA")
                        
                        max_resumenes = st.slider(
                            "Número de ayudas a resumir:",
                            min_value=1,
                            max_value=min(20, len(df_filtrado)),
                            value=min(5, len(df_filtrado)),
                            help="Cada resumen tarda ~2-5 segundos"
                        )
                        
                        if st.button("📝 Generar resúmenes con IA", type="primary"):
                            resumenes = []
                            progress_bar = st.progress(0)
                            progress_text = st.empty()
                            
                            for idx, (_, row) in enumerate(df_filtrado.head(max_resumenes).iterrows()):
                                progress_bar.progress((idx + 1) / max_resumenes)
                                progress_text.text(f"Resumiendo {idx+1}/{max_resumenes}: {row['Título'][:50]}...")
                                
                                texto_completo = row.get('Contenido_Completo', '')
                                if not texto_completo and row['Enlace']:
                                    texto_completo = extraer_contenido_completo(row['Enlace'])
                                
                                texto_para_ia = f"{row['Título']}\n\n{row['Resumen']}\n\n{texto_completo[:6000]}"
                                
                                resumen_ia = resumir_con_openai(
                                    texto_para_ia, 
                                    api_key_openai, 
                                    modelo_openai
                                )
                                
                                resumenes.append({
                                    **row.to_dict(),
                                    **resumen_ia
                                })
                            
                            progress_bar.empty()
                            progress_text.empty()
                            
                            # Mostrar resúmenes
                            for res in resumenes:
                                with st.expander(f"📄 {res['Título'][:100]}...", expanded=False):
                                    col1, col2 = st.columns([2, 1])
                                    
                                    with col1:
                                        st.markdown(f"**🎯 Tipo:** {res.get('tipo', 'N/A')}")
                                        st.markdown(f"**👥 Beneficiarios:** {res.get('beneficiarios', 'N/A')}")
                                        st.markdown(f"**💰 Cuantía:** {res.get('cuantia', 'N/A')}")
                                        st.markdown(f"**📅 Plazo:** {res.get('plazo', 'N/A')}")
                                    
                                    with col2:
                                        st.markdown(f"**📰 Boletín:** {res['Boletín']}")
                                        if pd.notna(res.get('Fecha')):
                                            st.markdown(f"**📆 Fecha:** {res['Fecha'].strftime('%d/%m/%Y')}")
                                        st.markdown(f"[🔗 Ver documento]({res['Enlace']})")
                                    
                                    st.markdown("---")
                                    st.markdown(f"**📝 Resumen IA:**")
                                    st.info(res.get('resumen', 'No disponible'))
                            
                            # Exportar
                            df_resumenes = pd.DataFrame(resumenes)
                            columnas_export = ['Boletín', 'Título', 'tipo', 'beneficiarios', 'cuantia', 'plazo', 'resumen', 'Enlace', 'Fecha']
                            columnas_disponibles = [col for col in columnas_export if col in df_resumenes.columns]
                            
                            csv_resumenes = df_resumenes[columnas_disponibles].to_csv(index=False, encoding='utf-8-sig')
                            st.download_button(
                                "📥 Descargar resúmenes con IA (CSV)",
                                csv_resumenes,
                                f"resumenes_ia_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                                "text/csv",
                                key='download-resumenes'
                            )
                    
                    # Tabla original
                    st.markdown("---")
                    st.subheader("📊 Tabla de resultados")
                    st.dataframe(
                        df_filtrado,
                        use_container_width=True,
                        height=600,
                        column_config={
                            "Enlace": st.column_config.LinkColumn("Enlace"),
                            "Fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY")
                        }
                    )
                    
                    csv = df_filtrado.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        "📥 Descargar CSV",
                        csv,
                        f"ayudas_subvenciones_{datetime.now().strftime('%Y%m%d')}.csv",
                        "text/csv",
                        key='download-csv'
                    )
                else:
                    st.warning("⚠️ No se encontraron resultados")
                    st.info("💡 **Sugerencias:**\n- Desactiva 'Solo ayudas/subvenciones'\n- Reduce palabras clave\n- Cambia a búsqueda no exacta\n- Amplía el rango de fechas")
            else:
                st.error("❌ No se obtuvieron resultados")

# ============= INFORMACIÓN =============
with st.expander("ℹ️ Ayuda y Guía de Uso"):
    st.markdown("""
    ### 🎯 Cómo usar esta aplicación
    
    #### 1. Configurar OpenAI (opcional pero recomendado)
    - Obtén tu API Key en: https://platform.openai.com/api-keys
    - Pégala en el campo "API Key de OpenAI"
    - Esto activará resúmenes inteligentes y búsqueda en lenguaje natural
    
    #### 2. Seleccionar fuentes
    - **Feed del día**: Publicaciones más recientes (rápido)
    - **Histórico**: Busca en fechas anteriores (más lento)
    
    #### 3. Búsqueda inteligente con IA
    - Activa "Búsqueda inteligente con IA"
    - Describe en lenguaje natural lo que buscas
    - Ejemplo: "Busco financiación para startups tecnológicas en Andalucía"
    - La IA convertirá tu consulta en palabras clave óptimas
    
    #### 4. Búsqueda tradicional (sin IA)
    - Introduce palabras clave separadas por comas
    - Ejemplo: "feder, turismo, pyme"
    - Activa "búsqueda exacta" para mayor precisión
    
    #### 5. Generar resúmenes con IA
    - Después de buscar, activa "Generar resúmenes con IA"
    - Selecciona cuántas ayudas resumir (máx. 20)
    - La IA extraerá: tipo, beneficiarios, cuantía, plazo y resumen
    
    ### 💰 Costos de OpenAI
    
    - **gpt-4o-mini**: ~$0.001 por resumen (recomendado)
    - **gpt-4o**: ~$0.003 por resumen (más potente)
    - Resumir 10 ayudas: ~$0.01-0.03 USD
    
    ### 🔍 Tipos de búsqueda
    
    **Exacta** (activada):
    - "feder" encuentra: "FEDER", "Feder"
    - NO encuentra: "federación", "confederación"
    
    **Normal** (desactivada):
    - "feder" encuentra todo lo anterior
    
    ### 📊 Fuentes oficiales
    
    - **BOJA**: https://www.juntadeandalucia.es/boja
    - **BOE**: https://www.boe.es
    - **API BOE**: https://www.boe.es/datosabiertos/
    """)

# Footer
st.markdown("---")
st.markdown("🤖 **Desarrollado con Streamlit + OpenAI** | 📅 Actualizado: Octubre 2025")

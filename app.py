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
        allowed_methods=["GET", "POST"]
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
    """Genera un resumen estructurado usando OpenAI API"""
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
    """Convierte una consulta en lenguaje natural a palabras clave específicas"""
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
                time.sleep(1)
        except requests.exceptions.HTTPError as e:
            break
        except requests.exceptions.RequestException as e:
            if intento < max_intentos - 1:
                time.sleep(1)
        except Exception as e:
            break
    
    return ""

def buscar_boja_feed(contenido_completo=False):
    """Busca en el feed principal de BOJA con filtrado mejorado"""
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
            
            # Filtro mejorado
            urls_excluir = [
                '/temas/',
                '/organismos/',
                '/servicios/',
                'juntadeandalucia.es/temas',
                'juntadeandalucia.es/organismos'
            ]
            
            if any(excluir in enlace for excluir in urls_excluir):
                continue
            
            if '/boja/' not in enlace and '/eboja/' not in enlace:
                continue
            
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

# ============= FUNCIONES EXHAUSTIVAS PARA BOJA HISTÓRICO =============

def extraer_secciones_boja(url_boletin):
    """Extrae todas las URLs de secciones del menú lateral derecho de un boletín"""
    secciones = []
    
    try:
        response = session.get(url_boletin, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Buscar enlaces a secciones (formato: /boja/YYYY/NNN/sXX)
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                # Identificar enlaces a secciones (contienen /sXX)
                if re.search(r'/s\d+', href):
                    # Construir URL completa
                    if href.startswith('/'):
                        url_completa = f"https://www.juntadeandalucia.es{href}"
                    elif href.startswith('http'):
                        url_completa = href
                    else:
                        url_completa = f"https://www.juntadeandalucia.es{href}"
                    
                    secciones.append({
                        'titulo': titulo,
                        'url': url_completa
                    })
            
            # Eliminar duplicados
            secciones_unicas = []
            urls_vistas = set()
            
            for seccion in secciones:
                if seccion['url'] not in urls_vistas:
                    secciones_unicas.append(seccion)
                    urls_vistas.add(seccion['url'])
            
            return secciones_unicas
    
    except Exception as e:
        return []


def extraer_documentos_de_seccion(url_seccion):
    """Extrae todos los documentos de una sección del BOJA"""
    documentos = []
    
    try:
        response = session.get(url_seccion, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Buscar enlaces a documentos individuales
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                # Identificar enlaces a documentos (números al final, no secciones)
                if re.search(r'/\d+$', href) and '/s' not in href:
                    # Construir URL completa
                    if href.startswith('/'):
                        url_completa = f"https://www.juntadeandalucia.es{href}"
                    elif href.startswith('http'):
                        url_completa = href
                    else:
                        url_completa = f"https://www.juntadeandalucia.es{href}"
                    
                    if titulo and len(titulo) > 10:
                        documentos.append({
                            'titulo': titulo,
                            'url': url_completa
                        })
            
            # Eliminar duplicados
            documentos_unicos = []
            urls_vistas = set()
            
            for doc in documentos:
                if doc['url'] not in urls_vistas:
                    documentos_unicos.append(doc)
                    urls_vistas.add(doc['url'])
            
            return documentos_unicos
    
    except Exception as e:
        return []


def buscar_en_boletin_completo(año, num_boletin, fecha_publicacion, contenido_completo=False):
    """Busca exhaustivamente en un boletín completo del BOJA recorriendo todas sus secciones"""
    
    resultados = []
    
    # URLs base del boletín
    urls_boletin = [
        f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
        f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
    ]
    
    url_boletin_valida = None
    
    # Encontrar la URL válida del boletín
    for url in urls_boletin:
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                url_boletin_valida = url
                break
        except:
            continue
    
    if not url_boletin_valida:
        return []
    
    # Paso 1: Extraer todas las secciones del menú lateral
    secciones = extraer_secciones_boja(url_boletin_valida)
    
    if len(secciones) > 0:
        # Paso 2: Recorrer cada sección
        for seccion in secciones:
            documentos = extraer_documentos_de_seccion(seccion['url'])
            
            # Paso 3: Extraer contenido de cada documento
            for doc in documentos:
                texto_completo = ""
                
                if contenido_completo:
                    texto_completo = extraer_contenido_completo(doc['url'])
                
                resultados.append({
                    'Boletín': 'BOJA',
                    'Título': doc['titulo'],
                    'Resumen': f"BOJA núm. {num_boletin} de {año} - Sección: {seccion['titulo']}",
                    'Contenido_Completo': texto_completo,
                    'Enlace': doc['url'],
                    'Fecha': fecha_publicacion,
                    'Seccion': seccion['titulo'],
                    'Numero_Boletin': num_boletin
                })
            
            time.sleep(0.2)
    
    else:
        # Si no hay secciones, buscar documentos directamente en el índice
        try:
            response = session.get(url_boletin_valida, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                if re.search(r'/\d+$', href) and titulo and len(titulo) > 10:
                    if href.startswith('/'):
                        href_completo = f"https://www.juntadeandalucia.es{href}"
                    elif href.startswith('http'):
                        href_completo = href
                    else:
                        href_completo = f"{url_boletin_valida.rstrip('/')}/{href}"
                    
                    texto_completo = ""
                    if contenido_completo:
                        texto_completo = extraer_contenido_completo(href_completo)
                    
                    resultados.append({
                        'Boletín': 'BOJA',
                        'Título': titulo,
                        'Resumen': f'BOJA núm. {num_boletin} de {año}',
                        'Contenido_Completo': texto_completo,
                        'Enlace': href_completo,
                        'Fecha': fecha_publicacion,
                        'Seccion': 'Principal',
                        'Numero_Boletin': num_boletin
                    })
        
        except Exception as e:
            pass
    
    return resultados


def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Busca en BOJA histórico fecha por fecha, recorriendo todas las secciones"""
    
    # Calcular si son fechas antiguas
    dias_antiguedad = (datetime.now() - fecha_fin).days
    
    if dias_antiguedad <= 30:
        st.info("🔍 Fechas recientes detectadas. Usando feed RSS filtrado...")
        return buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo)
    else:
        st.info(f"🔍 Búsqueda histórica exhaustiva activada ({dias_antiguedad} días de antigüedad)")
        return buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo)


def buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo=False):
    """Usa el feed RSS y filtra por fechas (solo para fechas recientes)"""
    
    resultados_feed = buscar_boja_feed(contenido_completo=False)
    
    if not resultados_feed:
        return []
    
    df_feed = pd.DataFrame(resultados_feed)
    
    fecha_inicio_pd = pd.to_datetime(fecha_inicio)
    fecha_fin_pd = pd.to_datetime(fecha_fin)
    
    if 'Fecha' in df_feed.columns:
        mascara_fechas = (df_feed['Fecha'] >= fecha_inicio_pd) & (df_feed['Fecha'] <= fecha_fin_pd)
        df_filtrado = df_feed[mascara_fechas]
    else:
        df_filtrado = df_feed
    
    st.info(f"📊 Encontrados {len(df_filtrado)} documentos del BOJA")
    
    resultados = df_filtrado.to_dict('records')
    
    if contenido_completo and len(resultados) > 0:
        for resultado in resultados:
            if resultado['Enlace']:
                resultado['Contenido_Completo'] = extraer_contenido_completo(resultado['Enlace'])
            time.sleep(0.3)
    
    return resultados


def buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda exhaustiva del BOJA histórico: fecha por fecha, sección por sección"""
    
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    st.info("🔄 Iniciando búsqueda exhaustiva: revisando cada fecha, entrando en cada sección...")
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias}) - Buscando boletín...")
        
        año = fecha_actual.year
        mes = fecha_actual.month
        dia = fecha_actual.day
        
        # Estimación del número de boletín basado en la fecha
        boletines_por_mes = {
            1: 0, 2: 20, 3: 40, 4: 60, 5: 80, 6: 100,
            7: 120, 8: 140, 9: 160, 10: 180, 11: 200, 12: 220
        }
        
        num_base = boletines_por_mes.get(mes, 0)
        num_dia = int((dia / 30) * 22)
        num_boletin_estimado = num_base + num_dia
        
        encontrado = False
        
        # Probar un rango de números de boletín
        for offset in range(-20, 21):
            num_boletin = max(1, min(250, num_boletin_estimado + offset))
            
            urls_boletin = [
                f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
                f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
            ]
            
            for url_boletin in urls_boletin:
                try:
                    response = session.get(url_boletin, timeout=10)
                    
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        texto_pagina = soup.get_text().lower()
                        
                        # Verificar si la página corresponde a la fecha buscada
                        fecha_formatos = [
                            fecha_actual.strftime('%d/%m/%Y'),
                            fecha_actual.strftime('%d-%m-%Y'),
                            f"{dia} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][mes-1]} de {año}".lower()
                        ]
                        
                        pagina_correcta = any(fecha in texto_pagina for fecha in fecha_formatos)
                        
                        if pagina_correcta:
                            progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} - ✅ BOJA {num_boletin} encontrado. Recorriendo secciones...")
                            
                            # Buscar exhaustivamente en este boletín
                            docs_boletin = buscar_en_boletin_completo(
                                año, 
                                num_boletin,
                                pd.to_datetime(fecha_actual),
                                contenido_completo
                            )
                            
                            if docs_boletin:
                                resultados.extend(docs_boletin)
                                st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_boletin)} documentos extraídos del BOJA {num_boletin}")
                                encontrado = True
                                break
                
                except:
                    continue
            
            if encontrado:
                break
        
        if not encontrado:
            st.warning(f"⚠️ {fecha_actual.strftime('%d/%m/%Y')}: No se encontró boletín para esta fecha")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.3)
    
    progress_bar.empty()
    progress_text.empty()
    
    st.success(f"✅ Búsqueda histórica completada: {len(resultados)} documentos encontrados en total")
    
    return resultados

# ============= FUNCIONES DE DIAGNÓSTICO =============

def probar_boja_especifico():
    """Prueba exhaustiva del BOJA específico"""
    
    st.subheader("🧪 Prueba Exhaustiva de BOJA")
    
    col1, col2 = st.columns(2)
    
    with col1:
        año_prueba = st.number_input("Año:", min_value=2000, max_value=2025, value=2022)
    
    with col2:
        num_boletin_prueba = st.number_input("Número de boletín:", min_value=1, max_value=250, value=82)
    
    palabra_buscar = st.text_input("Palabra a buscar:", "FEDER")
    
    if st.button("🔍 Buscar exhaustivamente", type="primary"):
        with st.spinner(f"Buscando en BOJA {num_boletin_prueba}/{año_prueba}..."):
            
            resultados = buscar_en_boletin_completo(
                año_prueba, 
                num_boletin_prueba,
                pd.to_datetime(datetime.now()),
                contenido_completo=True
            )
            
            if resultados:
                st.success(f"✅ Encontrados {len(resultados)} documentos")
                
                # Contar por sección
                df_temp = pd.DataFrame(resultados)
                if 'Seccion' in df_temp.columns:
                    st.write("**📂 Documentos por sección:**")
                    secciones_count = df_temp['Seccion'].value_counts()
                    for seccion, count in secciones_count.items():
                        st.write(f"- {seccion}: {count} documentos")
                
                st.markdown("---")
                
                # Buscar palabra
                docs_con_palabra = []
                
                for doc in resultados:
                    texto_busqueda = f"{doc['Título']} {doc['Resumen']} {doc.get('Contenido_Completo', '')}".lower()
                    
                    if palabra_buscar.lower() in texto_busqueda:
                        num_apariciones = texto_busqueda.count(palabra_buscar.lower())
                        docs_con_palabra.append({
                            **doc,
                            'apariciones': num_apariciones
                        })
                
                st.success(f"🎯 Documentos con '{palabra_buscar}': **{len(docs_con_palabra)}**")
                
                if len(docs_con_palabra) > 0:
                    for doc in docs_con_palabra:
                        with st.expander(f"📄 {doc['Título'][:80]} ({doc['apariciones']} apariciones)"):
                            st.write(f"**Sección:** {doc.get('Seccion', 'N/A')}")
                            st.markdown(f"[🔗 Ver documento]({doc['Enlace']})")
                            
                            contenido = doc.get('Contenido_Completo', '')
                            if contenido:
                                idx_palabra = contenido.lower().find(palabra_buscar.lower())
                                if idx_palabra != -1:
                                    inicio = max(0, idx_palabra - 200)
                                    fin = min(len(contenido), idx_palabra + 200)
                                    extracto = contenido[inicio:fin]
                                    st.info(f"...{extracto}...")
                
                if st.checkbox("Ver todos los documentos"):
                    st.dataframe(df_temp[['Título', 'Seccion', 'Enlace']])
            
            else:
                st.error(f"❌ No se pudo acceder al BOJA {num_boletin_prueba}/{año_prueba}")


def diagnosticar_boja():
    """Diagnóstico de acceso al BOJA"""
    st.subheader("🔧 Diagnóstico de Acceso")
    
    fecha_prueba = st.date_input("Fecha:", datetime.now() - timedelta(days=7))
    
    if st.button("🚀 Ejecutar diagnóstico"):
        año = fecha_prueba.year
        
        urls_prueba = [
            ("Feed RSS", "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"),
            ("BOJA 2022/82", "https://www.juntadeandalucia.es/boja/2022/82/"),
            ("e-BOJA 2022/82", "https://www.juntadeandalucia.es/eboja/2022/82/"),
        ]
        
        for nombre, url in urls_prueba:
            try:
                response = session.get(url, timeout=10)
                st.write(f"**{nombre}**: Status {response.status_code}")
                
                if response.status_code == 200:
                    st.success("✅ Acceso correcto")
                else:
                    st.error("❌ Error de acceso")
            except Exception as e:
                st.error(f"❌ {str(e)[:100]}")


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
    
    # Modo diagnóstico
    modo_diagnostico = st.checkbox("🔧 Modo diagnóstico", value=False)
    
    if modo_diagnostico:
        st.markdown("---")
        diagnosticar_boja()
        st.markdown("---")
        probar_boja_especifico()
        st.markdown("---")
    
    # IA
    st.subheader("🤖 Inteligencia Artificial")
    
    usar_ia = st.checkbox(
        "Activar resúmenes con IA",
        value=False,
        help="Genera resúmenes automáticos estructurados"
    )
    
    api_key_openai = None
    modelo_openai = None
    
    if usar_ia:
        api_key_default = ""
        try:
            api_key_default = st.secrets.get("openai", {}).get("api_key", "")
        except:
            pass
        
        if api_key_default:
            api_key_openai = api_key_default
            st.success("✅ API Key cargada")
        else:
            api_key_openai = st.text_input(
                "🔑 API Key de OpenAI:",
                type="password",
                help="https://platform.openai.com/api-keys"
            )
        
        if api_key_openai:
            modelo_openai = st.selectbox(
                "Modelo:",
                ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
            )
            st.info(f"💰 ~$0.001 por resumen")
    
    # Búsqueda inteligente
    st.markdown("---")
    busqueda_inteligente = st.checkbox(
        "🔮 Búsqueda inteligente con IA",
        value=False
    )
    
    palabras_clave = ""
    
    if busqueda_inteligente and api_key_openai:
        consulta_natural = st.text_area(
            "Describe lo que buscas:",
            placeholder="Ej: ayudas turismo rural Andalucía fondos europeos",
            height=100
        )
        
        if st.button("🔮 Generar palabras clave"):
            if consulta_natural:
                palabras_generadas = busqueda_inteligente_openai(
                    consulta_natural, 
                    api_key_openai, 
                    modelo_openai or "gpt-4o-mini"
                )
                st.success(f"✅ **{palabras_generadas}**")
                palabras_clave = palabras_generadas
    
    st.markdown("---")
    
    # Fuentes
    st.subheader("📰 Fuentes de datos")
    
    usar_boja = st.checkbox("BOJA (Feed del día)", value=True)
    usar_boe = st.checkbox("BOE (RSS del día)", value=True)
    
    st.markdown("**📅 Búsqueda histórica**")
    usar_boja_hist = st.checkbox(
        "BOJA (Histórico exhaustivo)", 
        value=False,
        help="Busca fecha por fecha, sección por sección"
    )
    usar_boe_hist = st.checkbox(
        "BOE (Histórico - API oficial)", 
        value=False
    )
    
    fecha_desde = None
    fecha_hasta = None
    
    if usar_boja_hist or usar_boe_hist:
        st.markdown("**Rango de fechas:**")
        col1, col2 = st.columns(2)
        
        fecha_desde = col1.date_input(
            "Desde",
            datetime.now() - timedelta(days=7),
            max_value=datetime.now()
        )
        
        fecha_hasta = col2.date_input(
            "Hasta",
            datetime.now(),
            max_value=datetime.now()
        )
        
        if fecha_desde > fecha_hasta:
            st.error("⚠️ Fecha incorrecta")
        
        dias_rango = (fecha_hasta - fecha_desde).days
        if dias_rango > 90:
            st.warning(f"⏱️ Rango amplio: {dias_rango} días")
    
    st.markdown("---")
    
    # Opciones
    st.subheader("🔍 Opciones")
    
    contenido_completo = st.checkbox(
        "🔥 Buscar en contenido completo",
        value=False,
        help="⚠️ LENTO pero exhaustivo"
    )
    
    if contenido_completo:
        st.warning("⏱️ Puede tardar mucho tiempo")
    
    st.markdown("---")
    
    # Filtros
    st.subheader("🎯 Filtros")
    solo_ayudas = st.checkbox("Solo ayudas/subvenciones", value=True)
    
    if not busqueda_inteligente:
        palabras_clave = st.text_input(
            "Palabras clave (separadas por coma)", 
            "",
            help="Ej: feder, turismo, pyme"
        )
    
    busqueda_exacta = st.checkbox(
        "Búsqueda exacta",
        value=True
    )

# Botón de búsqueda
if st.button("🚀 Buscar", type="primary"):
    if (usar_boja_hist or usar_boe_hist) and fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        st.error("❌ Corrige fechas")
    else:
        with st.spinner("Buscando..."):
            todos_resultados = []
            
            if usar_boja:
                with st.status("🔎 BOJA (feed)..."):
                    todos_resultados.extend(buscar_boja_feed(contenido_completo))
            
            if usar_boe:
                with st.status("🔎 BOE (RSS)..."):
                    todos_resultados.extend(buscar_boe_rss(contenido_completo))
            
            if usar_boja_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 BOJA histórico exhaustivo..."):
                    todos_resultados.extend(
                        buscar_boja_historico(
                            datetime.combine(fecha_desde, datetime.min.time()),
                            datetime.combine(fecha_hasta, datetime.min.time()),
                            contenido_completo
                        )
                    )
            
            if usar_boe_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 BOE histórico..."):
                    todos_resultados.extend(
                        buscar_boe_historico_api(
                            datetime.combine(fecha_desde, datetime.min.time()),
                            datetime.combine(fecha_hasta, datetime.min.time()),
                            contenido_completo
                        )
                    )
            
            if todos_resultados:
                df = pd.DataFrame(todos_resultados)
                df = df.drop_duplicates(subset=['Enlace'], keep='first')
                
                lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
                df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
                df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
                
                if len(df_filtrado) > 0:
                    st.success(f"✅ **{len(df_filtrado)} resultados** (de {len(df)} totales)")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total", len(df_filtrado))
                    col2.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
                    col3.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
                    
                    # Resúmenes con IA
                    if usar_ia and api_key_openai:
                        st.markdown("---")
                        st.subheader("🤖 Resúmenes con IA")
                        
                        max_resumenes = st.slider(
                            "Número a resumir:",
                            1,
                            min(20, len(df_filtrado)),
                            min(5, len(df_filtrado))
                        )
                        
                        if st.button("📝 Generar resúmenes"):
                            resumenes = []
                            
                            for idx, (_, row) in enumerate(df_filtrado.head(max_resumenes).iterrows()):
                                texto_completo = row.get('Contenido_Completo', '')
                                if not texto_completo and row['Enlace']:
                                    texto_completo = extraer_contenido_completo(row['Enlace'])
                                
                                texto_para_ia = f"{row['Título']}\n\n{row['Resumen']}\n\n{texto_completo[:6000]}"
                                
                                resumen_ia = resumir_con_openai(
                                    texto_para_ia, 
                                    api_key_openai, 
                                    modelo_openai
                                )
                                
                                resumenes.append({**row.to_dict(), **resumen_ia})
                            
                            for res in resumenes:
                                with st.expander(f"📄 {res['Título'][:100]}"):
                                    st.write(f"**Tipo:** {res.get('tipo')}")
                                    st.write(f"**Beneficiarios:** {res.get('beneficiarios')}")
                                    st.write(f"**Cuantía:** {res.get('cuantia')}")
                                    st.write(f"**Plazo:** {res.get('plazo')}")
                                    st.info(res.get('resumen'))
                    
                    # Tabla
                    st.markdown("---")
                    st.subheader("📊 Resultados")
                    st.dataframe(df_filtrado, use_container_width=True, height=600)
                    
                    csv = df_filtrado.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        "📥 Descargar CSV",
                        csv,
                        f"resultados_{datetime.now().strftime('%Y%m%d')}.csv",
                        "text/csv"
                    )
                else:
                    st.warning("⚠️ Sin resultados")
            else:
                st.error("❌ No se obtuvieron resultados")

# Ayuda
with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### 🎯 Búsqueda Histórica Exhaustiva del BOJA
    
    La búsqueda histórica ahora:
    - ✅ Va **fecha por fecha** en el rango especificado
    - ✅ Encuentra el **boletín del BOJA** de cada fecha
    - ✅ Entra en **todas las secciones** del menú lateral derecho
    - ✅ Extrae **todos los documentos** de cada sección
    - ✅ Descarga el **contenido completo** si se activa la opción
    - ✅ Busca la **palabra clave** en todo el contenido
    
    ### Ejemplo: BOJA 82/2022 (3 mayo 2022)
    - Secciones: Disposiciones generales, Autoridades y personal, Otras disposiciones, etc.
    - La búsqueda recorre **cada sección** y extrae todos los documentos
    - Si buscas "FEDER", encontrará todos los docs que contengan esa palabra
    
    ### ⚠️ Importante
    - La búsqueda histórica es **muy exhaustiva** y puede tardar
    - Para rangos grandes (>30 días), activa solo si es necesario
    - El contenido completo hace la búsqueda aún más lenta pero más precisa
    """)

st.markdown("---")
st.markdown("🤖 **Versión 3.0** - Búsqueda exhaustiva sección por sección")

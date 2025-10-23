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
    """Extrae el texto completo de una página con logging mejorado"""
    for intento in range(max_intentos):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for element in soup(["script", "style", "nav", "header", "footer", "iframe"]):
                element.decompose()
            
            contenido = soup.get_text(separator=' ', strip=True)
            
            # Log del tamaño del contenido extraído
            if contenido:
                # Retornar el contenido sin sleep para acelerar
                return contenido
            else:
                return ""
            
        except requests.exceptions.Timeout:
            if intento < max_intentos - 1:
                time.sleep(0.5)
        except requests.exceptions.HTTPError as e:
            break
        except requests.exceptions.RequestException as e:
            if intento < max_intentos - 1:
                time.sleep(0.5)
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
    """Extrae todas las URLs de secciones del menú lateral derecho"""
    secciones = []
    
    try:
        response = session.get(url_boletin, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                if re.search(r'/s\d+', href):
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
    """Extrae todos los documentos de una sección"""
    documentos = []
    
    try:
        response = session.get(url_seccion, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                if re.search(r'/\d+$', href) and '/s' not in href:
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
            
            documentos_unicos = []
            urls_vistas = set()
            
            for doc in documentos:
                if doc['url'] not in urls_vistas:
                    documentos_unicos.append(doc)
                    urls_vistas.add(doc['url'])
            
            return documentos_unicos
    
    except Exception as e:
        return []


def buscar_en_boletin_completo(año, num_boletin, fecha_publicacion, contenido_completo=False, progress_container=None):
    """Busca exhaustivamente en un boletín completo con logging detallado"""
    
    resultados = []
    
    urls_boletin = [
        f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
        f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
    ]
    
    url_boletin_valida = None
    
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
    
    # Extraer secciones
    secciones = extraer_secciones_boja(url_boletin_valida)
    
    if len(secciones) > 0:
        for seccion in secciones:
            documentos = extraer_documentos_de_seccion(seccion['url'])
            
            # CRUCIAL: Descargar contenido de cada documento
            for idx, doc in enumerate(documentos):
                texto_completo = ""
                
                # SIEMPRE descargar contenido si se solicita
                if contenido_completo:
                    if progress_container:
                        progress_container.text(f"    📄 Descargando contenido: {doc['titulo'][:50]}... ({idx+1}/{len(documentos)})")
                    
                    texto_completo = extraer_contenido_completo(doc['url'])
                    
                    # Verificar que se descargó contenido
                    if texto_completo:
                        longitud = len(texto_completo)
                        if progress_container:
                            progress_container.text(f"    ✅ Contenido descargado: {longitud} caracteres")
                    else:
                        if progress_container:
                            progress_container.text(f"    ⚠️ Sin contenido para: {doc['titulo'][:50]}")
                
                resultados.append({
                    'Boletín': 'BOJA',
                    'Título': doc['titulo'],
                    'Resumen': f"BOJA núm. {num_boletin} de {año} - Sección: {seccion['titulo']}",
                    'Contenido_Completo': texto_completo,
                    'Enlace': doc['url'],
                    'Fecha': fecha_publicacion,
                    'Seccion': seccion['titulo'],
                    'Numero_Boletin': num_boletin,
                    'Tiene_Contenido': len(texto_completo) > 0
                })
            
            time.sleep(0.2)
    
    else:
        # Fallback: documentos del índice principal
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
                        'Numero_Boletin': num_boletin,
                        'Tiene_Contenido': len(texto_completo) > 0
                    })
        
        except Exception as e:
            pass
    
    return resultados


def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Busca en BOJA histórico con logging detallado"""
    
    dias_antiguedad = (datetime.now() - fecha_fin).days
    
    if dias_antiguedad <= 30:
        st.info("🔍 Fechas recientes. Usando feed RSS...")
        return buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo)
    else:
        st.info(f"🔍 Búsqueda exhaustiva activada ({dias_antiguedad} días)")
        return buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo)


def buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo=False):
    """Usa feed RSS filtrado"""
    
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
    
    st.info(f"📊 {len(df_filtrado)} documentos del BOJA")
    
    resultados = df_filtrado.to_dict('records')
    
    if contenido_completo and len(resultados) > 0:
        for resultado in resultados:
            if resultado['Enlace']:
                resultado['Contenido_Completo'] = extraer_contenido_completo(resultado['Enlace'])
            time.sleep(0.3)
    
    return resultados


def buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda exhaustiva con descarga de contenido garantizada"""
    
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_detail = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    st.info("🔄 Búsqueda exhaustiva: cada fecha, cada sección, cada documento...")
    
    # Advertencia sobre descarga de contenido
    if contenido_completo:
        st.warning("⚠️ DESCARGA DE CONTENIDO ACTIVADA - Esto tardará más tiempo pero buscará en el contenido completo")
    else:
        st.info("ℹ️ Solo buscando en títulos y resúmenes. Activa 'Buscar en contenido completo' para buscar dentro de los documentos")
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
        año = fecha_actual.year
        mes = fecha_actual.month
        dia = fecha_actual.day
        
        boletines_por_mes = {
            1: 0, 2: 20, 3: 40, 4: 60, 5: 80, 6: 100,
            7: 120, 8: 140, 9: 160, 10: 180, 11: 200, 12: 220
        }
        
        num_base = boletines_por_mes.get(mes, 0)
        num_dia = int((dia / 30) * 22)
        num_boletin_estimado = num_base + num_dia
        
        encontrado = False
        
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
                        
                        fecha_formatos = [
                            fecha_actual.strftime('%d/%m/%Y'),
                            fecha_actual.strftime('%d-%m-%Y'),
                            f"{dia} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][mes-1]} de {año}".lower()
                        ]
                        
                        pagina_correcta = any(fecha in texto_pagina for fecha in fecha_formatos)
                        
                        if pagina_correcta:
                            progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} - ✅ BOJA {num_boletin}")
                            progress_detail.text(f"    🔍 Extrayendo documentos y descargando contenido...")
                            
                            docs_boletin = buscar_en_boletin_completo(
                                año, 
                                num_boletin,
                                pd.to_datetime(fecha_actual),
                                contenido_completo,
                                progress_detail
                            )
                            
                            if docs_boletin:
                                # Contar cuántos tienen contenido
                                con_contenido = sum(1 for d in docs_boletin if d.get('Tiene_Contenido', False))
                                
                                resultados.extend(docs_boletin)
                                
                                if contenido_completo:
                                    st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_boletin)} docs extraídos ({con_contenido} con contenido descargado)")
                                else:
                                    st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_boletin)} docs extraídos (sin descargar contenido)")
                                
                                encontrado = True
                                break
                
                except:
                    continue
            
            if encontrado:
                break
        
        if not encontrado:
            st.warning(f"⚠️ {fecha_actual.strftime('%d/%m/%Y')}: No se encontró boletín")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.2)
    
    progress_bar.empty()
    progress_text.empty()
    progress_detail.empty()
    
    # Estadísticas finales
    if resultados:
        con_contenido = sum(1 for d in resultados if d.get('Tiene_Contenido', False))
        st.success(f"✅ Búsqueda completada: {len(resultados)} documentos ({con_contenido} con contenido completo descargado)")
    else:
        st.warning("⚠️ No se encontraron documentos en el rango de fechas")
    
    return resultados

# ============= FUNCIONES DE DIAGNÓSTICO =============

def probar_boja_especifico():
    """Prueba exhaustiva"""
    
    st.subheader("🧪 Prueba Exhaustiva")
    
    col1, col2 = st.columns(2)
    
    with col1:
        año_prueba = st.number_input("Año:", min_value=2000, max_value=2025, value=2022)
    
    with col2:
        num_boletin_prueba = st.number_input("Boletín:", min_value=1, max_value=250, value=82)
    
    palabra_buscar = st.text_input("Palabra:", "FEDER")
    
    if st.button("🔍 Buscar exhaustivamente", type="primary"):
        with st.spinner(f"Buscando en BOJA {num_boletin_prueba}/{año_prueba}..."):
            
            progress_detail = st.empty()
            
            resultados = buscar_en_boletin_completo(
                año_prueba, 
                num_boletin_prueba,
                pd.to_datetime(datetime.now()),
                contenido_completo=True,
                progress_container=progress_detail
            )
            
            progress_detail.empty()
            
            if resultados:
                st.success(f"✅ {len(resultados)} documentos")
                
                df_temp = pd.DataFrame(resultados)
                if 'Seccion' in df_temp.columns:
                    st.write("**📂 Por sección:**")
                    for seccion, count in df_temp['Seccion'].value_counts().items():
                        st.write(f"- {seccion}: {count}")
                
                st.markdown("---")
                
                docs_con_palabra = []
                
                for doc in resultados:
                    texto_busqueda = f"{doc['Título']} {doc['Resumen']} {doc.get('Contenido_Completo', '')}".lower()
                    
                    if palabra_buscar.lower() in texto_busqueda:
                        num_apariciones = texto_busqueda.count(palabra_buscar.lower())
                        docs_con_palabra.append({
                            **doc,
                            'apariciones': num_apariciones
                        })
                
                st.success(f"🎯 Docs con '{palabra_buscar}': **{len(docs_con_palabra)}**")
                
                if len(docs_con_palabra) > 0:
                    for doc in docs_con_palabra:
                        with st.expander(f"📄 {doc['Título'][:80]} ({doc['apariciones']} veces)"):
                            st.write(f"**Sección:** {doc.get('Seccion')}")
                            st.markdown(f"[🔗 Ver]({doc['Enlace']})")
                            
                            contenido = doc.get('Contenido_Completo', '')
                            if contenido:
                                idx_palabra = contenido.lower().find(palabra_buscar.lower())
                                if idx_palabra != -1:
                                    extracto = contenido[max(0, idx_palabra-200):min(len(contenido), idx_palabra+200)]
                                    st.info(f"...{extracto}...")


def diagnosticar_boja():
    """Diagnóstico"""
    st.subheader("🔧 Diagnóstico")
    
    fecha_prueba = st.date_input("Fecha:", datetime.now() - timedelta(days=7))
    
    if st.button("🚀 Ejecutar"):
        urls_prueba = [
            ("Feed RSS", "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"),
            ("BOJA 2022/82", "https://www.juntadeandalucia.es/boja/2022/82/"),
        ]
        
        for nombre, url in urls_prueba:
            try:
                response = session.get(url, timeout=10)
                st.write(f"**{nombre}**: {response.status_code}")
                if response.status_code == 200:
                    st.success("✅ OK")
            except Exception as e:
                st.error(f"❌ {str(e)[:100]}")


def filtrar_resultados(df, palabras_clave, solo_ayudas=True, busqueda_exacta=False):
    """Filtra con logging mejorado"""
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
    
    # Log: mostrar cuántos tienen contenido
    if 'Tiene_Contenido' in df.columns:
        con_contenido = df['Tiene_Contenido'].sum()
        st.info(f"📊 De {len(df)} documentos, {con_contenido} tienen contenido completo descargado")
    
    if solo_ayudas:
        patron_ayudas = r'\b(ayuda|ayudas|subvención|subvencion|subvenciones|convocatoria|convocatorias|bases\s+reguladoras)\b'
        mascara_ayudas = df['_texto_busqueda'].str.contains(
            patron_ayudas, 
            case=False, 
            regex=True, 
            na=False
        )
        df = df[mascara_ayudas]
        st.info(f"📊 Después de filtrar 'ayudas/subvenciones': {len(df)} documentos")
    
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
                
                encontrados = mascara_palabra.sum()
                st.info(f"🔍 Palabra '{palabra}': encontrada en {encontrados} documentos")
        
        df = df[mascara_final]
    
    df = df.drop(columns=['_texto_busqueda'])
    
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador Inteligente de Ayudas y Subvenciones")
st.markdown("**BOJA** (Junta de Andalucía) + **BOE** (Estado)")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    modo_diagnostico = st.checkbox("🔧 Modo diagnóstico", value=False)
    
    if modo_diagnostico:
        st.markdown("---")
        diagnosticar_boja()
        st.markdown("---")
        probar_boja_especifico()
        st.markdown("---")
    
    st.subheader("🤖 IA")
    
    usar_ia = st.checkbox("Activar resúmenes con IA", value=False)
    
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
            api_key_openai = st.text_input("🔑 API Key:", type="password")
        
        if api_key_openai:
            modelo_openai = st.selectbox("Modelo:", ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"])
    
    st.markdown("---")
    busqueda_inteligente = st.checkbox("🔮 Búsqueda inteligente", value=False)
    
    palabras_clave = ""
    
    if busqueda_inteligente and api_key_openai:
        consulta_natural = st.text_area("Describe:", placeholder="Ej: ayudas turismo", height=100)
        
        if st.button("🔮 Generar"):
            if consulta_natural:
                palabras_generadas = busqueda_inteligente_openai(consulta_natural, api_key_openai, modelo_openai or "gpt-4o-mini")
                st.success(f"✅ **{palabras_generadas}**")
                palabras_clave = palabras_generadas
    
    st.markdown("---")
    st.subheader("📰 Fuentes")
    
    usar_boja = st.checkbox("BOJA (Feed)", value=True)
    usar_boe = st.checkbox("BOE (RSS)", value=True)
    
    st.markdown("**📅 Histórico**")
    usar_boja_hist = st.checkbox("BOJA (Exhaustivo)", value=False)
    usar_boe_hist = st.checkbox("BOE (API)", value=False)
    
    fecha_desde = None
    fecha_hasta = None
    
    if usar_boja_hist or usar_boe_hist:
        col1, col2 = st.columns(2)
        
        fecha_desde = col1.date_input("Desde", datetime.now() - timedelta(days=7))
        fecha_hasta = col2.date_input("Hasta", datetime.now())
        
        if fecha_desde > fecha_hasta:
            st.error("⚠️ Fecha incorrecta")
    
    st.markdown("---")
    st.subheader("🔍 Opciones")
    
    contenido_completo = st.checkbox(
        "🔥 Buscar en contenido completo",
        value=False,
        help="ACTIVAR para buscar dentro de los documentos"
    )
    
    if contenido_completo:
        st.warning("⏱️ Descargará el contenido de cada documento")
    else:
        st.info("ℹ️ Solo buscará en títulos/resúmenes")
    
    st.markdown("---")
    st.subheader("🎯 Filtros")
    solo_ayudas = st.checkbox("Solo ayudas/subvenciones", value=True)
    
    if not busqueda_inteligente:
        palabras_clave = st.text_input("Palabras clave:", "", help="Ej: feder, turismo")
    
    busqueda_exacta = st.checkbox("Búsqueda exacta", value=True)

# Búsqueda
if st.button("🚀 Buscar", type="primary"):
    if (usar_boja_hist or usar_boe_hist) and fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        st.error("❌ Corrige fechas")
    else:
        todos_resultados = []
        
        if usar_boja:
            with st.status("🔎 BOJA..."):
                todos_resultados.extend(buscar_boja_feed(contenido_completo))
        
        if usar_boe:
            with st.status("🔎 BOE..."):
                todos_resultados.extend(buscar_boe_rss(contenido_completo))
        
        if usar_boja_hist and fecha_desde and fecha_hasta:
            todos_resultados.extend(
                buscar_boja_historico(
                    datetime.combine(fecha_desde, datetime.min.time()),
                    datetime.combine(fecha_hasta, datetime.min.time()),
                    contenido_completo
                )
            )
        
        if usar_boe_hist and fecha_desde and fecha_hasta:
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
            
            st.info(f"📊 Total de documentos extraídos: {len(df)}")
            
            lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
            
            if lista_palabras:
                st.info(f"🔍 Filtrando por: {', '.join(lista_palabras)}")
            
            df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
            df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
            
            if len(df_filtrado) > 0:
                st.success(f"✅ **{len(df_filtrado)} resultados**")
                
                col1, col2, col3 = st.columns(3)
                col1.metric("Total", len(df_filtrado))
                col2.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
                col3.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
                
                st.markdown("---")
                st.subheader("📊 Resultados")
                st.dataframe(df_filtrado, use_container_width=True, height=600)
                
                csv = df_filtrado.to_csv(index=False, encoding='utf-8-sig')
                st.download_button("📥 Descargar CSV", csv, f"resultados_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
            else:
                st.warning("⚠️ Sin resultados con esos filtros")
                
                if not contenido_completo:
                    st.info("💡 Prueba activando 'Buscar en contenido completo' para buscar dentro de los documentos")
        else:
            st.error("❌ No se obtuvieron resultados")

with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### ⚠️ IMPORTANTE: Búsqueda en Contenido Completo
    
    Para que la búsqueda encuentre palabras **dentro de los documentos**:
    
    1. ✅ **Activa** "Buscar en contenido completo" en Opciones
    2. Esto descargará el texto completo de cada documento
    3. Entonces buscará tu palabra clave en TODO el contenido
    
    **Sin esta opción activada**, solo busca en:
    - Títulos de documentos
    - Resúmenes cortos
    
    ### Ejemplo
    - Sin contenido completo: Encuentra 74 docs pero no la palabra "FEDER"
    - CON contenido completo: Descarga los 74 docs y busca "FEDER" dentro de cada uno
    """)

st.markdown("---")
st.markdown("🤖 **Versión 3.1** - Descarga de contenido mejorada con logging detallado")

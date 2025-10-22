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

# ============= FUNCIONES DE EXTRACCIÓN DE INFORMACIÓN =============

def extraer_informacion_documento(titulo, resumen, contenido, palabras_clave):
    """Extrae información estructurada de un documento"""
    
    texto_completo = f"{titulo} {resumen} {contenido}".lower()
    
    info = {
        'tipo_documento': '',
        'organismo': '',
        'cuantia': '',
        'plazo_solicitud': '',
        'beneficiarios': '',
        'objeto': '',
        'contexto_palabras': []
    }
    
    # 1. Tipo de documento
    if re.search(r'\b(resolución|resolucion)\b', texto_completo):
        info['tipo_documento'] = 'Resolución'
    elif re.search(r'\b(orden)\b', texto_completo):
        info['tipo_documento'] = 'Orden'
    elif re.search(r'\b(decreto)\b', texto_completo):
        info['tipo_documento'] = 'Decreto'
    elif re.search(r'\b(convocatoria)\b', texto_completo):
        info['tipo_documento'] = 'Convocatoria'
    elif re.search(r'\b(bases reguladoras)\b', texto_completo):
        info['tipo_documento'] = 'Bases Reguladoras'
    else:
        info['tipo_documento'] = 'Documento Oficial'
    
    # 2. Organismo emisor
    organismos = [
        r'Consejería de [A-Za-záéíóúñÑ\s,]+',
        r'Dirección General de [A-Za-záéíóúñÑ\s,]+',
        r'Secretaría General de [A-Za-záéíóúñÑ\s,]+',
        r'Ministerio de [A-Za-záéíóúñÑ\s,]+',
    ]
    
    for patron in organismos:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['organismo'] = match.group(0).strip()
            break
    
    # 3. Cuantía
    patrones_cuantia = [
        r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
        r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*€',
        r'importe\s+(?:total\s+)?(?:de\s+)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
        r'cuantía\s+(?:de\s+)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
        r'presupuesto\s+(?:de\s+)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
    ]
    
    for patron in patrones_cuantia:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['cuantia'] = match.group(0).strip()
            break
    
    # 4. Plazo de solicitud
    patrones_plazo = [
        r'plazo\s+de\s+(?:presentación\s+de\s+)?solicitudes?[:\s]+([^.]{10,80})',
        r'hasta\s+el\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})',
        r'antes\s+del\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})',
        r'(\d{1,2})\s+días?\s+(?:hábiles|naturales)',
    ]
    
    for patron in patrones_plazo:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['plazo_solicitud'] = match.group(0).strip()
            break
    
    # 5. Beneficiarios
    patrones_beneficiarios = [
        r'beneficiarios?[:\s]+([^.]{10,100})',
        r'podrán\s+solicitar\s+([^.]{10,100})',
        r'destinadas?\s+a\s+([^.]{10,100})',
    ]
    
    for patron in patrones_beneficiarios:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['beneficiarios'] = match.group(1).strip()
            break
    
    # 6. Objeto
    patrones_objeto = [
        r'objeto[:\s]+([^.]{20,150})',
        r'finalidad[:\s]+([^.]{20,150})',
        r'destinadas?\s+a\s+([^.]{20,150})',
    ]
    
    for patron in patrones_objeto:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['objeto'] = match.group(1).strip()
            break
    
    # 7. Contexto de palabras clave
    for palabra in palabras_clave:
        palabra_lower = palabra.lower().strip()
        if palabra_lower in texto_completo:
            # Buscar todas las apariciones
            idx = 0
            while True:
                idx = texto_completo.find(palabra_lower, idx)
                if idx == -1:
                    break
                
                # Extraer contexto (200 caracteres antes y después)
                inicio = max(0, idx - 200)
                fin = min(len(contenido), idx + len(palabra_lower) + 200)
                
                contexto = contenido[inicio:fin] if contenido else texto_completo[inicio:fin]
                
                info['contexto_palabras'].append({
                    'palabra': palabra,
                    'contexto': f"...{contexto}..."
                })
                
                idx += len(palabra_lower)
                
                # Limitar a 3 contextos por palabra
                if len([c for c in info['contexto_palabras'] if c['palabra'] == palabra]) >= 3:
                    break
    
    return info

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
            
            if contenido:
                return contenido
            else:
                return ""
            
        except:
            if intento < max_intentos - 1:
                time.sleep(0.5)
    
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
            
            urls_excluir = ['/temas/', '/organismos/', '/servicios/']
            
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
            
    except Exception as e:
        st.error(f"Error BOJA: {e}")
    
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
            
    except Exception as e:
        st.error(f"Error BOE: {e}")
    
    return resultados

def buscar_boe_historico_api(fecha_inicio, fecha_fin, contenido_completo=False):
    """Busca en el BOE histórico"""
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"BOE {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
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
                                            'Resumen': f"Sección: {seccion.get('nombre', '')}",
                                            'Contenido_Completo': texto_completo,
                                            'Enlace': enlace,
                                            'Fecha': pd.to_datetime(fecha_actual)
                                        })
            
            time.sleep(0.3)
            
        except Exception as e:
            pass
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
    
    progress_bar.empty()
    progress_text.empty()
    
    return resultados

# ============= FUNCIONES BOJA HISTÓRICO =============

def extraer_secciones_boja(url_boletin):
    """Extrae secciones del BOJA"""
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
                    else:
                        url_completa = href
                    
                    secciones.append({'titulo': titulo, 'url': url_completa})
            
            secciones_unicas = []
            urls_vistas = set()
            
            for seccion in secciones:
                if seccion['url'] not in urls_vistas:
                    secciones_unicas.append(seccion)
                    urls_vistas.add(seccion['url'])
            
            return secciones_unicas
    except:
        return []


def extraer_documentos_de_seccion(url_seccion):
    """Extrae documentos de una sección"""
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
                    else:
                        url_completa = href
                    
                    if titulo and len(titulo) > 10:
                        documentos.append({'titulo': titulo, 'url': url_completa})
            
            return list({d['url']: d for d in documentos}.values())
    except:
        return []


def buscar_en_boletin_completo(año, num_boletin, fecha_publicacion, contenido_completo=False, progress_container=None):
    """Busca en un boletín completo"""
    
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
    
    secciones = extraer_secciones_boja(url_boletin_valida)
    
    if len(secciones) > 0:
        for seccion in secciones:
            documentos = extraer_documentos_de_seccion(seccion['url'])
            
            for idx, doc in enumerate(documentos):
                texto_completo = ""
                
                if contenido_completo:
                    if progress_container:
                        progress_container.text(f"    📄 {doc['titulo'][:50]}... ({idx+1}/{len(documentos)})")
                    
                    texto_completo = extraer_contenido_completo(doc['url'])
                
                resultados.append({
                    'Boletín': 'BOJA',
                    'Título': doc['titulo'],
                    'Resumen': f"BOJA {num_boletin}/{año} - {seccion['titulo']}",
                    'Contenido_Completo': texto_completo,
                    'Enlace': doc['url'],
                    'Fecha': fecha_publicacion,
                    'Seccion': seccion['titulo'],
                    'Numero_Boletin': num_boletin,
                    'Tiene_Contenido': len(texto_completo) > 0
                })
            
            time.sleep(0.2)
    
    return resultados


def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda histórica del BOJA"""
    
    dias_antiguedad = (datetime.now() - fecha_fin).days
    
    if dias_antiguedad <= 30:
        st.info("🔍 Fechas recientes. Usando feed RSS...")
        return buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo)
    else:
        st.info(f"🔍 Búsqueda exhaustiva ({dias_antiguedad} días)")
        return buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo)


def buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo=False):
    """Feed RSS filtrado"""
    
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
    
    resultados = df_filtrado.to_dict('records')
    
    if contenido_completo and len(resultados) > 0:
        for resultado in resultados:
            if resultado['Enlace']:
                resultado['Contenido_Completo'] = extraer_contenido_completo(resultado['Enlace'])
    
    return resultados


def buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda exhaustiva BOJA"""
    
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_detail = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    st.info("🔄 Búsqueda exhaustiva...")
    
    if contenido_completo:
        st.warning("⚠️ DESCARGA DE CONTENIDO ACTIVADA")
    
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
                            f"{dia} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][mes-1]} de {año}".lower()
                        ]
                        
                        pagina_correcta = any(fecha in texto_pagina for fecha in fecha_formatos)
                        
                        if pagina_correcta:
                            progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} - ✅ BOJA {num_boletin}")
                            
                            docs_boletin = buscar_en_boletin_completo(
                                año, 
                                num_boletin,
                                pd.to_datetime(fecha_actual),
                                contenido_completo,
                                progress_detail
                            )
                            
                            if docs_boletin:
                                con_contenido = sum(1 for d in docs_boletin if d.get('Tiene_Contenido', False))
                                resultados.extend(docs_boletin)
                                
                                if contenido_completo:
                                    st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_boletin)} docs ({con_contenido} con contenido)")
                                else:
                                    st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_boletin)} docs")
                                
                                encontrado = True
                                break
                except:
                    continue
            
            if encontrado:
                break
        
        if not encontrado:
            st.warning(f"⚠️ {fecha_actual.strftime('%d/%m/%Y')}: No encontrado")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.2)
    
    progress_bar.empty()
    progress_text.empty()
    progress_detail.empty()
    
    if resultados:
        con_contenido = sum(1 for d in resultados if d.get('Tiene_Contenido', False))
        st.success(f"✅ Completado: {len(resultados)} docs ({con_contenido} con contenido)")
    
    return resultados

# ============= FUNCIONES DE DIAGNÓSTICO =============

def probar_boja_especifico():
    """Prueba exhaustiva"""
    st.subheader("🧪 Prueba")
    
    col1, col2 = st.columns(2)
    año_prueba = col1.number_input("Año:", min_value=2000, max_value=2025, value=2022)
    num_boletin_prueba = col2.number_input("Boletín:", min_value=1, max_value=250, value=82)
    
    palabra_buscar = st.text_input("Palabra:", "FEDER")
    
    if st.button("🔍 Buscar", type="primary"):
        progress_detail = st.empty()
        
        resultados = buscar_en_boletin_completo(
            año_prueba, num_boletin_prueba,
            pd.to_datetime(datetime.now()),
            contenido_completo=True,
            progress_container=progress_detail
        )
        
        progress_detail.empty()
        
        if resultados:
            st.success(f"✅ {len(resultados)} docs")


def diagnosticar_boja():
    """Diagnóstico"""
    st.subheader("🔧 Diagnóstico")
    
    if st.button("🚀 Ejecutar"):
        url = "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"
        try:
            response = session.get(url, timeout=10)
            st.write(f"Feed RSS: {response.status_code}")
        except Exception as e:
            st.error(f"Error: {e}")


def filtrar_resultados(df, palabras_clave, solo_ayudas=True, busqueda_exacta=False):
    """Filtra resultados"""
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
    
    if 'Tiene_Contenido' in df.columns:
        con_contenido = df['Tiene_Contenido'].sum()
        st.info(f"📊 {len(df)} docs, {con_contenido} con contenido")
    
    if solo_ayudas:
        patron = r'\b(ayuda|ayudas|subvención|subvencion|subvenciones|convocatoria|convocatorias|bases\s+reguladoras)\b'
        mascara = df['_texto_busqueda'].str.contains(patron, case=False, regex=True, na=False)
        df = df[mascara]
        st.info(f"📊 Filtro ayudas: {len(df)} docs")
    
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    patron = r'\b' + re.escape(palabra) + r'\b'
                    mascara_palabra = df['_texto_busqueda'].str.contains(patron, case=False, regex=True, na=False)
                else:
                    mascara_palabra = df['_texto_busqueda'].str.contains(palabra, case=False, regex=False, na=False)
                
                mascara_final = mascara_final | mascara_palabra
                st.info(f"🔍 '{palabra}': {mascara_palabra.sum()} docs")
        
        df = df[mascara_final]
    
    df = df.drop(columns=['_texto_busqueda'])
    
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador Inteligente de Ayudas y Subvenciones")
st.markdown("**BOJA** + **BOE** - Con extracción automática de información")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    modo_diagnostico = st.checkbox("🔧 Diagnóstico", value=False)
    
    if modo_diagnostico:
        st.markdown("---")
        diagnosticar_boja()
        st.markdown("---")
        probar_boja_especifico()
        st.markdown("---")
    
    st.subheader("🤖 IA")
    usar_ia = st.checkbox("Resúmenes IA", value=False)
    
    api_key_openai = None
    modelo_openai = None
    
    if usar_ia:
        try:
            api_key_openai = st.secrets.get("openai", {}).get("api_key", "")
        except:
            pass
        
        if not api_key_openai:
            api_key_openai = st.text_input("🔑 API Key:", type="password")
        
        if api_key_openai:
            modelo_openai = st.selectbox("Modelo:", ["gpt-4o-mini", "gpt-4o"])
    
    st.markdown("---")
    busqueda_inteligente = st.checkbox("🔮 Búsqueda IA", value=False)
    
    palabras_clave = ""
    
    if busqueda_inteligente and api_key_openai:
        consulta_natural = st.text_area("Describe:", height=100)
        
        if st.button("🔮 Generar"):
            if consulta_natural:
                palabras_generadas = busqueda_inteligente_openai(consulta_natural, api_key_openai, modelo_openai or "gpt-4o-mini")
                st.success(f"✅ {palabras_generadas}")
                palabras_clave = palabras_generadas
    
    st.markdown("---")
    st.subheader("📰 Fuentes")
    
    usar_boja = st.checkbox("BOJA (Feed)", value=True)
    usar_boe = st.checkbox("BOE (RSS)", value=True)
    
    usar_boja_hist = st.checkbox("BOJA (Histórico)", value=False)
    usar_boe_hist = st.checkbox("BOE (Histórico)", value=False)
    
    fecha_desde = None
    fecha_hasta = None
    
    if usar_boja_hist or usar_boe_hist:
        col1, col2 = st.columns(2)
        fecha_desde = col1.date_input("Desde", datetime.now() - timedelta(days=7))
        fecha_hasta = col2.date_input("Hasta", datetime.now())
    
    st.markdown("---")
    st.subheader("🔍 Opciones")
    
    contenido_completo = st.checkbox("🔥 Contenido completo", value=False)
    
    if contenido_completo:
        st.warning("⚠️ Descarga contenido")
    
    st.markdown("---")
    st.subheader("🎯 Filtros")
    solo_ayudas = st.checkbox("Solo ayudas", value=True)
    
    if not busqueda_inteligente:
        palabras_clave = st.text_input("Palabras clave:", "")
    
    busqueda_exacta = st.checkbox("Búsqueda exacta", value=True)

# Búsqueda
if st.button("🚀 Buscar", type="primary"):
    todos_resultados = []
    
    if usar_boja:
        todos_resultados.extend(buscar_boja_feed(contenido_completo))
    
    if usar_boe:
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
        
        st.info(f"📊 Total: {len(df)} docs")
        
        lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
        
        df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
        df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
        
        if len(df_filtrado) > 0:
            st.success(f"✅ **{len(df_filtrado)} resultados**")
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total", len(df_filtrado))
            col2.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
            col3.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
            
            # ============= EXTRACCIÓN DE INFORMACIÓN =============
            st.markdown("---")
            st.subheader("📋 Información Extraída")
            
            with st.spinner("Extrayendo información de los documentos..."):
                documentos_procesados = []
                
                for idx, (_, row) in enumerate(df_filtrado.iterrows()):
                    info_extraida = extraer_informacion_documento(
                        row['Título'],
                        row['Resumen'],
                        row.get('Contenido_Completo', ''),
                        lista_palabras
                    )
                    
                    doc_procesado = {
                        **row.to_dict(),
                        **info_extraida
                    }
                    
                    documentos_procesados.append(doc_procesado)
            
            # Mostrar documentos con información extraída
            for idx, doc in enumerate(documentos_procesados):
                with st.expander(f"📄 {doc['Título'][:100]}...", expanded=(idx == 0)):
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        st.markdown(f"**🎯 Tipo:** {doc['tipo_documento']}")
                        if doc['organismo']:
                            st.markdown(f"**🏛️ Organismo:** {doc['organismo']}")
                        if doc['beneficiarios']:
                            st.markdown(f"**👥 Beneficiarios:** {doc['beneficiarios'][:150]}...")
                        if doc['objeto']:
                            st.markdown(f"**🎯 Objeto:** {doc['objeto'][:200]}...")
                        if doc['cuantia']:
                            st.markdown(f"**💰 Cuantía:** {doc['cuantia']}")
                        if doc['plazo_solicitud']:
                            st.markdown(f"**📅 Plazo:** {doc['plazo_solicitud']}")
                    
                    with col2:
                        st.markdown(f"**📰 Boletín:** {doc['Boletín']}")
                        if pd.notna(doc.get('Fecha')):
                            st.markdown(f"**📆 Fecha:** {doc['Fecha'].strftime('%d/%m/%Y')}")
                        st.markdown(f"[🔗 Ver documento]({doc['Enlace']})")
                    
                    # Mostrar contextos donde aparecen las palabras clave
                    if doc['contexto_palabras']:
                        st.markdown("---")
                        st.markdown(f"**🔍 Apariciones de palabras clave:**")
                        for contexto in doc['contexto_palabras'][:3]:  # Máximo 3 contextos
                            st.info(f"**{contexto['palabra'].upper()}:** {contexto['contexto']}")
            
            # Exportar con información extraída
            st.markdown("---")
            df_export = pd.DataFrame(documentos_procesados)
            
            # Seleccionar columnas para exportar
            columnas_export = [
                'Boletín', 'Título', 'tipo_documento', 'organismo', 
                'beneficiarios', 'cuantia', 'plazo_solicitud', 'objeto',
                'Enlace', 'Fecha'
            ]
            
            columnas_disponibles = [col for col in columnas_export if col in df_export.columns]
            
            csv_completo = df_export[columnas_disponibles].to_csv(index=False, encoding='utf-8-sig')
            
            st.download_button(
                "📥 Descargar CSV con información extraída",
                csv_completo,
                f"ayudas_info_extraida_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                "text/csv",
                key='download-info-extraida'
            )
            
            # Tabla simple
            st.markdown("---")
            st.subheader("📊 Tabla Resumen")
            st.dataframe(df_filtrado[['Boletín', 'Título', 'Fecha', 'Enlace']], use_container_width=True, height=400)
        else:
            st.warning("⚠️ Sin resultados")
    else:
        st.error("❌ No se obtuvieron resultados")

with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### 🎯 Extracción Automática de Información
    
    La aplicación ahora extrae automáticamente:
    
    - **Tipo de documento**: Resolución, Orden, Convocatoria, etc.
    - **Organismo emisor**: Consejería, Dirección General, etc.
    - **Cuantía**: Importe de la ayuda
    - **Plazo de solicitud**: Fechas límite
    - **Beneficiarios**: Quién puede solicitar
    - **Objeto**: Finalidad de la ayuda
    - **Contexto**: Dónde aparecen las palabras clave
    
    ### 📥 Exportación
    
    El CSV exportado incluye toda la información extraída en columnas separadas.
    """)

st.markdown("---")
st.markdown("🤖 **Versión 4.0** - Extracción automática de información")

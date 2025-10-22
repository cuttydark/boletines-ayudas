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

# ============= CONFIGURACIÓN DE SESIÓN =============

def crear_session():
    """Crea una sesión HTTP con retry automático"""
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Connection": "keep-alive",
    })
    
    return session

session = crear_session()

# ============= FUNCIONES DE EXTRACCIÓN DE INFORMACIÓN =============

def extraer_informacion_documento(titulo, resumen, contenido, palabras_clave):
    """Extrae información estructurada"""
    
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
    
    # Tipo de documento
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
    
    # Organismo
    organismos = [
        r'Consejería de [A-Za-záéíóúñÑ\s,]+',
        r'Dirección General de [A-Za-záéíóúñÑ\s,]+',
        r'Secretaría General de [A-Za-záéíóúñÑ\s,]+',
    ]
    
    for patron in organismos:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['organismo'] = match.group(0).strip()
            break
    
    # Cuantía
    patrones_cuantia = [
        r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
        r'importe\s+(?:total\s+)?(?:de\s+)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?',
    ]
    
    for patron in patrones_cuantia:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['cuantia'] = match.group(0).strip()
            break
    
    # Plazo
    patrones_plazo = [
        r'plazo\s+de\s+(?:presentación\s+de\s+)?solicitudes?[:\s]+([^.]{10,80})',
        r'hasta\s+el\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})',
    ]
    
    for patron in patrones_plazo:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['plazo_solicitud'] = match.group(0).strip()
            break
    
    # Contexto palabras clave
    for palabra in palabras_clave:
        palabra_lower = palabra.lower().strip()
        if palabra_lower in texto_completo:
            idx = 0
            while True:
                idx = texto_completo.find(palabra_lower, idx)
                if idx == -1:
                    break
                
                inicio = max(0, idx - 200)
                fin = min(len(contenido) if contenido else len(texto_completo), idx + len(palabra_lower) + 200)
                
                contexto = contenido[inicio:fin] if contenido else texto_completo[inicio:fin]
                
                info['contexto_palabras'].append({
                    'palabra': palabra,
                    'contexto': f"...{contexto}..."
                })
                
                idx += len(palabra_lower)
                
                if len([c for c in info['contexto_palabras'] if c['palabra'] == palabra]) >= 2:
                    break
    
    return info

# ============= FUNCIONES DE IA =============

def resumir_con_openai(texto, api_key, modelo="gpt-4o-mini"):
    """Genera resumen con IA"""
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": "Eres experto en ayudas españolas."},
                {"role": "user", "content": f"Resume esta ayuda en JSON:\n{texto[:8000]}"}
            ],
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"}
        )
        
        return json.loads(response.choices[0].message.content)
    except:
        return {}

def busqueda_inteligente_openai(consulta, api_key, modelo="gpt-4o-mini"):
    """Convierte consulta a palabras clave"""
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": "Convierte consultas a palabras clave."},
                {"role": "user", "content": f"Palabras clave para: {consulta}"}
            ],
            temperature=0.3,
            max_tokens=100
        )
        
        return response.choices[0].message.content.strip()
    except:
        return consulta

# ============= FUNCIONES DE BÚSQUEDA =============

def extraer_contenido_completo(url, max_intentos=2):
    """Extrae contenido de una URL"""
    for intento in range(max_intentos):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for element in soup(["script", "style", "nav", "header", "footer"]):
                element.decompose()
            
            contenido = soup.get_text(separator=' ', strip=True)
            return contenido if contenido else ""
        except:
            if intento < max_intentos - 1:
                time.sleep(0.5)
    
    return ""

def buscar_boja_feed(contenido_completo=False):
    """Feed BOJA"""
    resultados = []
    url = "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"
    
    try:
        response = session.get(url, timeout=20)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            if any(x in enlace for x in ['/temas/', '/organismos/']):
                continue
            
            if '/boja/' not in enlace and '/eboja/' not in enlace:
                continue
            
            fecha = pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True)
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
    except:
        pass
    
    return resultados

def buscar_boe_rss(contenido_completo=False):
    """Feed BOE"""
    resultados = []
    url = "https://www.boe.es/rss/boe.php"
    
    try:
        response = session.get(url, timeout=20)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            fecha = pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True)
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
    except:
        pass
    
    return resultados

def buscar_boe_historico_api(fecha_inicio, fecha_fin, contenido_completo=False):
    """BOE histórico"""
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"BOE {fecha_actual.strftime('%d/%m/%Y')}")
        
        fecha_str = fecha_actual.strftime("%Y%m%d")
        url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha_str}"
        
        try:
            response = session.get(url, timeout=20)
            if response.status_code == 200:
                data = response.json()
                # Procesar sumario...
        except:
            pass
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.3)
    
    progress_bar.empty()
    progress_text.empty()
    
    return resultados

# ============= BOJA HISTÓRICO MEJORADO =============

def extraer_secciones_boja(url_boletin):
    """Extrae secciones"""
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
            
            return list({s['url']: s for s in secciones}.values())
    except:
        pass
    
    return []


def extraer_documentos_de_seccion(url_seccion):
    """Extrae documentos de sección"""
    documentos = []
    
    try:
        response = session.get(url_seccion, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                
                if re.search(r'/\d+$', href) and '/s' not in href and len(titulo) > 10:
                    if href.startswith('/'):
                        url_completa = f"https://www.juntadeandalucia.es{href}"
                    else:
                        url_completa = href
                    
                    documentos.append({'titulo': titulo, 'url': url_completa})
            
            return list({d['url']: d for d in documentos}.values())
    except:
        pass
    
    return []


def buscar_en_boletin_completo(año, num_boletin, fecha_publicacion, contenido_completo=False, progress_container=None):
    """Busca en boletín completo"""
    
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
                        progress_container.text(f"    📄 {idx+1}/{len(documentos)}")
                    
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
            
            time.sleep(0.1)
    
    return resultados


def calcular_numero_boletin_mejorado(fecha):
    """Calcula número de boletín con mayor precisión"""
    año = fecha.year
    mes = fecha.month
    dia = fecha.day
    
    # Días transcurridos en el año
    dia_año = fecha.timetuple().tm_yday
    
    # Estimar días hábiles (lunes a viernes)
    # Aproximadamente 5/7 de los días son hábiles
    dias_habiles_estimados = int(dia_año * (5/7))
    
    # El BOJA publica aproximadamente 200-220 boletines al año
    # Ratio: 220 boletines / 260 días hábiles ≈ 0.85
    numero_estimado = int(dias_habiles_estimados * 0.85)
    
    return max(1, min(250, numero_estimado))


def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda histórica"""
    
    dias_antiguedad = (datetime.now() - fecha_fin).days
    
    if dias_antiguedad <= 30:
        st.info("🔍 Fechas recientes (feed RSS)")
        return buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo)
    else:
        st.info(f"🔍 Búsqueda exhaustiva ({dias_antiguedad} días)")
        return buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo)


def buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo=False):
    """Feed filtrado"""
    
    resultados_feed = buscar_boja_feed(contenido_completo=False)
    
    if not resultados_feed:
        return []
    
    df_feed = pd.DataFrame(resultados_feed)
    
    if 'Fecha' in df_feed.columns:
        mascara = (df_feed['Fecha'] >= pd.to_datetime(fecha_inicio)) & (df_feed['Fecha'] <= pd.to_datetime(fecha_fin))
        df_filtrado = df_feed[mascara]
    else:
        df_filtrado = df_feed
    
    resultados = df_filtrado.to_dict('records')
    
    if contenido_completo:
        for r in resultados:
            if r['Enlace']:
                r['Contenido_Completo'] = extraer_contenido_completo(r['Enlace'])
    
    return resultados


def buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda exhaustiva mejorada"""
    
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_detail = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    st.info("🔄 Búsqueda exhaustiva...")
    
    if contenido_completo:
        st.warning("⚠️ DESCARGA ACTIVADA")
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
        año = fecha_actual.year
        mes = fecha_actual.month
        dia = fecha_actual.day
        
        # Calcular número estimado con el nuevo algoritmo
        num_boletin_estimado = calcular_numero_boletin_mejorado(fecha_actual)
        
        progress_detail.text(f"    🔍 Estimado: BOJA {num_boletin_estimado}")
        
        encontrado = False
        
        # AMPLIADO: probar un rango MÁS GRANDE (±40 en lugar de ±20)
        for offset in range(-40, 41):
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
                        
                        # VERIFICACIÓN MÁS FLEXIBLE de la fecha
                        meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
                        
                        fecha_formatos = [
                            fecha_actual.strftime('%d/%m/%Y'),
                            fecha_actual.strftime('%d-%m-%Y'),
                            f"{dia}/{mes}/{año}",
                            f"{dia} de {meses[mes-1]} de {año}",
                            f"{dia} {meses[mes-1]} {año}",
                        ]
                        
                        # Verificar si ALGUNO de los formatos aparece
                        pagina_correcta = any(fecha.lower() in texto_pagina for fecha in fecha_formatos)
                        
                        # ALTERNATIVA: Si no encuentra fecha exacta, verificar si el número está en rango razonable
                        if not pagina_correcta:
                            # Aceptar si estamos cerca del número estimado (±5)
                            if abs(num_boletin - num_boletin_estimado) <= 5:
                                # Verificar que la página tenga contenido válido
                                if len(texto_pagina) > 500:
                                    pagina_correcta = True
                        
                        if pagina_correcta:
                            progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} - ✅ BOJA {num_boletin}")
                            progress_detail.text(f"    ✅ URL: {url_boletin}")
                            
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
            st.warning(f"⚠️ {fecha_actual.strftime('%d/%m/%Y')}: No encontrado (probado BOJA {num_boletin_estimado-40} a {num_boletin_estimado+40})")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.1)
    
    progress_bar.empty()
    progress_text.empty()
    progress_detail.empty()
    
    if resultados:
        con_contenido = sum(1 for d in resultados if d.get('Tiene_Contenido', False))
        st.success(f"✅ Completado: {len(resultados)} docs ({con_contenido} con contenido)")
    
    return resultados

# ============= FILTRADO =============

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
        patron = r'\b(ayuda|ayudas|subvención|subvencion|subvenciones|convocatoria|convocatorias)\b'
        mascara = df['_texto_busqueda'].str.contains(patron, case=False, regex=True, na=False)
        df = df[mascara]
        st.info(f"📊 Con filtro ayudas: {len(df)} docs")
    
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    patron = r'\b' + re.escape(palabra) + r'\b'
                    mascara = df['_texto_busqueda'].str.contains(patron, case=False, regex=True, na=False)
                else:
                    mascara = df['_texto_busqueda'].str.contains(palabra, case=False, regex=False, na=False)
                
                mascara_final = mascara_final | mascara
                st.info(f"🔍 '{palabra}': {mascara.sum()} docs")
        
        df = df[mascara_final]
    
    df = df.drop(columns=['_texto_busqueda'])
    
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador de Ayudas y Subvenciones")
st.markdown("**BOJA + BOE** - Con extracción de información")

with st.sidebar:
    st.header("⚙️ Config")
    
    st.subheader("🤖 IA")
    usar_ia = st.checkbox("Resúmenes IA", value=False)
    
    api_key_openai = None
    
    if usar_ia:
        try:
            api_key_openai = st.secrets.get("openai", {}).get("api_key", "")
        except:
            pass
        
        if not api_key_openai:
            api_key_openai = st.text_input("API Key:", type="password")
    
    st.markdown("---")
    st.subheader("📰 Fuentes")
    
    usar_boja = st.checkbox("BOJA (Feed)", value=True)
    usar_boe = st.checkbox("BOE (RSS)", value=False)
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
    
    st.markdown("---")
    st.subheader("🎯 Filtros")
    solo_ayudas = st.checkbox("Solo ayudas", value=True)
    palabras_clave = st.text_input("Palabras clave:", "")
    busqueda_exacta = st.checkbox("Búsqueda exacta", value=True)

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
            
            st.markdown("---")
            st.subheader("📋 Información Extraída")
            
            documentos_procesados = []
            
            for _, row in df_filtrado.iterrows():
                info = extraer_informacion_documento(
                    row['Título'],
                    row['Resumen'],
                    row.get('Contenido_Completo', ''),
                    lista_palabras
                )
                
                documentos_procesados.append({**row.to_dict(), **info})
            
            for idx, doc in enumerate(documentos_procesados):
                with st.expander(f"📄 {doc['Título'][:100]}...", expanded=(idx == 0)):
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        if doc['tipo_documento']:
                            st.markdown(f"**Tipo:** {doc['tipo_documento']}")
                        if doc['organismo']:
                            st.markdown(f"**Organismo:** {doc['organismo']}")
                        if doc['cuantia']:
                            st.markdown(f"**Cuantía:** {doc['cuantia']}")
                        if doc['plazo_solicitud']:
                            st.markdown(f"**Plazo:** {doc['plazo_solicitud']}")
                    
                    with col2:
                        st.markdown(f"**Boletín:** {doc['Boletín']}")
                        if pd.notna(doc.get('Fecha')):
                            st.markdown(f"**Fecha:** {doc['Fecha'].strftime('%d/%m/%Y')}")
                        st.markdown(f"[🔗 Ver]({doc['Enlace']})")
                    
                    if doc['contexto_palabras']:
                        st.markdown("---")
                        st.markdown("**🔍 Contexto:**")
                        for ctx in doc['contexto_palabras'][:2]:
                            st.info(f"**{ctx['palabra']}:** {ctx['contexto']}")
            
            st.markdown("---")
            df_export = pd.DataFrame(documentos_procesados)
            csv = df_export.to_csv(index=False, encoding='utf-8-sig')
            
            st.download_button(
                "📥 Descargar CSV",
                csv,
                f"ayudas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                "text/csv"
            )
        else:
            st.warning("⚠️ Sin resultados")
    else:
        st.error("❌ No se obtuvieron resultados")

st.markdown("---")
st.markdown("🤖 **Versión 4.1** - Algoritmo mejorado (rango ±40 boletines)")


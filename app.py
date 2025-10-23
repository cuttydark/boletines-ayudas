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

# ============= CONFIGURACIÓN =============

def crear_session():
    """Crea sesión HTTP"""
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

# ============= EXTRACCIÓN DE INFORMACIÓN =============

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
    
    # Tipo
    if re.search(r'\b(resolución|resolucion)\b', texto_completo):
        info['tipo_documento'] = 'Resolución'
    elif re.search(r'\b(orden)\b', texto_completo):
        info['tipo_documento'] = 'Orden'
    elif re.search(r'\b(decreto)\b', texto_completo):
        info['tipo_documento'] = 'Decreto'
    elif re.search(r'\b(convocatoria)\b', texto_completo):
        info['tipo_documento'] = 'Convocatoria'
    
    # Organismo
    organismos = [
        r'Consejería de [A-Za-záéíóúñÑ\s,]+',
        r'Dirección General de [A-Za-záéíóúñÑ\s,]+',
    ]
    
    for patron in organismos:
        match = re.search(patron, texto_completo, re.IGNORECASE)
        if match:
            info['organismo'] = match.group(0).strip()
            break
    
    # Cuantía
    match = re.search(r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*euros?', texto_completo, re.IGNORECASE)
    if match:
        info['cuantia'] = match.group(0).strip()
    
    # Plazo
    match = re.search(r'plazo\s+de\s+(?:presentación\s+de\s+)?solicitudes?[:\s]+([^.]{10,80})', texto_completo, re.IGNORECASE)
    if match:
        info['plazo_solicitud'] = match.group(0).strip()
    
    # Contexto palabras
    for palabra in palabras_clave:
        palabra_lower = palabra.lower().strip()
        if palabra_lower in texto_completo:
            idx = 0
            count = 0
            while count < 2:
                idx = texto_completo.find(palabra_lower, idx)
                if idx == -1:
                    break
                
                inicio = max(0, idx - 150)
                fin = min(len(contenido) if contenido else len(texto_completo), idx + len(palabra_lower) + 150)
                
                contexto = contenido[inicio:fin] if contenido else texto_completo[inicio:fin]
                
                info['contexto_palabras'].append({
                    'palabra': palabra,
                    'contexto': f"...{contexto}..."
                })
                
                idx += len(palabra_lower)
                count += 1
    
    return info

# ============= IA =============

def resumir_con_openai(texto, api_key, modelo="gpt-4o-mini"):
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": "Eres experto en ayudas españolas."},
                {"role": "user", "content": f"Resume:\n{texto[:8000]}"}
            ],
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {}

def busqueda_inteligente_openai(consulta, api_key, modelo="gpt-4o-mini"):
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": "Convierte a palabras clave."},
                {"role": "user", "content": f"Palabras clave: {consulta}"}
            ],
            temperature=0.3,
            max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except:
        return consulta

# ============= BÚSQUEDA =============

def extraer_contenido_completo(url, max_intentos=2):
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
    resultados = []
    try:
        response = session.get("https://www.juntadeandalucia.es/boja/distribucion/boja.xml", timeout=20)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            enlace = entry.get('link', '')
            
            if any(x in enlace for x in ['/temas/', '/organismos/']) or '/boja/' not in enlace:
                continue
            
            resultados.append({
                'Boletín': 'BOJA',
                'Título': titulo,
                'Resumen': BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()[:300],
                'Contenido_Completo': extraer_contenido_completo(enlace) if contenido_completo else "",
                'Enlace': enlace,
                'Fecha': pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True).tz_localize(None) if pd.notna(pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True)) else pd.NaT
            })
    except:
        pass
    return resultados

def buscar_boe_rss(contenido_completo=False):
    resultados = []
    try:
        response = session.get("https://www.boe.es/rss/boe.php", timeout=20)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            resultados.append({
                'Boletín': 'BOE',
                'Título': entry.get('title', ''),
                'Resumen': BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()[:300],
                'Contenido_Completo': "",
                'Enlace': entry.get('link', ''),
                'Fecha': pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True).tz_localize(None) if pd.notna(pd.to_datetime(entry.get('published', ''), errors='coerce', utc=True)) else pd.NaT
            })
    except:
        pass
    return resultados

# ============= BOJA HISTÓRICO =============

def extraer_secciones_boja(url_boletin):
    secciones = []
    try:
        response = session.get(url_boletin, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                if re.search(r'/s\d+', href):
                    url = f"https://www.juntadeandalucia.es{href}" if href.startswith('/') else href
                    secciones.append({'titulo': enlace.get_text(strip=True), 'url': url})
            return list({s['url']: s for s in secciones}.values())
    except:
        pass
    return []

def extraer_documentos_de_seccion(url_seccion):
    documentos = []
    try:
        response = session.get(url_seccion, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for enlace in soup.find_all('a', href=True):
                href = enlace['href']
                titulo = enlace.get_text(strip=True)
                if re.search(r'/\d+$', href) and '/s' not in href and len(titulo) > 10:
                    url = f"https://www.juntadeandalucia.es{href}" if href.startswith('/') else href
                    documentos.append({'titulo': titulo, 'url': url})
            return list({d['url']: d for d in documentos}.values())
    except:
        pass
    return []

def buscar_en_boletin_completo(año, num_boletin, fecha_publicacion, contenido_completo=False, progress_container=None):
    resultados = []
    
    urls_boletin = [
        f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
        f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
    ]
    
    url_valida = None
    for url in urls_boletin:
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                url_valida = url
                break
        except:
            continue
    
    if not url_valida:
        return []
    
    secciones = extraer_secciones_boja(url_valida)
    
    if len(secciones) > 0:
        for seccion in secciones:
            documentos = extraer_documentos_de_seccion(seccion['url'])
            
            for idx, doc in enumerate(documentos):
                if progress_container:
                    progress_container.text(f"    📄 {idx+1}/{len(documentos)}: {doc['titulo'][:40]}...")
                
                texto = extraer_contenido_completo(doc['url']) if contenido_completo else ""
                
                resultados.append({
                    'Boletín': 'BOJA',
                    'Título': doc['titulo'],
                    'Resumen': f"BOJA {num_boletin}/{año} - {seccion['titulo']}",
                    'Contenido_Completo': texto,
                    'Enlace': doc['url'],
                    'Fecha': fecha_publicacion,
                    'Seccion': seccion['titulo'],
                    'Numero_Boletin': num_boletin,
                    'Tiene_Contenido': len(texto) > 0
                })
            
            time.sleep(0.1)
    
    return resultados

def encontrar_boletin_por_fecha(año, fecha_buscar, contenido_completo=False, progress_detail=None):
    """Busca el boletín de una fecha específica probando diferentes estrategias"""
    
    mes = fecha_buscar.month
    dia = fecha_buscar.day
    
    # ESTRATEGIA 1: Estimación basada en días del año
    dia_año = fecha_buscar.timetuple().tm_yday
    dias_habiles = int(dia_año * (5/7))
    num_estimado = int(dias_habiles * 0.85)
    
    if progress_detail:
        progress_detail.text(f"    🎯 Estimación inicial: BOJA {num_estimado}")
    
    # ESTRATEGIA 2: Probar rango amplio alrededor del estimado
    for offset in range(-50, 51):
        num_boletin = max(1, min(250, num_estimado + offset))
        
        if offset % 10 == 0 and progress_detail:
            progress_detail.text(f"    🔍 Probando BOJA {num_boletin}...")
        
        urls = [
            f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
            f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
        ]
        
        for url in urls:
            try:
                response = session.get(url, timeout=8)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    texto = soup.get_text().lower()
                    
                    # Verificar fecha
                    meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
                    
                    formatos_fecha = [
                        fecha_buscar.strftime('%d/%m/%Y'),
                        f"{dia} de {meses[mes-1]} de {año}",
                        f"{dia}/{mes}/{año}",
                    ]
                    
                    if any(f.lower() in texto for f in formatos_fecha):
                        if progress_detail:
                            progress_detail.text(f"    ✅ ¡ENCONTRADO! BOJA {num_boletin}")
                        
                        return buscar_en_boletin_completo(año, num_boletin, pd.to_datetime(fecha_buscar), contenido_completo, progress_detail)
                
                time.sleep(0.05)
            except:
                continue
    
    # ESTRATEGIA 3: Si no encontró nada, probar TODOS los boletines del mes
    if progress_detail:
        progress_detail.text(f"    ⚠️ No encontrado en rango. Probando TODO el mes {meses[mes-1]}...")
    
    # Rango de boletines para cada mes (aproximado)
    rangos_mes = {
        1: (1, 20), 2: (21, 40), 3: (41, 60), 4: (61, 80),
        5: (81, 100), 6: (101, 120), 7: (121, 140), 8: (141, 160),
        9: (161, 180), 10: (181, 200), 11: (201, 220), 12: (221, 240)
    }
    
    inicio, fin = rangos_mes.get(mes, (1, 250))
    
    for num_boletin in range(inicio, fin + 1):
        if num_boletin % 5 == 0 and progress_detail:
            progress_detail.text(f"    🔍 Búsqueda exhaustiva: BOJA {num_boletin}...")
        
        urls = [
            f"https://www.juntadeandalucia.es/boja/{año}/{str(num_boletin).zfill(3)}/",
            f"https://www.juntadeandalucia.es/eboja/{año}/{str(num_boletin).zfill(3)}/",
        ]
        
        for url in urls:
            try:
                response = session.get(url, timeout=8)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    texto = soup.get_text().lower()
                    
                    meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
                    
                    formatos_fecha = [
                        fecha_buscar.strftime('%d/%m/%Y'),
                        f"{dia} de {meses[mes-1]} de {año}",
                    ]
                    
                    if any(f.lower() in texto for f in formatos_fecha):
                        if progress_detail:
                            progress_detail.text(f"    ✅ ¡ENCONTRADO en búsqueda exhaustiva! BOJA {num_boletin}")
                        
                        return buscar_en_boletin_completo(año, num_boletin, pd.to_datetime(fecha_buscar), contenido_completo, progress_detail)
                
                time.sleep(0.05)
            except:
                continue
    
    return []

def buscar_boja_historico(fecha_inicio, fecha_fin, contenido_completo=False):
    """Búsqueda histórica"""
    
    dias_antiguedad = (datetime.now() - fecha_fin).days
    
    if dias_antiguedad <= 30:
        st.info("🔍 Fechas recientes (feed RSS)")
        return buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo)
    else:
        st.info(f"🔍 Búsqueda exhaustiva activada ({dias_antiguedad} días)")
        return buscar_boja_historico_exhaustivo(fecha_inicio, fecha_fin, contenido_completo)

def buscar_boja_feed_filtrado_por_fechas(fecha_inicio, fecha_fin, contenido_completo=False):
    resultados = buscar_boja_feed(contenido_completo=False)
    if not resultados:
        return []
    
    df = pd.DataFrame(resultados)
    if 'Fecha' in df.columns:
        mascara = (df['Fecha'] >= pd.to_datetime(fecha_inicio)) & (df['Fecha'] <= pd.to_datetime(fecha_fin))
        df = df[mascara]
    
    resultados = df.to_dict('records')
    
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
    
    st.info("🔄 Búsqueda exhaustiva fecha por fecha...")
    
    if contenido_completo:
        st.warning("⚠️ DESCARGA DE CONTENIDO ACTIVADA")
    
    while fecha_actual <= fecha_fin:
        progress_bar.progress(dia_actual / total_dias)
        progress_text.text(f"📅 {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
        año = fecha_actual.year
        
        # Usar la nueva función mejorada
        docs_encontrados = encontrar_boletin_por_fecha(año, fecha_actual, contenido_completo, progress_detail)
        
        if docs_encontrados:
            con_contenido = sum(1 for d in docs_encontrados if d.get('Tiene_Contenido', False))
            resultados.extend(docs_encontrados)
            
            if contenido_completo:
                st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_encontrados)} docs ({con_contenido} con contenido)")
            else:
                st.success(f"✅ {fecha_actual.strftime('%d/%m/%Y')}: {len(docs_encontrados)} docs")
        else:
            st.warning(f"⚠️ {fecha_actual.strftime('%d/%m/%Y')}: No se encontró boletín para esta fecha")
        
        fecha_actual += timedelta(days=1)
        dia_actual += 1
        time.sleep(0.1)
    
    progress_bar.empty()
    progress_text.empty()
    progress_detail.empty()
    
    if resultados:
        con_contenido = sum(1 for d in resultados if d.get('Tiene_Contenido', False))
        st.success(f"✅ Búsqueda completada: {len(resultados)} documentos ({con_contenido} con contenido)")
    else:
        st.error("❌ No se encontraron documentos en el rango de fechas especificado")
    
    return resultados

# ============= FILTRADO =============

def filtrar_resultados(df, palabras_clave, solo_ayudas=True, busqueda_exacta=False):
    if df.empty:
        return df
    
    if 'Contenido_Completo' in df.columns:
        df['_texto'] = df['Título'].fillna('').astype(str) + ' ' + df['Resumen'].fillna('').astype(str) + ' ' + df['Contenido_Completo'].fillna('').astype(str)
    else:
        df['_texto'] = df['Título'].fillna('').astype(str) + ' ' + df['Resumen'].fillna('').astype(str)
    
    if 'Tiene_Contenido' in df.columns:
        st.info(f"📊 {len(df)} docs, {df['Tiene_Contenido'].sum()} con contenido")
    
    if solo_ayudas:
        patron = r'\b(ayuda|ayudas|subvención|subvencion|subvenciones|convocatoria|convocatorias)\b'
        df = df[df['_texto'].str.contains(patron, case=False, regex=True, na=False)]
        st.info(f"📊 Filtro ayudas: {len(df)} docs")
    
    if palabras_clave:
        mascara = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    patron = r'\b' + re.escape(palabra) + r'\b'
                    m = df['_texto'].str.contains(patron, case=False, regex=True, na=False)
                else:
                    m = df['_texto'].str.contains(palabra, case=False, regex=False, na=False)
                
                mascara = mascara | m
                st.info(f"🔍 '{palabra}': {m.sum()} docs")
        
        df = df[mascara]
    
    df = df.drop(columns=['_texto'])
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador de Ayudas y Subvenciones")
st.markdown("**BOJA + BOE** - Búsqueda exhaustiva mejorada")

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
    
    fecha_desde = None
    fecha_hasta = None
    
    if usar_boja_hist:
        col1, col2 = st.columns(2)
        fecha_desde = col1.date_input("Desde", datetime(2025, 3, 3))
        fecha_hasta = col2.date_input("Hasta", datetime(2025, 3, 3))
    
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
    
    if todos_resultados:
        df = pd.DataFrame(todos_resultados)
        df = df.drop_duplicates(subset=['Enlace'], keep='first')
        
        st.info(f"📊 Total: {len(df)} docs")
        
        lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
        
        df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
        df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
        
        if len(df_filtrado) > 0:
            st.success(f"✅ **{len(df_filtrado)} resultados**")
            
            col1, col2 = st.columns(2)
            col1.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
            col2.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
            
            st.markdown("---")
            st.subheader("📋 Información Extraída")
            
            docs_procesados = []
            for _, row in df_filtrado.iterrows():
                info = extraer_informacion_documento(row['Título'], row['Resumen'], row.get('Contenido_Completo', ''), lista_palabras)
                docs_procesados.append({**row.to_dict(), **info})
            
            for idx, doc in enumerate(docs_procesados):
                with st.expander(f"📄 {doc['Título'][:80]}...", expanded=(idx == 0)):
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        if doc['tipo_documento']:
                            st.markdown(f"**Tipo:** {doc['tipo_documento']}")
                        if doc['organismo']:
                            st.markdown(f"**Organismo:** {doc['organismo'][:100]}")
                        if doc['cuantia']:
                            st.markdown(f"**Cuantía:** {doc['cuantia']}")
                        if doc['plazo_solicitud']:
                            st.markdown(f"**Plazo:** {doc['plazo_solicitud'][:100]}")
                    
                    with col2:
                        st.markdown(f"**Boletín:** {doc['Boletín']}")
                        if pd.notna(doc.get('Fecha')):
                            st.markdown(f"**Fecha:** {doc['Fecha'].strftime('%d/%m/%Y')}")
                        st.markdown(f"[🔗 Ver]({doc['Enlace']})")
                    
                    if doc['contexto_palabras']:
                        st.markdown("---")
                        for ctx in doc['contexto_palabras'][:2]:
                            st.info(f"**{ctx['palabra'].upper()}:** {ctx['contexto']}")
            
            st.markdown("---")
            csv = pd.DataFrame(docs_procesados).to_csv(index=False, encoding='utf-8-sig')
            st.download_button("📥 Descargar CSV", csv, f"ayudas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", "text/csv")
        else:
            st.warning("⚠️ Sin resultados con esos filtros")
            if not contenido_completo:
                st.info("💡 Activa 'Contenido completo' para buscar dentro de los documentos")
    else:
        st.error("❌ No se obtuvieron resultados")

with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### 🎯 Nueva búsqueda exhaustiva de 3 niveles
    
    **Nivel 1:** Estimación inteligente (±50 boletines)
    **Nivel 2:** Búsqueda exhaustiva en TODO el mes
    **Nivel 3:** Verificación fecha exacta
    
    Si no encuentra el boletín del 3 de marzo, probará TODOS los boletines de marzo (41-60 aproximadamente).
    """)

st.markdown("---")
st.markdown("🤖 **Versión 5.0** - Búsqueda exhaustiva de 3 niveles")

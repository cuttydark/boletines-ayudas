import streamlit as st
import requests
import feedparser
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="Búsqueda Ayudas BOJA/BOE", layout="wide")

# ============= CONFIGURACIÓN DE SESIÓN MEJORADA =============

def crear_session():
    """Crea una sesión HTTP con retry automático y User-Agent completo"""
    session = requests.Session()
    
    # Configurar reintentos automáticos
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # User-Agent completo y actualizado
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    
    return session

session = crear_session()

# ============= FUNCIONES DE BÚSQUEDA =============

def extraer_contenido_completo(url, max_intentos=2):
    """Extrae el texto completo de una página con mejor manejo de errores"""
    for intento in range(max_intentos):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Eliminar elementos no deseados
            for element in soup(["script", "style", "nav", "header", "footer", "iframe"]):
                element.decompose()
            
            # Extraer texto limpio
            contenido = soup.get_text(separator=' ', strip=True)
            
            # Rate limiting
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
            
            # Extraer fecha
            fecha_str = entry.get('published', entry.get('updated', ''))
            fecha = pd.to_datetime(fecha_str, errors='coerce', utc=True)
            if pd.notna(fecha):
                fecha = fecha.tz_localize(None)
            
            # Contenido completo opcional
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
    """
    Busca en el BOE por rango de fechas usando la API oficial de sumarios
    Documentación: https://www.boe.es/datosabiertos/documentos/APIsumarioBOE.pdf
    """
    resultados = []
    fecha_actual = fecha_inicio
    
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    total_dias = (fecha_fin - fecha_actual).days + 1
    dia_actual = 0
    
    while fecha_actual <= fecha_fin:
        # Actualizar barra de progreso
        progreso = dia_actual / total_dias
        progress_bar.progress(progreso)
        progress_text.text(f"Consultando BOE del {fecha_actual.strftime('%d/%m/%Y')} ({dia_actual+1}/{total_dias})")
        
        # Formato de fecha para la API: AAAAMMDD
        fecha_str = fecha_actual.strftime("%Y%m%d")
        url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha_str}"
        
        try:
            response = session.get(
                url,
                headers={"Accept": "application/json"},
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Verificar respuesta correcta
                if data.get("status", {}).get("code") == "200":
                    sumario = data.get("data", {}).get("sumario", {})
                    
                    # Recorrer todas las secciones del BOE
                    for diario in sumario.get("diario", []):
                        for seccion in diario.get("seccion", []):
                            # Procesar departamentos
                            for departamento in seccion.get("departamento", []):
                                # Procesar epígrafes
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
                                
                                # Procesar items directos
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
            
            # Rate limiting
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
        
        # Estimar rango de números de boletín
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
    
    # Crear columna de búsqueda
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
    
    # Filtro de ayudas/subvenciones
    if solo_ayudas:
        patron_ayudas = r'\b(ayuda|subvención|subvencion|convocatoria|bases\s+reguladoras)\b'
        mascara_ayudas = df['_texto_busqueda'].str.contains(
            patron_ayudas, 
            case=False, 
            regex=True, 
            na=False
        )
        df = df[mascara_ayudas]
    
    # Filtro de palabras clave
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    palabra_escaped = re.escape(palabra)
                    patron = r'(?<![a-záéíóúñ])' + palabra_escaped + r'(?![a-záéíóúñ])'
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
    
    # Limpiar columnas auxiliares
    df = df.drop(columns=['_texto_busqueda'])
    
    if 'Contenido_Completo' in df.columns:
        df = df.drop(columns=['Contenido_Completo'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador de Ayudas y Subvenciones")
st.markdown("**BOJA** (Junta de Andalucía) + **BOE** (Estado) - Con API oficial")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    st.subheader("Fuentes de datos")
    
    # Fuentes recientes
    st.markdown("**📰 Publicaciones recientes**")
    usar_boja = st.checkbox("BOJA (Feed del día)", value=True)
    usar_boe = st.checkbox("BOE (RSS del día)", value=True)
    
    st.markdown("---")
    
    # Búsqueda histórica
    st.markdown("**📅 Búsqueda histórica**")
    usar_boja_hist = st.checkbox(
        "BOJA (Histórico por fechas)", 
        value=False,
        help="Busca en boletines anteriores de BOJA por rango de fechas"
    )
    usar_boe_hist = st.checkbox(
        "BOE (Histórico por fechas - API oficial)", 
        value=False,
        help="Busca en BOE usando la API oficial de sumarios"
    )
    
    # Selector de fechas
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
            st.warning(f"⏱️ Rango amplio ({dias_rango} días). Puede tardar varios minutos.")
    
    st.markdown("---")
    
    st.subheader("🔍 Opciones de búsqueda")
    
    contenido_completo = st.checkbox(
        "🔥 Buscar en contenido completo",
        value=False,
        help="⚠️ MUY LENTO: Descarga y analiza el texto completo. Puede tardar varios minutos."
    )
    
    if contenido_completo:
        st.warning("⏱️ Esta opción puede tardar 5-10 minutos o más con búsquedas históricas.")
    
    st.markdown("---")
    
    st.subheader("Filtros")
    solo_ayudas = st.checkbox("Solo ayudas/subvenciones", value=True)
    palabras_clave = st.text_input(
        "Palabras clave (separadas por coma)", 
        "",
        help="Ejemplo: feder, turismo, pyme"
    )
    
    busqueda_exacta = st.checkbox(
        "Búsqueda de palabra exacta",
        value=True,
        help="Busca 'feder' solo como palabra completa, no dentro de 'confederación'"
    )

# Botón de búsqueda
if st.button("🚀 Buscar", type="primary"):
    if (usar_boja_hist or usar_boe_hist) and fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        st.error("❌ Por favor corrige el rango de fechas antes de buscar")
    else:
        with st.spinner("Buscando en boletines oficiales..."):
            todos_resultados = []
            
            # BOJA Feed reciente
            if usar_boja:
                with st.status("🔎 Buscando en BOJA (feed reciente)..."):
                    todos_resultados.extend(buscar_boja_feed(contenido_completo))
            
            # BOE RSS reciente
            if usar_boe:
                with st.status("🔎 Buscando en BOE (RSS reciente)..."):
                    todos_resultados.extend(buscar_boe_rss(contenido_completo))
            
            # BOJA Histórico
            if usar_boja_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 Buscando en BOJA histórico ({fecha_desde} a {fecha_hasta})..."):
                    todos_resultados.extend(
                        buscar_boja_historico(
                            datetime.combine(fecha_desde, datetime.min.time()),
                            datetime.combine(fecha_hasta, datetime.min.time()),
                            contenido_completo
                        )
                    )
            
            # BOE Histórico
            if usar_boe_hist and fecha_desde and fecha_hasta:
                with st.status(f"🔎 Consultando BOE histórico ({fecha_desde} a {fecha_hasta})..."):
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
                
                # Eliminar duplicados
                df = df.drop_duplicates(subset=['Enlace'], keep='first')
                
                # Aplicar filtros
                lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
                df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas, busqueda_exacta)
                
                # Ordenar por fecha
                df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
                
                # Mostrar resultados
                if len(df_filtrado) > 0:
                    st.success(f"✅ **{len(df_filtrado)} resultados** encontrados (de {len(df)} totales)")
                    
                    # Estadísticas
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total resultados", len(df_filtrado))
                    col2.metric("BOJA", len(df_filtrado[df_filtrado['Boletín'] == 'BOJA']))
                    col3.metric("BOE", len(df_filtrado[df_filtrado['Boletín'] == 'BOE']))
                    
                    # Mostrar tabla
                    st.dataframe(
                        df_filtrado,
                        use_container_width=True,
                        height=600,
                        column_config={
                            "Enlace": st.column_config.LinkColumn("Enlace"),
                            "Fecha": st.column_config.DatetimeColumn(
                                "Fecha", 
                                format="DD/MM/YYYY"
                            )
                        }
                    )
                    
                    # Botón de descarga
                    csv = df_filtrado.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        "📥 Descargar CSV",
                        csv,
                        f"ayudas_subvenciones_{datetime.now().strftime('%Y%m%d')}.csv",
                        "text/csv",
                        key='download-csv'
                    )
                else:
                    st.warning("⚠️ No se encontraron resultados con los filtros aplicados")
                    st.info("💡 **Sugerencias:**\n- Desactiva 'Solo ayudas/subvenciones'\n- Reduce las palabras clave\n- Cambia a búsqueda no exacta\n- Amplía el rango de fechas")
            else:
                st.error("❌ No se pudieron obtener resultados de ninguna fuente")

# Información
with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### Cómo usar esta aplicación
    
    1. **Selecciona las fuentes** que quieres consultar en el panel lateral
       - **Feed del día**: Publicaciones más recientes (rápido)
       - **Histórico**: Busca en fechas anteriores (más lento)
    
    2. **Configura las fechas** (solo para búsqueda histórica)
       - Selecciona el rango "Desde" y "Hasta"
       - Recomendado: máximo 90 días
    
    3. **Activa filtros**
       - ✅ Solo ayudas/subvenciones: Filtra por palabras clave relacionadas
       - 🔍 Palabras clave: Busca términos específicos (ej: "feder, turismo, pyme")
       - 📝 Búsqueda exacta: Encuentra solo palabras completas
    
    4. **Búsqueda en contenido completo** (opcional)
       - ⚠️ Muy lento pero más exhaustivo
       - Descarga cada documento completo
       - Puede tardar 5-10 minutos
    
    5. Haz clic en **🚀 Buscar**
    
    ### Consejos
    
    - ✅ **Recomendado**: Usa feeds del día para búsquedas rápidas
    - ⚡ Para búsquedas históricas, usa la API del BOE (más rápida)
    - 🐌 Evita "contenido completo" para rangos amplios
    - 🔍 Si no encuentras resultados, prueba con menos filtros
    - 📊 Descarga los resultados en CSV para análisis posterior
    
    ### Tipos de búsqueda
    
    **Búsqueda exacta** (activada):
    - "feder" → encuentra: "FEDER", "Feder"
    - "feder" → NO encuentra: "federación", "confederación"
    
    **Búsqueda normal** (desactivada):
    - "feder" → encuentra: "FEDER", "federación", "confederación"
    
    ### Fuentes de datos
    
    - **BOJA**: [www.juntadeandalucia.es/boja](https://www.juntadeandalucia.es/boja)
    - **BOE**: [www.boe.es](https://www.boe.es)
    - **API BOE**: [Documentación oficial](https://www.boe.es/datosabiertos/)
    """)

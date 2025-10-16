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

# ============= FUNCIONES DE BÚSQUEDA MEJORADAS =============

def extraer_contenido_completo(url, max_intentos=2):
    """Extrae el texto completo de una página con mejor manejo de errores"""
    for intento in range(max_intentos):
        try:
            response = session.get(url, timeout=20)  # Timeout más largo
            response.raise_for_status()  # Lanza excepción si status != 200
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Eliminar elementos no deseados
            for element in soup(["script", "style", "nav", "header", "footer", "iframe"]):
                element.decompose()
            
            # Extraer texto limpio
            contenido = soup.get_text(separator=' ', strip=True)
            
            # Rate limiting: esperar entre peticiones
            time.sleep(0.5)
            
            return contenido
            
        except requests.exceptions.Timeout:
            st.warning(f"⏱️ Timeout en {url} (intento {intento + 1}/{max_intentos})")
        except requests.exceptions.HTTPError as e:
            st.warning(f"❌ Error HTTP {e.response.status_code} en {url}")
            break
        except requests.exceptions.RequestException as e:
            st.warning(f"⚠️ Error de conexión: {str(e)[:100]}")
        except Exception as e:
            st.error(f"🔴 Error inesperado: {str(e)[:100]}")
    
    return ""

def buscar_boe_api(fecha_inicio=None, fecha_fin=None, palabras_clave=None):
    """
    Usa la API oficial del BOE (más rápido y confiable)
    Documentación: https://www.boe.es/datosabiertos/
    """
    resultados = []
    
    # Endpoint de la API del BOE
    base_url = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
    
    params = {
        "limit": 100,
        "offset": 0
    }
    
    if fecha_inicio:
        params["from"] = fecha_inicio.strftime("%Y%m%d")
    if fecha_fin:
        params["to"] = fecha_fin.strftime("%Y%m%d")
    
    try:
        response = session.get(
            base_url,
            params=params,
            headers={"Accept": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        
        if data.get("status", {}).get("code") == "200":
            for item in data.get("data", []):
                resultados.append({
                    'Boletín': 'BOE',
                    'Título': item.get('titulo', ''),
                    'Resumen': f"Rango: {item.get('rango', '')} - Dpto: {item.get('departamento', '')}",
                    'Contenido_Completo': "",
                    'Enlace': item.get('url_html_consolidada', ''),
                    'Fecha': pd.to_datetime(item.get('fecha_publicacion', ''), format='%Y%m%d', errors='coerce')
                })
    
    except requests.exceptions.RequestException as e:
        st.error(f"Error al consultar API del BOE: {e}")
    
    return resultados

def buscar_boja_feed(contenido_completo=False):
    """Busca en el feed principal de BOJA con mejor manejo de errores"""
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
            
            # Extraer fecha con mejor manejo
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
    """Busca en el RSS del BOE con mejor manejo de errores"""
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
    
    # Filtro de ayudas/subvenciones MEJORADO
    if solo_ayudas:
        # Palabras completas para evitar falsos positivos
        patron_ayudas = r'\b(ayuda|subvención|subvencion|convocatoria|bases\s+reguladoras)\b'
        mascara_ayudas = df['_texto_busqueda'].str.contains(
            patron_ayudas, 
            case=False, 
            regex=True, 
            na=False
        )
        df = df[mascara_ayudas]
    
    # Filtro de palabras clave (modo OR)
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                if busqueda_exacta:
                    # Escape mejor para caracteres especiales en español
                    palabra_escaped = re.escape(palabra)
                    # Usar límites de palabra flexibles
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

# ============= INTERFAZ (sin cambios funcionales, solo mejoras visuales) =============

st.title("🔍 Buscador de Ayudas y Subvenciones")
st.markdown("**BOJA** (Junta de Andalucía) + **BOE** (Estado) - Con API oficial")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    st.subheader("Fuentes de datos")
    usar_boja = st.checkbox("BOJA (Feed reciente)", value=True)
    usar_boe = st.checkbox("BOE (RSS del día)", value=True)
    usar_boe_api = st.checkbox("🆕 BOE (API oficial - Recomendado)", value=False, 
                               help="Más rápido y confiable que el RSS")
    
    st.markdown("---")
    st.subheader("🔍 Opciones de búsqueda")
    
    contenido_completo = st.checkbox(
        "🔥 Buscar en contenido completo",
        value=False,
        help="⚠️ MUY LENTO: Descarga y analiza el texto completo. Puede tardar varios minutos."
    )
    
    if contenido_completo:
        st.warning("⏱️ Esta opción puede tardar 5-10 minutos y causar bloqueos temporales.")
    
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
    with st.spinner("Buscando en boletines oficiales..."):
        todos_resultados = []
        
        # BOJA Feed
        if usar_boja:
            with st.status("🔎 Buscando en BOJA (feed)..."):
                todos_resultados.extend(buscar_boja_feed(contenido_completo))
        
        # BOE RSS
        if usar_boe:
            with st.status("🔎 Buscando en BOE (RSS)..."):
                todos_resultados.extend(buscar_boe_rss(contenido_completo))
        
        # BOE API (recomendado)
        if usar_boe_api:
            with st.status("🔎 Consultando API oficial del BOE..."):
                fecha_inicio = datetime.now() - timedelta(days=30)
                fecha_fin = datetime.now()
                todos_resultados.extend(buscar_boe_api(fecha_inicio, fecha_fin))
        
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
                st.success(f"✅ {len(df_filtrado)} resultados encontrados (de {len(df)} totales)")
                
                # Mostrar tabla
                st.dataframe(
                    df_filtrado,
                    use_container_width=True,
                    height=600,
                    column_config={
                        "Enlace": st.column_config.LinkColumn("Enlace"),
                        "Fecha": st.column_config.DatetimeColumn(
                            "Fecha", 
                            format="DD/MM/YYYY HH:mm"
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
                st.warning("⚠️ No se encontraron resultados con los filtros aplicados. Prueba a:")
                st.markdown("- Desactivar 'Solo ayudas/subvenciones'")
                st.markdown("- Quitar algunas palabras clave")
                st.markdown("- Cambiar de 'búsqueda exacta' a búsqueda normal")
        else:
            st.error("❌ No se pudieron obtener resultados de ninguna fuente")

# Información
with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### Cómo usar esta aplicación
    
    1. **Selecciona las fuentes** que quieres consultar en el panel lateral
       - 🆕 **API oficial del BOE**: Más rápido y confiable (recomendado)
       - RSS feeds: Actualizaciones del día
       - Contenido completo: Muy lento pero más exhaustivo
    
    2. **Activa filtros** para buscar solo ayudas/subvenciones
    
    3. **Añade palabras clave** específicas (ej: "feder, turismo, pyme")
       - Separa múltiples palabras con comas
       - La búsqueda es tipo OR (encuentra cualquiera de las palabras)
    
    4. **Búsqueda exacta vs normal**:
       - Exacta: "feder" no encuentra "confederación"
       - Normal: "feder" encuentra "feder", "federación", "confederación"
    
    5. Haz clic en **Buscar**
    
    ### Consejos
    
    - ✅ **Recomendado**: Usa la API oficial del BOE para búsquedas rápidas
    - ⚡ Para búsquedas rápidas, usa solo los feeds (sin contenido completo)
    - 🐌 La búsqueda en contenido completo puede tardar 5-10 minutos
    - 🔍 Si no encuentras resultados, prueba sin filtros o con menos palabras clave
    - 📊 Descarga los resultados en CSV para análisis posterior
    
    ### Fuentes de datos
    
    - **BOJA**: [www.juntadeandalucia.es/boja](https://www.juntadeandalucia.es/boja)
    - **BOE**: [www.boe.es](https://www.boe.es)
    - **API BOE**: [Documentación oficial](https://www.boe.es/datosabiertos/)
    """)

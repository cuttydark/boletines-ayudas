import streamlit as st
import requests
import feedparser
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re

st.set_page_config(page_title="Búsqueda Ayudas BOJA/BOE", layout="wide")

# Configuración de sesión HTTP
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

# ============= FUNCIONES DE BÚSQUEDA =============

def buscar_boja_feed():
    """Busca en el feed principal de BOJA"""
    resultados = []
    url = "https://www.juntadeandalucia.es/boja/distribucion/boja.xml"
    
    try:
        response = session.get(url, timeout=15)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            # Extraer fecha
            fecha_str = entry.get('published', entry.get('updated', ''))
            try:
                fecha = pd.to_datetime(fecha_str).tz_localize(None) if fecha_str else pd.NaT
            except:
                fecha = pd.NaT
            
            resultados.append({
                'Boletín': 'BOJA',
                'Título': titulo,
                'Resumen': resumen[:300],
                'Enlace': enlace,
                'Fecha': fecha
            })
    except Exception as e:
        st.warning(f"Error al buscar en BOJA: {e}")
    
    return resultados

def buscar_boja_historico(fecha_inicio, fecha_fin):
    """Busca en BOJA por rango de fechas (números de boletín)"""
    resultados = []
    
    # Calcular números aproximados de boletín
    # BOJA publica ~250 boletines al año
    año_actual = datetime.now().year
    
    # Si las fechas son del año actual, buscamos desde el boletín actual hacia atrás
    dias_desde_inicio_año = (datetime.now() - datetime(año_actual, 1, 1)).days
    numero_aproximado_actual = int(dias_desde_inicio_año * 0.7)  # ~0.7 boletines/día
    
    # Buscar últimos N boletines
    num_boletines = st.session_state.get('num_boletines', 50)
    
    for num in range(max(1, numero_aproximado_actual - num_boletines), numero_aproximado_actual + 1):
        url = f"https://www.juntadeandalucia.es/boja/{año_actual}/{str(num).zfill(3)}/index.html"
        
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Buscar enlaces a disposiciones
                for enlace in soup.find_all('a', href=True):
                    href = enlace['href']
                    if '/boja/' in href and '.html' in href:
                        titulo = enlace.get_text(strip=True)
                        
                        if href.startswith('/'):
                            href = f"https://www.juntadeandalucia.es{href}"
                        
                        resultados.append({
                            'Boletín': 'BOJA',
                            'Título': titulo,
                            'Resumen': f'Boletín núm. {num} de {año_actual}',
                            'Enlace': href,
                            'Fecha': pd.NaT
                        })
        except:
            continue
    
    return resultados

def buscar_boe_rss():
    """Busca en el RSS del BOE"""
    resultados = []
    url = "https://www.boe.es/rss/boe.php"
    
    try:
        response = session.get(url, timeout=15)
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            titulo = entry.get('title', '')
            resumen = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
            enlace = entry.get('link', '')
            
            fecha_str = entry.get('published', entry.get('updated', ''))
            try:
                fecha = pd.to_datetime(fecha_str).tz_localize(None) if fecha_str else pd.NaT
            except:
                fecha = pd.NaT
            
            resultados.append({
                'Boletín': 'BOE',
                'Título': titulo,
                'Resumen': resumen[:300],
                'Enlace': enlace,
                'Fecha': fecha
            })
    except Exception as e:
        st.warning(f"Error al buscar en BOE: {e}")
    
    return resultados

def buscar_boe_historico(fecha_inicio, fecha_fin):
    """Busca en BOE por fechas específicas"""
    resultados = []
    
    fecha_actual = fecha_inicio
    while fecha_actual <= fecha_fin:
        url = f"https://www.boe.es/boe/dias/{fecha_actual.year:04d}/{fecha_actual.month:02d}/{fecha_actual.day:02d}/"
        
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Buscar documentos
                for enlace in soup.find_all('a', href=True):
                    href = enlace['href']
                    if 'BOE-A-' in href or 'txt.php' in href:
                        titulo = enlace.get_text(strip=True)
                        
                        if href.startswith('/'):
                            href = f"https://www.boe.es{href}"
                        
                        resultados.append({
                            'Boletín': 'BOE',
                            'Título': titulo,
                            'Resumen': f'BOE del {fecha_actual.strftime("%d/%m/%Y")}',
                            'Enlace': href,
                            'Fecha': pd.to_datetime(fecha_actual)
                        })
        except:
            pass
        
        fecha_actual += timedelta(days=1)
    
    return resultados

def filtrar_resultados(df, palabras_clave, solo_ayudas=True):
    """Filtra los resultados por palabras clave"""
    if df.empty:
        return df
    
    # Crear columna de texto completo ANTES de filtrar
    df['_texto_busqueda'] = (
        df['Título'].fillna('').astype(str) + ' ' + 
        df['Resumen'].fillna('').astype(str)
    )
    
    # Filtro de ayudas/subvenciones
    if solo_ayudas:
        patron_ayudas = r'ayuda|subvenci|convocatoria|bases reguladoras'
        mascara_ayudas = df['_texto_busqueda'].str.contains(patron_ayudas, case=False, regex=True, na=False)
        df = df[mascara_ayudas]
    
    # Filtro de palabras clave adicionales (modo OR, no AND)
    if palabras_clave:
        mascara_final = pd.Series([False] * len(df), index=df.index)
        
        for palabra in palabras_clave:
            palabra = palabra.strip()
            if palabra:
                mascara_palabra = df['_texto_busqueda'].str.contains(palabra, case=False, regex=False, na=False)
                mascara_final = mascara_final | mascara_palabra
        
        df = df[mascara_final]
    
    # Eliminar columna auxiliar
    df = df.drop(columns=['_texto_busqueda'])
    
    return df

# ============= INTERFAZ =============

st.title("🔍 Buscador de Ayudas y Subvenciones")
st.markdown("**BOJA** (Junta de Andalucía) + **BOE** (Estado)")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuración")
    
    st.subheader("Fuentes de datos")
    usar_boja = st.checkbox("BOJA (Feed reciente)", value=True)
    usar_boja_hist = st.checkbox("BOJA (Histórico)", value=False)
    usar_boe = st.checkbox("BOE (RSS del día)", value=True)
    usar_boe_hist = st.checkbox("BOE (Histórico por fechas)", value=False)
    
    st.markdown("---")
    st.subheader("Filtros")
    solo_ayudas = st.checkbox("Solo ayudas/subvenciones", value=True)
    palabras_clave = st.text_input("Palabras clave (separadas por coma)", "")
    
    st.markdown("---")
    
    # Configuración de búsqueda histórica
    if usar_boja_hist:
        st.subheader("BOJA Histórico")
        st.session_state['num_boletines'] = st.slider(
            "Núm. boletines a revisar", 
            10, 200, 50, 10
        )
    
    if usar_boe_hist:
        st.subheader("BOE Histórico")
        col1, col2 = st.columns(2)
        fecha_desde = col1.date_input("Desde", datetime.now() - timedelta(days=30))
        fecha_hasta = col2.date_input("Hasta", datetime.now())

# Botón de búsqueda
if st.button("🚀 Buscar", type="primary"):
    with st.spinner("Buscando..."):
        todos_resultados = []
        
        # BOJA Feed
        if usar_boja:
            with st.status("Buscando en BOJA (feed)..."):
                todos_resultados.extend(buscar_boja_feed())
        
        # BOJA Histórico
        if usar_boja_hist:
            with st.status("Buscando en BOJA histórico..."):
                todos_resultados.extend(buscar_boja_historico(None, None))
        
        # BOE RSS
        if usar_boe:
            with st.status("Buscando en BOE (RSS)..."):
                todos_resultados.extend(buscar_boe_rss())
        
        # BOE Histórico
        if usar_boe_hist:
            with st.status("Buscando en BOE histórico..."):
                todos_resultados.extend(buscar_boe_historico(fecha_desde, fecha_hasta))
        
        # Crear DataFrame
        if todos_resultados:
            df = pd.DataFrame(todos_resultados)
            
            # Eliminar duplicados
            df = df.drop_duplicates(subset=['Enlace'], keep='first')
            
            # Aplicar filtros
            lista_palabras = [p.strip() for p in palabras_clave.split(',') if p.strip()]
            df_filtrado = filtrar_resultados(df, lista_palabras, solo_ayudas)
            
            # Ordenar por fecha
            df_filtrado = df_filtrado.sort_values('Fecha', ascending=False, na_position='last')
            
            # Mostrar resultados
            st.success(f"✅ {len(df_filtrado)} resultados encontrados (de {len(df)} totales)")
            
            # Mostrar tabla
            st.dataframe(
                df_filtrado,
                use_container_width=True,
                height=600,
                column_config={
                    "Enlace": st.column_config.LinkColumn("Enlace"),
                    "Fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY")
                }
            )
            
            # Botón de descarga
            csv = df_filtrado.to_csv(index=False, encoding='utf-8-sig')
            st.download_button(
                "📥 Descargar CSV",
                csv,
                "ayudas_subvenciones.csv",
                "text/csv"
            )
            
        else:
            st.warning("⚠️ No se encontraron resultados")

# Información
with st.expander("ℹ️ Ayuda"):
    st.markdown("""
    ### Cómo usar esta aplicación
    
    1. **Selecciona las fuentes** que quieres consultar en el panel lateral
    2. **Activa filtros** para buscar solo ayudas/subvenciones
    3. **Añade palabras clave** específicas si buscas algo concreto (ej: "agricultura, turismo")
    4. **Búsqueda histórica**:
       - BOJA: Busca en los últimos N boletines del año actual
       - BOE: Busca en un rango específico de fechas
    5. Haz clic en **Buscar**
    
    ### Consejos
    - Para búsquedas rápidas, usa solo los feeds (BOJA y BOE RSS)
    - Para búsquedas históricas, activa las opciones de histórico
    - Las búsquedas históricas pueden tardar varios minutos
    - Si no encuentras resultados, prueba sin filtros o con menos palabras clave
    """)

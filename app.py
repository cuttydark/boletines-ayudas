# -*- coding: utf-8 -*-
import io
import re
import unicodedata
import datetime as dt
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd

# =========================
# Configuración de página
# =========================
st.set_page_config(
    page_title="BOJA Finder (términos)",
    page_icon="📜",
    layout="wide"
)

BASE = "https://www.juntadeandalucia.es"

# =========================
# Utilidades HTTP (cache)
# =========================
@st.cache_data(show_spinner=False)
def http_get_text(url: str, timeout=30) -> str:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text

@st.cache_data(show_spinner=False)
def http_get_bytes(url: str, timeout=60) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content

# =========================
# Normalización texto
# =========================
def norm(txt: str) -> str:
    if not txt:
        return ""
    # quita acentos y normaliza espacios
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = " ".join(txt.split())
    return txt.lower()

# =========================
# Extractor PDF defensivo
# =========================
def safe_extract_text(pdf_bytes: bytes) -> str:
    """
    Intenta extraer texto con pdfminer.six.
    Si falla, usa PyMuPDF (si está instalado).
    Si todo falla, devuelve "" y la app no revienta.
    """
    # 1) pdfminer.six
    try:
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(pdf_bytes)) or ""
    except Exception:
        pass
    # 2) PyMuPDF (fitz)
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            chunks = []
            for page in doc:
                chunks.append(page.get_text("text") or "")
        return "\n".join(chunks)
    except Exception:
        pass
    # 3) Sin extractor
    st.info("No se pudo validar el PDF (extractor no disponible). Mantengo resultados por HTML/título.")
    return ""

# =========================
# Lógica de calendario/BOJA
# =========================
def dias_en_rango(desde: dt.date, hasta: dt.date):
    cur = desde
    while cur <= hasta:
        yield cur
        cur += dt.timedelta(days=1)

@st.cache_data(show_spinner=False)
def url_sumario_dia(fecha: dt.date) -> str | None:
    """
    Busca el sumario del BOJA del día dado inspeccionando el calendario del año.
    """
    cal_url = f"{BASE}/boja/{fecha.year}/"
    html = http_get_text(cal_url)
    soup = BeautifulSoup(html, "html.parser")
    # enlace cuyo texto es el número de día y acaba en /index.html
    for a in soup.select("a[href*='/boja/'][href$='/index.html']"):
        if a.get_text(strip=True) == str(fecha.day):
            return urljoin(BASE, a["href"])
    return None

@st.cache_data(show_spinner=False)
def extraer_disposiciones(sumario_url: str):
    """
    Devuelve lista de dicts {titulo, url_html} para todas las disposiciones de un sumario.
    Selectores robustos con fallback.
    """
    html = http_get_text(sumario_url)
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # 1) Enlaces directos a disposiciones .html
    for a in soup.select("a[href*='/boja/'][href$='.html']"):
        href = urljoin(BASE, a.get("href"))
        titulo = a.get_text(strip=True)
        if href.endswith(".html"):
            items.append({"titulo": titulo, "url_html": href})

    # 2) Fallback por <li><a>
    if not items:
        for li in soup.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = urljoin(BASE, a["href"])
            if "/boja/" in href and href.endswith(".html"):
                titulo = a.get_text(strip=True) or li.get_text(" ", strip=True)[:140]
                items.append({"titulo": titulo, "url_html": href})

    # 3) Dedup
    seen = set()
    dedup = []
    for it in items:
        if it["url_html"] in seen:
            continue
        seen.add(it["url_html"])
        dedup.append(it)
    return dedup

@st.cache_data(show_spinner=False)
def url_pdf_disposicion(url_html: str) -> str | None:
    html = http_get_text(url_html)
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("a", string=re.compile(r"Descargar PDF", re.I))
    return urljoin(BASE, link["href"]) if link and link.has_attr("href") else None

# =========================
# Búsqueda por términos
# =========================
def parse_terms(entrada: str) -> list[str]:
    """
    Admite términos separados por coma o por salto de línea.
    Ignora vacíos.
    """
    if not entrada:
        return []
    raw = [t.strip() for t in re.split(r"[,\n]+", entrada)]
    return [t for t in raw if t]

def match_terms(texto_norm: str, terms_norm: list[str], modo: str) -> bool:
    """
    modo: "all" -> deben aparecer todos los términos
          "any" -> basta con que aparezca alguno
    """
    if not terms_norm:
        return False
    hits = [t in texto_norm for t in terms_norm]
    return all(hits) if modo == "all" else any(hits)

def buscar(desde: dt.date,
           hasta: dt.date,
           terms: list[str],
           modo_terminos: str,
           buscar_en_titulo: bool,
           validar_pdf: bool,
           debug: bool):
    """
    Devuelve lista de resultados:
    {fecha, titulo, url_html, url_pdf (opcional), fuente_match ('titulo'/'html'/'pdf')}
    """
    terms_norm = [norm(t) for t in terms]
    hallazgos = []

    for dia in dias_en_rango(desde, hasta):
        if debug:
            st.write(f"🔎 Día: {dia.isoformat()}")

        # 1) Localiza sumario
        try:
            sumario = url_sumario_dia(dia)
        except Exception as e:
            if debug:
                st.error(f"❌ Calendario {dia.year}: {e}")
            continue

        if not sumario:
            if debug:
                st.warning(f"⚠️ Sin sumario para {dia} (posible día sin BOJA)")
            continue

        # 2) Extrae disposiciones
        try:
            dispos = extraer_disposiciones(sumario)
            if debug:
                st.write(f"📄 Sumario {dia}: {len(dispos)} disposiciones")
            if debug and dispos:
                st.caption(f"Ejemplo: {dispos[0]['url_html']}")
        except Exception as e:
            if debug:
                st.error(f"❌ Sumario {dia}: {e}")
            continue

        # 3) Evalúa cada disposición
        for disp in dispos:
            titulo_norm = norm(disp["titulo"])
            fuente_match = None

            # 3.1) Título
            if buscar_en_titulo and match_terms(titulo_norm, terms_norm, modo_terminos):
                fuente_match = "titulo"

            # 3.2) HTML
            if not fuente_match:
                try:
                    html_texto = BeautifulSoup(http_get_text(disp["url_html"]), "html.parser").get_text(" ", strip=True)
                    html_norm = norm(html_texto)
                    if match_terms(html_norm, terms_norm, modo_terminos):
                        fuente_match = "html"
                except Exception as e:
                    if debug:
                        st.warning(f"⚠️ No se pudo leer HTML de {disp['url_html']} ({e})")

            # 3.3) PDF (validación opcional)
            pdf_url = None
            if fuente_match and validar_pdf:
                try:
                    pdf_url = url_pdf_disposicion(disp["url_html"])
                    if pdf_url:
                        pdf_norm = norm(safe_extract_text(http_get_bytes(pdf_url)))
                        # Si no valida en PDF, mantenemos el match por título/HTML (no descartamos)
                        if match_terms(pdf_norm, terms_norm, modo_terminos):
                            fuente_match = "pdf"
                except Exception as e:
                    if debug:
                        st.info(f"ℹ️ No se pudo validar PDF ({e}). Se mantiene el match por {fuente_match}.")
            else:
                # si no validamos PDF, al menos intentamos obtener la URL por comodidad
                try:
                    pdf_url = url_pdf_disposicion(disp["url_html"])
                except Exception:
                    pdf_url = None

            if fuente_match:
                hallazgos.append({
                    "fecha": dia.isoformat(),
                    "titulo": disp["titulo"],
                    "url_html": disp["url_html"],
                    "url_pdf": pdf_url,
                    "coincidencia": fuente_match
                })

    return hallazgos

# =========================
# UI
# =========================
st.title("📜 BOJA Finder — Búsqueda por términos (sin regex)")
st.caption("Busca uno o varios términos (separados por coma o salto de línea) en el BOJA entre dos fechas. Acentos insensibles. Opción de validar contra el PDF oficial.")

with st.sidebar:
    st.header("Parámetros")
    colA, colB = st.columns(2)
    desde = colA.date_input("Desde", value=dt.date(2025, 3, 3))
    hasta = colB.date_input("Hasta", value=dt.date(2025, 3, 16))

    terms_input = st.text_area(
        "Términos de búsqueda",
        value="vivienda",
        help="Separa por coma o por salto de línea. Ej.: vivienda, ayudas, alquiler"
    )
    terms = parse_terms(terms_input)

    modo_terminos = st.radio(
        "Coincidencia de términos",
        options=[("Todos", "all"), ("Cualquiera", "any")],
        format_func=lambda x: x[0],
        horizontal=True
    )[1]

    buscar_en_titulo = st.checkbox("Buscar también en el título", value=True)
    validar_pdf = st.checkbox("Validar coincidencia en PDF oficial", value=False)
    debug = st.toggle("Modo diagnóstico", value=True)
    demo = st.toggle("Ejemplo rápido", value=False, help="Ignora parámetros y prueba 2025-03-03 a 2025-03-05 con 'vivienda'.")

# Form para ejecutar bajo demanda
with st.form("buscar_form"):
    lanzador = st.form_submit_button("🔎 Buscar", use_container_width=True)

if lanzador:
    if demo:
        desde = dt.date(2025, 3, 3)
        hasta = dt.date(2025, 3, 5)
        terms = ["vivienda"]
        modo_terminos = "any"
        buscar_en_titulo = True
        validar_pdf = False
        debug = True

    # Validaciones
    if hasta < desde:
        st.error("El rango de fechas es inválido: 'Hasta' es anterior a 'Desde'.")
    elif (hasta - desde).days > 31:
        st.warning("Rango grande. Para ir rápido, prueba ≤ 31 días. Aún así, continúo…")
        with st.spinner("Buscando (rango amplio)…"):
            resultados = buscar(desde, hasta, terms, modo_terminos, buscar_en_titulo, validar_pdf, debug)
        st.success(f"Coincidencias: {len(resultados)}")
    else:
        with st.spinner("Buscando…"):
            resultados = buscar(desde, hasta, terms, modo_terminos, buscar_en_titulo, validar_pdf, debug)
        st.success(f"Coincidencias: {len(resultados)}")

    # Resultados
    if resultados:
        # Tabla compacta con enlaces
        def elink(url): return f"[abrir]({url})" if url else ""
        df = pd.DataFrame(resultados)
        df_view = df.copy()
        df_view["HTML"] = df_view["url_html"].map(elink)
        df_view["PDF"] = df_view["url_pdf"].map(elink)
        df_view = df_view[["fecha", "titulo", "coincidencia", "HTML", "PDF"]]
        st.dataframe(df_view, use_container_width=True, hide_index=True)

        # Listado expandido
        st.divider()
        for r in resultados:
            st.markdown(f"**{r['fecha']} — {r['titulo']}**")
            links = f"[HTML]({r['url_html']})"
            if r["url_pdf"]:
                links += f" · [PDF]({r['url_pdf']})"
            st.write(f"{links}  ·  Coincidencia: **{r['coincidencia']}**")
    else:
        st.info("Sin coincidencias para los parámetros indicados.")

# -*- coding: utf-8 -*-
import io
import re
import datetime as dt
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ---------- Ajustes de página ----------
st.set_page_config(
    page_title="BOJA Finder",
    page_icon="📜",
    layout="wide"
)

BASE = "https://www.juntadeandalucia.es"

# ---------- Utilidades HTTP con cache ----------
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

# ---------- Función DEFENSIVA para PDF ----------
def safe_extract_text(pdf_bytes: bytes) -> str:
    """
    Intenta extraer texto con pdfminer.six.
    Si no está instalado o falla, devuelve cadena vacía y no rompe la app.
    """
    try:
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(pdf_bytes)) or ""
    except Exception:
        # Aviso discreto; no paramos la app
        st.info("No se pudo usar pdfminer.six para validar el PDF. Continuo con coincidencia en HTML.")
        return ""

# ---------- Core scraping ----------
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
    html = http_get_text(sumario_url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # enlaces a las páginas HTML de cada disposición
    for a in soup.select("a[href$='.html']"):
        href = urljoin(BASE, a.get("href"))
        titulo = a.get_text(strip=True)
        if "/boja/" in href and href.endswith(".html"):
            items.append({"titulo": titulo, "url_html": href})
    return items

@st.cache_data(show_spinner=False)
def url_pdf_disposicion(url_html: str) -> str | None:
    html = http_get_text(url_html)
    soup = BeautifulSoup(html, "html.parser")
    # enlace que contenga "Descargar PDF"
    link = soup.find("a", string=re.compile(r"Descargar PDF", re.I))
    return urljoin(BASE, link["href"]) if link and link.has_attr("href") else None

def buscar(desde: dt.date, hasta: dt.date, patron: str, validar_pdf: bool):
    rx = re.compile(patron, re.I)
    hallazgos = []

    for dia in dias_en_rango(desde, hasta):
        try:
            sumario = url_sumario_dia(dia)
        except Exception as e:
            st.warning(f"No pude cargar el calendario de {dia.year} ({e}). Sigo con el siguiente día.")
            continue

        if not sumario:
            # Días sin BOJA publicado
            continue

        try:
            dispos = extraer_disposiciones(sumario)
        except Exception as e:
            st.warning(f"No pude leer el sumario de {dia.isoformat()} ({e}).")
            continue

        for disp in dispos:
            try:
                html_texto = BeautifulSoup(http_get_text(disp["url_html"]), "html.parser").get_text(" ", strip=True)
            except Exception as e:
                st.warning(f"No pude abrir la disposición: {disp['url_html']} ({e}).")
                continue

            if not rx.search(html_texto):
                continue

            pdf_url = None
            if validar_pdf:
                try:
                    pdf_url = url_pdf_disposicion(disp["url_html"])
                    if pdf_url:
                        texto_pdf = safe_extract_text(http_get_bytes(pdf_url))
                        if not texto_pdf or not rx.search(texto_pdf):
                            # Si no valida en PDF, descarta
                            continue
                except Exception:
                    # Si falla la validación PDF, no tumbamos la app; dejamos pasar por HTML
                    pass

            hallazgos.append({
                "fecha": dia.isoformat(),
                "titulo": disp["titulo"],
                "url_html": disp["url_html"],
                "url_pdf": pdf_url
            })

    return hallazgos

# ---------- UI ----------
st.title("📜 BOJA Finder")
st.caption("Busca un patrón (regex o texto literal) en disposiciones del BOJA entre dos fechas. Opción de validar contra el PDF oficial.")

with st.sidebar:
    st.header("Parámetros")
    colA, colB = st.columns(2)
    desde = colA.date_input("Desde", value=dt.date(2025, 3, 3))
    hasta = colB.date_input("Hasta", value=dt.date(2025, 3, 16))
    patron_default = r"\bvivienda\b"
    patron = st.text_input("Patrón de búsqueda (regex)", value=patron_default, help="Usa regex de Python. Ej: \\bvivienda\\b")
    validar_pdf = st.checkbox("Validar coincidencia en PDF oficial", value=True)
    demo = st.toggle("Ejemplo rápido (ignora parámetros)", value=False, help="Usa un rango pequeño conocido para comprobar la app.")
    st.divider()
    with st.popover("Ayuda rápida"):
        st.markdown(
            "- **Patrón** admite regex. Para texto literal, escribe la palabra tal cual.\n"
            "- Si no ves resultados, prueba desmarcar **Validar PDF**.\n"
            "- Los días sin BOJA no devuelven resultados."
        )

# Formulario para ejecutar bajo demanda (evita reruns en cada cambio)
with st.form("buscar_form"):
    lanzador = st.form_submit_button("🔎 Buscar", use_container_width=True)

if lanzador:
    if demo:
        desde = dt.date(2025, 3, 3)
        hasta = dt.date(2025, 3, 5)
        patron = r"\bvivienda\b"

    # Validaciones básicas
    if hasta < desde:
        st.error("El rango de fechas es inválido: 'Hasta' es anterior a 'Desde'.")
    elif (hasta - desde).days > 31:
        st.warning("Rango grande. Para ir rápido, prueba ≤ 31 días. Aún así, continúo…")
        with st.spinner("Buscando (rango amplio)…"):
            resultados = buscar(desde, hasta, patron, validar_pdf)
        st.success(f"Coincidencias: {len(resultados)}")
    else:
        with st.spinner("Buscando…"):
            resultados = buscar(desde, hasta, patron, validar_pdf)
        st.success(f"Coincidencias: {len(resultados)}")

    if resultados:
        # Tabla
        import pandas as pd
        df = pd.DataFrame(resultados)
        # Enlaces clicables
        def elink(url):
            return f"[abrir]({url})" if url else ""
        df_view = df.copy()
        df_view["HTML"] = df_view["url_html"].map(elink)
        df_view["PDF"] = df_view["url_pdf"].map(elink)
        df_view = df_view.drop(columns=["url_html", "url_pdf"])
        st.dataframe(df_view, use_container_width=True, hide_index=True)

        # Listado
        st.divider()
        for r in resultados:
            st.markdown(f"**{r['fecha']} — {r['titulo']}**")
            links = f"[HTML]({r['url_html']})"
            if r["url_pdf"]:
                links += f" · [PDF]({r['url_pdf']})"
            st.write(links)
    else:
        st.info("Sin coincidencias para los parámetros indicados.")

# app.py — BOJA only
import re
from datetime import datetime, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

st.set_page_config(page_title="BOJA · Ayudas y Subvenciones", layout="wide")

# ----------------------------
# Constantes y headers
# ----------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Feeds oficiales (Atom). Algunas secciones pueden no estar siempre activas.
BOJA_FEEDS_MAP = {
    "Boletín completo": "https://www.juntadeandalucia.es/boja/distribucion/boja.xml",
    "Disposiciones generales (S51)": "https://www.juntadeandalucia.es/boja/distribucion/s51.xml",
    # Estas suelen fluctuar/404; déjalas desmarcadas salvo prueba puntual:
    "Otras disposiciones (S63)": "https://www.juntadeandalucia.es/boja/distribucion/s63.xml",
    "Otros anuncios (S69)": "https://www.juntadeandalucia.es/boja/distribucion/s69.xml",
}

# Patrón histórico por número: https://www.juntadeandalucia.es/boja/{year}/{num}/index.html
def boja_index_url(year: int, num: int) -> str:
    return f"https://www.juntadeandalucia.es/boja/{year}/{str(num).zfill(3)}/index.html"

# Heurísticas de detección
KEY_FILTER = re.compile(
    r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b",
    re.I,
)
ENTIDAD_FILTER = re.compile(
    r"\bayuntamiento(s)?\b|\bmunicipio(s)?\b|\bentidad(es)? (local(es)?|pública(s)?)\b|"
    r"\bmancomunidad(es)?\b|\bdiputaci(ón|ones)\b|\buniversidad(es)?\b|\basociaci(ón|ones)\b|"
    r"\bfundaci(ón|ones)\b|\bconsorcio(s)?\b|\bcámara(s)? de comercio\b",
    re.I,
)

# ----------------------------
# Utilidades
# ----------------------------
def parse_date_safe(s: str):
    try:
        return dateparser.parse(s) if s else None
    except Exception:
        return None

def normalize_record(source, title, summary, url, pub_date, raw=""):
    title = (title or "").strip()
    summary = (summary or "").strip()
    text_all = " ".join([title, summary, raw or ""])
    return {
        "boletin": "BOJA",
        "source": source,  # "feed" o "hist"
        "title": title,
        "summary": summary,
        "url": url,
        "pub_date": parse_date_safe(pub_date),
        "is_ayuda_subvencion": bool(KEY_FILTER.search(text_all)),
        "entity_mentions": "; ".join(sorted(set(m.group(0) for m in ENTIDAD_FILTER.finditer(text_all)))),
    }

def apply_filters(df: pd.DataFrame, keywords, use_or: bool, include_all: bool, desde_dt, hasta_dt):
    base_cols = ["boletin","source","title","summary","url","pub_date","is_ayuda_subvencion","entity_mentions"]
    if df.empty:
        return pd.DataFrame(columns=base_cols)

    for c in base_cols:
        if c not in df.columns:
            df[c] = None

    # fechas: a naive
    df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce", utc=True).dt.tz_localize(None)

    if not include_all:
        df = df[df["is_ayuda_subvencion"] == True]

    if keywords:
        t = df["title"].fillna("")
        s = df["summary"].fillna("")
        if use_or:
            mask_total = False
            for kw in keywords:
                m = t.str.contains(kw, case=False) | s.str.contains(kw, case=False)
                mask_total = mask_total | m
            df = df[mask_total]
        else:
            for kw in keywords:
                df = df[t.str.contains(kw, case=False) | s.str.contains(kw, case=False)]

    if desde_dt is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.min) >= desde_dt]
    if hasta_dt is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.max) <= hasta_dt]

    # dedup + orden
    df = df.drop_duplicates(subset=["url"] , keep="first")
    df = df.sort_values("pub_date", ascending=False, na_position="last")
    return df.reset_index(drop=True)

# ----------------------------
# Fetchers (Feeds)
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja_feeds(selected_feed_urls):
    out = []
    for feed_url in selected_feed_urls:
        try:
            r = requests.get(feed_url, headers=DEFAULT_HEADERS, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.text)  # Atom
            for e in feed.entries:
                title = getattr(e, "title", "") or e.get("title", "")
                summary_html = (
                    getattr(e, "summary", "") or e.get("summary", "") or
                    getattr(e, "subtitle", "") or e.get("subtitle", "")
                )
                summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
                link = getattr(e, "link", "") or e.get("link", "")
                pub = (
                    getattr(e, "published", "") or getattr(e, "updated", "") or
                    e.get("published") or e.get("updated")
                )
                out.append(normalize_record("feed", title, summary, link, pub))
        except Exception as ex:
            st.warning(f"BOJA feed error ({feed_url}): {ex}")
            continue
    return out

# ----------------------------
# Fetchers (Histórico por rango)
# ----------------------------
def parse_boja_index(html: str, url: str):
    """Parsea la página index.html de un BOJA concreto y extrae registros."""
    soup = BeautifulSoup(html, "html.parser")
    # título general de página (fallback)
    page_title = (soup.find(["h1", "h2"]).get_text(" ").strip() if soup.find(["h1", "h2"]) else "BOJA")

    # fecha (heurísticas)
    meta = soup.find("meta", {"name":"date"}) or soup.find("meta", {"property":"article:published_time"})
    pub = meta.get("content").strip() if meta and meta.get("content") else None
    if not pub:
        # último recurso: busca ISO en texto
        m = re.search(r"\b\d{4}-\d{2}-\d{2}\b", soup.get_text(" "))
        pub = m.group(0) if m else None

    records = []

    # bloques de disposiciones/noticias (heurística amplia)
    blocks = soup.select("article, .noticia, .disposicion, .resultado, .result, .detalle, li, .listado li")
    if blocks:
        for b in blocks:
            t = b.find(["h2","h3","a"])
            title = t.get_text(" ").strip() if t else page_title
            link = b.find("a")
            href = link.get("href") if link and link.get("href") else url
            # si el href es relativo, úsalo tal cual (la web usa rutas absolutas en general)
            summary = b.get_text(" ").strip()
            records.append(normalize_record("hist", title, summary, href, pub, raw=b.decode()))
    else:
        # si no hay bloques identificables, index como un único registro
        summary = soup.get_text(" ")
        records.append(normalize_record("hist", page_title, summary, url, pub, raw=soup.decode()))

    return records

def fetch_one_boja_number(year: int, num: int):
    url = boja_index_url(year, num)
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        return parse_boja_index(r.text, url)
    except Exception:
        return []

@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja_range(year: int, start_num: int, end_num: int, max_workers: int = 8):
    """Descarga en paralelo un rango de boletines: year/{start..end}/index.html"""
    results = []
    start, end = sorted((start_num, end_num))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one_boja_number, year, n): n for n in range(start, end + 1)}
        for fut in as_completed(futures):
            try:
                recs = fut.result()
                results.extend(recs)
            except Exception:
                continue
    return results

# ----------------------------
# UI
# ----------------------------
st.title("BOJA · Buscador de Ayudas y Subvenciones")

with st.sidebar:
    mode = st.radio("Modo", ["Feeds oficiales", "Histórico por rango"], index=0)

    st.markdown("---")
    st.header("Filtros")
    c1, c2 = st.columns(2)
    desde_d = c1.date_input("Desde", None)
    hasta_d = c2.date_input("Hasta", None)

    kw = st.text_input("Palabras clave (;). Vacío = sin filtro", "")
    use_or = st.toggle("Usar OR entre palabras", value=True)
    include_all = st.toggle("Incluir TODO (ignorar filtro de ayudas/subvenciones)", value=False)
    lim = st.number_input("Límite", 0, 5000, 500, 50)

    if mode == "Feeds oficiales":
        st.markdown("### BOJA · Secciones (Atom)")
        boja_opts = list(BOJA_FEEDS_MAP.keys())
        sel_sections = st.multiselect(
            "Selecciona secciones",
            boja_opts,
            default=["Boletín completo", "Disposiciones generales (S51)"]
        )
        selected_boja_urls = [BOJA_FEEDS_MAP[k] for k in sel_sections]
    else:
        st.markdown("### Rango histórico (por número)")
        year = st.number_input("Año", min_value=2010, max_value=datetime.now().year, value=datetime.now().year, step=1)
        start_num = st.number_input("Número inicial", min_value=1, max_value=400, value=1, step=1)
        end_num   = st.number_input("Número final", min_value=1, max_value=400, value=200, step=1)

    st.markdown("---")
    debug = st.toggle("Mostrar diagnóstico (muestras crudas)", value=False)
    run = st.button("Buscar", type="primary")

if run:
    # date -> datetime naive
    desde_dt = datetime.combine(desde_d, time.min) if desde_d else None
    hasta_dt = datetime.combine(hasta_d, time.max) if hasta_d else None

    keywords = [k.strip() for k in kw.split(";") if k.strip()]

    if mode == "Feeds oficiales":
        raw = fetch_boja_feeds(selected_boja_urls)
        counts = {"BOJA (feeds)": len(raw)}
        df_raw = pd.DataFrame(raw)
    else:
        raw = fetch_boja_range(int(year), int(start_num), int(end_num))
        counts = {"BOJA (histórico)": len(raw)}
        df_raw = pd.DataFrame(raw)

    st.caption("Entradas brutas → " + " | ".join([f"{k}: {v}" for k, v in counts.items()]))

    df = apply_filters(df_raw, keywords, use_or, include_all, desde_dt, hasta_dt)

    if df.empty:
        st.warning(
            "Sin resultados. Sugerencias: "
            "1) activa 'Incluir TODO' para validar que llegan entradas, "
            "2) deja keywords vacías, "
            "3) amplía fechas o el rango de números."
        )
    else:
        st.success(f"{len(df)} resultados")
        st.dataframe(df, use_container_width=True, height=650)
        st.download_button(
            "Descargar CSV",
            df.to_csv(index=False).encode("utf-8"),
            "boja_ayudas.csv",
            "text/csv",
        )

    if debug:
        st.markdown("### Diagnóstico (primeras 10 entradas crudas)")
        st.dataframe(df_raw.head(10), use_container_width=True)

# app.py — BOJA only (histórico robusto)
import re
from datetime import datetime, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

st.set_page_config(page_title="BOJA · Ayudas y Subvenciones (Histórico)", layout="wide")

# ----------------------------
# Constantes y headers
# ----------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Feeds oficiales (Atom) por si quieres usarlos además del histórico
BOJA_FEEDS_MAP = {
    "Boletín completo": "https://www.juntadeandalucia.es/boja/distribucion/boja.xml",
    "Disposiciones generales (S51)": "https://www.juntadeandalucia.es/boja/distribucion/s51.xml",
    # Estas pueden dar 404:
    "Otras disposiciones (S63)": "https://www.juntadeandalucia.es/boja/distribucion/s63.xml",
    "Otros anuncios (S69)": "https://www.juntadeandalucia.es/boja/distribucion/s69.xml",
}

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

SPANISH_MONTHS = {
    "enero":1, "febrero":2, "marzo":3, "abril":4, "mayo":5, "junio":6,
    "julio":7, "agosto":8, "septiembre":9, "setiembre":9, "octubre":10, "noviembre":11, "diciembre":12
}

# ----------------------------
# Utilidades
# ----------------------------
def parse_spanish_date(text: str):
    """Convierte '15 de octubre de 2025' → datetime(2025,10,15) si aparece en el texto."""
    if not text:
        return None
    m = re.search(r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", text, re.I)
    if not m:
        # Prueba dd/mm/yyyy
        m2 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if m2:
            d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            try: return datetime(y, mo, d)
            except: return None
        return None
    d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    mon = SPANISH_MONTHS.get(mon, None)
    if not mon:
        return None
    try:
        return datetime(y, mon, d)
    except:
        return None

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
        "source": source,  # "feed" | "hist"
        "title": title,
        "summary": summary,
        "url": url,
        "pub_date": pub_date,  # datetime o None
        "is_ayuda_subvencion": bool(KEY_FILTER.search(text_all)),
        "entity_mentions": "; ".join(sorted(set(m.group(0) for m in ENTIDAD_FILTER.finditer(text_all)))),
    }

# ----------------------------
# Feeds (opcional)
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja_feeds(selected_feed_urls):
    out = []
    for feed_url in selected_feed_urls:
        try:
            r = requests.get(feed_url, headers=DEFAULT_HEADERS, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for e in feed.entries:
                title = getattr(e, "title", "") or e.get("title", "")
                summary_html = getattr(e, "summary", "") or e.get("summary", "") or getattr(e, "subtitle", "") or e.get("subtitle", "")
                summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
                link = getattr(e, "link", "") or e.get("link", "")
                pub_raw = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
                pub = parse_date_safe(pub_raw)
                out.append(normalize_record("feed", title, summary, link, pub))
        except Exception as ex:
            st.warning(f"BOJA feed error ({feed_url}): {ex}")
            continue
    return out

# ----------------------------
# Histórico por rango
# ----------------------------
SECTION_HREF_PAT = re.compile(r"/boja/\d{4}/\d{3}/(s?\d+\.html|\d+)$")

def parse_boja_section(html: str, url: str, pub_dt):
    """Extrae ítems de una sección (sXX.html o subpágina numérica)."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    # Ítems típicos: artículos, bloques, listados
    for item in soup.select("article, .disposicion, .resultado, .result, .detalle, .noticia, .listado li"):
        # título + enlace
        h = item.find(["h2", "h3", "a"])
        title = h.get_text(" ").strip() if h else soup.title.get_text(" ").strip() if soup.title else "BOJA"
        a = item.find("a", href=True)
        href = a["href"] if a else url
        # normaliza href absoluto si viene relativo
        if href.startswith("/"):
            href = f"https://www.juntadeandalucia.es{href}"
        summary = item.get_text(" ").strip()
        records.append(normalize_record("hist", title, summary, href, pub_dt, raw=item.decode()))
    # fallback: si no hubo bloques, devuelve la página como un registro
    if not records:
        summary = soup.get_text(" ").strip()
        page_title = soup.find(["h1","h2"])
        title = page_title.get_text(" ").strip() if page_title else (soup.title.get_text(" ").strip() if soup.title else "BOJA")
        records.append(normalize_record("hist", title, summary, url, pub_dt, raw=soup.decode()))
    return records

def parse_boja_index(html: str, url: str):
    """Desde el index de un BOJA concreto, detecta fecha y sigue enlaces de secciones."""
    soup = BeautifulSoup(html, "html.parser")
    # 1) Detecta fecha
    text_for_date = " ".join([
        soup.get_text(" "),
        " ".join([m.get("content","") for m in soup.select("meta[name=date], meta[property='article:published_time']")])
    ])
    pub_dt = parse_spanish_date(text_for_date) or parse_date_safe(text_for_date)

    # 2) Encuentra secciones del boletín (sNN.html o subpáginas numéricas)
    section_links = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if SECTION_HREF_PAT.search(href):
            if href.startswith("/"):
                href = f"https://www.juntadeandalucia.es{href}"
            elif href.startswith("http") is False:
                # relativo al índice
                base = url.rsplit("/", 1)[0] + "/"
                href = base + href
            section_links.append(href)
    section_links = sorted(set(section_links))

    # 3) Si no hay secciones, parsea el propio índice como un único registro
    if not section_links:
        return parse_boja_section(html, url, pub_dt)

    # 4) Descarga secciones en paralelo
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for u in section_links:
            futs[ex.submit(requests.get, u, {"headers": DEFAULT_HEADERS, "timeout": 15})] = u
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                r = fut.result()
                # si la firma anterior no pasó kwargs, reintenta simple
                if not hasattr(r, "status_code"):
                    r = requests.get(u, headers=DEFAULT_HEADERS, timeout=15)
                if r.status_code == 200:
                    results.extend(parse_boja_section(r.text, u, pub_dt))
            except Exception:
                continue
    return results

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
    results = []
    start, end = sorted((start_num, end_num))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one_boja_number, year, n): n for n in range(start, end + 1)}
        for fut in as_completed(futures):
            try:
                recs = fut.result() or []
                results.extend(recs)
            except Exception:
                continue
    return results

# ----------------------------
# Filtros
# ----------------------------
def apply_filters(df: pd.DataFrame, keywords, use_or: bool, include_all: bool, desde_dt, hasta_dt):
    base_cols = ["boletin","source","title","summary","url","pub_date","is_ayuda_subvencion","entity_mentions"]
    if df.empty:
        return pd.DataFrame(columns=base_cols)

    for c in base_cols:
        if c not in df.columns:
            df[c] = None

    # fechas → naive
    df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce", utc=True).dt.tz_localize(None)

    # filtro ayudas (si no incluyes todo)
    if not include_all:
        df = df[df["is_ayuda_subvencion"] == True]

    # keywords
    if keywords:
        t = df["title"].fillna("")
        s = df["summary"].fillna("")
        if use_or:
            mask = False
            for kw in keywords:
                m = t.str.contains(kw, case=False) | s.str.contains(kw, case=False)
                mask = mask | m
            df = df[mask]
        else:
            for kw in keywords:
                df = df[t.str.contains(kw, case=False) | s.str.contains(kw, case=False)]

    # fechas — NO expulses NaT: si no hay fecha, se mantiene
    if desde_dt is not None:
        has_date = df["pub_date"].notna()
        df = df[~has_date | (df["pub_date"] >= desde_dt)]
    if hasta_dt is not None:
        has_date = df["pub_date"].notna()
        df = df[~has_date | (df["pub_date"] <= hasta_dt)]

    # dedup por URL + orden
    df = df.drop_duplicates(subset=["url"], keep="first")
    df = df.sort_values("pub_date", ascending=False, na_position="last")
    return df.reset_index(drop=True)

# ----------------------------
# UI
# ----------------------------
st.title("BOJA · Buscador de Ayudas y Subvenciones (Histórico y Feeds)")

with st.sidebar:
    mode = st.radio("Modo", ["Histórico por rango", "Feeds oficiales"], index=0)

    st.header("Filtros")
    c1, c2 = st.columns(2)
    desde_d = c1.date_input("Desde", None)
    hasta_d = c2.date_input("Hasta", None)

    kw = st.text_input("Palabras clave (;). Vacío = sin filtro", "")
    use_or = st.toggle("Usar OR entre palabras", value=True)
    include_all = st.toggle("Incluir TODO (ignorar filtro de ayudas/subvenciones)", value=True)
    lim = st.number_input("Límite", 0, 10000, 1000, 50)

    if mode == "Histórico por rango":
        st.markdown("### Rango histórico (por número)")
        year = st.number_input("Año", min_value=2010, max_value=datetime.now().year, value=datetime.now().year, step=1)
        start_num = st.number_input("Número inicial", min_value=1, max_value=400, value=150, step=1)
        end_num   = st.number_input("Número final",   min_value=1, max_value=400, value=198, step=1)
    else:
        st.markdown("### Feeds oficiales (Atom)")
        boja_opts = list(BOJA_FEEDS_MAP.keys())
        sel_sections = st.multiselect(
            "Secciones",
            boja_opts,
            default=["Boletín completo", "Disposiciones generales (S51)"]  # evita S63 y S69 por 404
        )
        selected_boja_urls = [BOJA_FEEDS_MAP[k] for k in sel_sections]

    debug = st.toggle("Mostrar diagnóstico (muestras crudas)", value=False)
    run = st.button("Buscar", type="primary")

if run:
    desde_dt = datetime.combine(desde_d, time.min) if desde_d else None
    hasta_dt = datetime.combine(hasta_d, time.max) if hasta_d else None
    keywords = [k.strip() for k in kw.split(";") if k.strip()]

    if mode == "Histórico por rango":
        raw = fetch_boja_range(int(year), int(start_num), int(end_num))
        counts = {"BOJA (histórico)": len(raw)}
    else:
        raw = fetch_boja_feeds(selected_boja_urls)
        counts = {"BOJA (feeds)": len(raw)}

    st.caption("Entradas brutas → " + " | ".join([f"{k}: {v}" for k, v in counts.items()]))

    df_raw = pd.DataFrame(raw)
    df = apply_filters(df_raw, keywords, use_or, include_all, desde_dt, hasta_dt)
    if lim:
        df = df.head(int(lim))

    if df.empty:
        st.warning("Sin resultados. Prueba con 'Incluir TODO', keywords vacías y/o amplía el rango de números/fechas.")
    else:
        st.success(f"{len(df)} resultados")
        st.dataframe(df, use_container_width=True, height=650)
        st.download_button("Descargar CSV", df.to_csv(index=False).encode("utf-8"), "boja_ayudas.csv", "text/csv")

    if debug:
        st.markdown("### Diagnóstico (primeras 20 entradas crudas)")
        st.dataframe(df_raw.head(20), use_container_width=True)


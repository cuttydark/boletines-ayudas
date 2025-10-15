# app.py
import re
from datetime import datetime, time
import pandas as pd
import requests, feedparser, streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

st.set_page_config(page_title="Ayudas · BOE · BOJA · DOE", layout="wide")

# ---------- Config ----------
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

# BOE (RSS oficial del sumario del día)
BOE_RSS = "https://www.boe.es/rss/boe.php"

# BOJA (algunas secciones pueden devolver 404 => se ignoran con warning)
BOJA_FEEDS_MAP = {
    "Boletín completo": "https://www.juntadeandalucia.es/boja/distribucion/boja.xml",
    "Disposiciones generales (S51)": "https://www.juntadeandalucia.es/boja/distribucion/s51.xml",
    # Estas dos suelen cambiar o dar 404; déjalas desmarcadas salvo prueba puntual:
    "Otras disposiciones (S63)": "https://www.juntadeandalucia.es/boja/distribucion/s63.xml",
    "Otros anuncios (S69)": "https://www.juntadeandalucia.es/boja/distribucion/s69.xml",
}

# DOE Extremadura (RSS index; usar bytes por codificación)
DOE_RSS = "https://doe.juntaex.es/rss/index.php"

KEY_FILTER = re.compile(
    r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b",
    re.I,
)

# ---------- Utils ----------
def parse_date(s: str):
    try:
        return dateparser.parse(s) if s else None
    except Exception:
        return None

def normalize(src, boletin, title, summary, url, pub_date, raw=""):
    title = (title or "").strip()
    summary = (summary or "").strip()
    text = " ".join([title, summary, raw or ""])
    return {
        "boletin": boletin,
        "source": src,
        "title": title,
        "summary": summary,
        "url": url,
        "pub_date": parse_date(pub_date),
        "is_ayuda_subvencion": bool(KEY_FILTER.search(text)),
    }

# ---------- Fetchers ----------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boe():
    res = []
    try:
        r = requests.get(BOE_RSS, headers=DEFAULT_HEADERS, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        for e in feed.entries:
            title = getattr(e, "title", "") or e.get("title", "")
            summary = BeautifulSoup(getattr(e, "summary", "") or e.get("summary", ""), "html.parser").get_text(" ")
            link = getattr(e, "link", "") or e.get("link", "")
            pub = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
            res.append(normalize("rss", "BOE", title, summary, link, pub))
    except Exception as ex:
        st.warning(f"BOE RSS error: {ex}")
    return res

@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja(selected_feed_urls):
    out = []
    for feed_url in selected_feed_urls:
        try:
            r = requests.get(feed_url, headers=DEFAULT_HEADERS, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.text)  # Atom
            for e in feed.entries:
                title = getattr(e, "title", "") or e.get("title", "")
                summary_html = getattr(e, "summary", "") or e.get("summary", "") or getattr(e, "subtitle", "") or e.get("subtitle", "")
                summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
                link = getattr(e, "link", "") or e.get("link", "")
                pub = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
                out.append(normalize("rss", "BOJA", title, summary, link, pub))
        except Exception as ex:
            st.warning(f"BOJA feed error ({feed_url}): {ex}")
            continue
    return out

@st.cache_data(ttl=600, show_spinner=False)
def fetch_doe():
    res = []
    try:
        r = requests.get(DOE_RSS, headers=DEFAULT_HEADERS, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)  # bytes -> autodetect charset
        for e in feed.entries:
            title = getattr(e, "title", "") or e.get("title", "")
            summary_html = getattr(e, "summary", "") or e.get("summary", "") or getattr(e, "description", "")
            summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
            link = getattr(e, "link", "") or e.get("link", "")
            pub = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
            res.append(normalize("rss", "DOE-EXT", title, summary, link, pub))
    except Exception as ex:
        st.warning(f"DOE RSS error: {ex}")
    return res

# ---------- Pipeline ----------
def run_pipeline(keywords, desde, hasta, limite, use_or=False, include_all=False, selected_boja_urls=None, debug=False):
    raw_boe  = fetch_boe()
    raw_boja = fetch_boja(selected_boja_urls or [])
    raw_doe  = fetch_doe()

    data = raw_boe + raw_boja + raw_doe
    base_cols = ["boletin","source","title","summary","url","pub_date","is_ayuda_subvencion"]
    df = pd.DataFrame(data)
    counts = {"BOE": len(raw_boe), "BOJA": len(raw_boja), "DOE": len(raw_doe)}

    if df.empty:
        return pd.DataFrame(columns=base_cols), counts, {
            "BOE": pd.DataFrame(raw_boe)[:5],
            "BOJA": pd.DataFrame(raw_boja)[:5],
            "DOE": pd.DataFrame(raw_doe)[:5],
        }

    # Columnas + fechas a naive (sin tz)
    for c in base_cols:
        if c not in df.columns:
            df[c] = None

    df["is_ayuda_subvencion"] = df["is_ayuda_subvencion"].fillna(False)
    df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce", utc=True).dt.tz_localize(None)

    # Filtro base (puede desactivarse con include_all)
    if not include_all:
        df = df[df["is_ayuda_subvencion"] == True]

    # Keywords
    if keywords:
        title = df["title"].fillna("")
        summ  = df["summary"].fillna("")
        if use_or:
            mask = False
            for kw in keywords:
                m = title.str.contains(kw, case=False) | summ.str.contains(kw, case=False)
                mask = mask | m
            df = df[mask]
        else:
            for kw in keywords:
                df = df[ title.str.contains(kw, case=False) | summ.str.contains(kw, case=False) ]

    # Fechas
    if desde is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.min) >= desde]
    if hasta is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.max) <= hasta]

    df = df.sort_values("pub_date", ascending=False, na_position="last")
    if limite:
        df = df.head(int(limite))

    debug_samples = None
    if debug:
        debug_samples = {
            "BOE": pd.DataFrame(raw_boe)[:5],
            "BOJA": pd.DataFrame(raw_boja)[:5],
            "DOE": pd.DataFrame(raw_doe)[:5],
        }

    return df.reset_index(drop=True), counts, debug_samples

# ---------- UI ----------
st.title("Buscador de Ayudas y Subvenciones (BOE · BOJA · DOE)")

with st.sidebar:
    st.header("Filtros")
    c1, c2 = st.columns(2)
    desde_d = c1.date_input("Desde", None)
    hasta_d = c2.date_input("Hasta", None)

    kw = st.text_input("Palabras clave (;). Vacío = sin filtro", "")
    use_or = st.toggle("Usar OR entre palabras", value=True)
    include_all = st.toggle("Incluir TODO (ignorar filtro de ayudas/subvenciones)", value=False)
    lim = st.number_input("Límite", 0, 2000, 200, 50)

    st.header("BOJA · Secciones")
    boja_opts = list(BOJA_FEEDS_MAP.keys())
    # Por defecto, evitamos S63 y S69 (404 en tu despliegue)
    sel_sections = st.multiselect(
        "Selecciona secciones BOJA",
        boja_opts,
        default=["Boletín completo", "Disposiciones generales (S51)"]
    )
    selected_boja_urls = [BOJA_FEEDS_MAP[k] for k in sel_sections]

    st.header("Debug")
    debug = st.toggle("Mostrar diagnóstico (muestras crudas)", value=False)

    run = st.button("Buscar", type="primary")

if run:
    # date -> datetime naive
    desde = datetime.combine(desde_d, time.min) if desde_d else None
    hasta = datetime.combine(hasta_d, time.max) if hasta_d else None

    kws = [k.strip() for k in kw.split(";") if k.strip()]
    df, counts, debug_samples = run_pipeline(
        kws, desde, hasta, lim,
        use_or=use_or,
        include_all=include_all,
        selected_boja_urls=selected_boja_urls,
        debug=debug,
    )

    st.caption(f"Entradas brutas → BOE: {counts['BOE']} | BOJA: {counts['BOJA']} | DOE: {counts['DOE']}")

    if df.empty:
        st.warning("Sin resultados. Activa 'Incluir TODO' y deja keywords vacías para validar fuentes. Si BOJA da 404 en S63/S69, deja sólo 'boja.xml' y 'S51'.")
    else:
        st.success(f"{len(df)} resultados")
        st.dataframe(df, use_container_width=True, height=650)
        st.download_button("Descargar CSV", df.to_csv(index=False).encode("utf-8"), "ayudas.csv", "text/csv")

    if debug and debug_samples:
        st.markdown("### Diagnóstico (primeras 5 entradas crudas por fuente)")
        t1, t2, t3 = st.tabs(["BOE", "BOJA", "DOE"])
        with t1:
            st.dataframe(debug_samples["BOE"], use_container_width=True)
        with t2:
            st.dataframe(debug_samples["BOJA"], use_container_width=True)
        with t3:
            st.dataframe(debug_samples["DOE"], use_container_width=True)

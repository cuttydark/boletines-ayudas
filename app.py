import streamlit as st
from datetime import datetime
import pandas as pd
import requests, feedparser, re
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from urllib.parse import urljoin

st.set_page_config(page_title="Ayudas · BOE · BOJA · DOE", layout="wide")

# --- Fuentes oficiales ---
BOE_RSS = "https://www.boe.es/diario_boe/rss.php"
BOE_XML_DOC = "https://www.boe.es/diario_boe/xml.php?id={boe_id}"
BOJA_ULTIMAS = "https://www.juntadeandalucia.es/eboja/últimas"
DOE_RSS = "https://doe.juntaex.es/rss"

KEY_FILTER = re.compile(
    r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b",
    re.I
)

def parse_date(s):
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

@st.cache_data(ttl=600, show_spinner=False)
def fetch_boe():
    feed = feedparser.parse(BOE_RSS)
    res = []
    for e in feed.entries:
        title = getattr(e, "title", "") or e.get("title", "")
        summary_html = getattr(e, "summary", "") or e.get("summary", "")
        summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
        link = getattr(e, "link", "") or e.get("link", "")
        pub = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
        res.append(normalize("rss", "BOE", title, summary, link, pub))
    return res

@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja():
    out = []
    r = requests.get(BOJA_ULTIMAS, timeout=20)
    r.raise_for_status()
    s = BeautifulSoup(r.text, "html.parser")
    for a in s.select("a"):
        href = a.get("href", "")
        if not href:
            continue
        if "eboja" in href:
            url = urljoin(BOJA_ULTIMAS, href)
            try:
                page = requests.get(url, timeout=12)
                page.raise_for_status()
                t = BeautifulSoup(page.text, "html.parser")
                h = t.find(["h1", "h2"])
                title = h.get_text(" ").strip() if h else "BOJA"
                summary = t.get_text(" ")[:800]
                # No siempre hay fecha fiable en estas páginas
                out.append(normalize("scrape", "BOJA", title, summary, url, None))
            except Exception:
                continue
    return out

@st.cache_data(ttl=600, show_spinner=False)
def fetch_doe():
    f = feedparser.parse(DOE_RSS)
    res = []
    for e in f.entries:
        title = getattr(e, "title", "") or e.get("title", "")
        summary_html = getattr(e, "summary", "") or e.get("summary", "")
        summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
        link = getattr(e, "link", "") or e.get("link", "")
        pub = getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated")
        res.append(normalize("rss", "DOE-EXT", title, summary, link, pub))
    return res

def run_pipeline(keywords, desde, hasta, limite):
    data = []
    try:
        data += fetch_boe()
    except Exception:
        pass
    try:
        data += fetch_boja()
    except Exception:
        pass
    try:
        data += fetch_doe()
    except Exception:
        pass

    base_cols = ["boletin", "source", "title", "summary", "url", "pub_date", "is_ayuda_subvencion"]
    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(columns=base_cols)

    for c in base_cols:
        if c not in df.columns:
            df[c] = None
    df["is_ayuda_subvencion"] = df["is_ayuda_subvencion"].fillna(False)
    df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce")

    # Filtro por ayudas/subvenciones
    df = df[df["is_ayuda_subvencion"] == True]

    # Keywords (AND)
    if keywords:
        for kw in keywords:
            mask = (
                df["title"].fillna("").str.contains(kw, case=False) |
                df["summary"].fillna("").str.contains(kw, case=False)
            )
            df = df[mask]

    # Fechas
    if desde is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.min) >= desde]
    if hasta is not None:
        df = df[df["pub_date"].fillna(pd.Timestamp.max) <= hasta]

    df = df.sort_values("pub_date", ascending=False, na_position="last")
    if limite:
        df = df.head(int(limite))

    return df.reset_index(drop=True)

# --- UI ---
st.title("Buscador de Ayudas y Subvenciones (BOE · BOJA · DOE)")

col1, col2 = st.columns(2)
desde = col1.date_input("Desde", None)
hasta = col2.date_input("Hasta", None)
kw = st.text_input("Palabras clave (separa por ;)", "ayuntamiento;convocatoria")
lim = st.number_input("Límite de resultados", min_value=0, max_value=2000, value=200, step=50)

if st.button("Buscar", type="primary"):
    kws = [k.strip() for k in kw.split(";") if k.strip()]
    df = run_pipeline(
        kws,
        datetime.combine(desde, datetime.min.time()) if desde else None,
        datetime.combine(hasta, datetime.max.time()) if hasta else None,
        lim,
    )

    if df.empty:
        st.warning("Sin resultados. Prueba con otras palabras clave o amplía el rango de fechas.")
        st.stop()

    st.success(f"{len(df)} resultados")
    st.dataframe(df, use_container_width=True, height=600)

    st.download_button(
        "Descargar CSV",
        df.to_csv(index=False).encode("utf-8"),
        "ayudas.csv",
        "text/csv",
    )


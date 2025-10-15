# app.py
import re
from datetime import datetime
from urllib.parse import urljoin

import pandas as pd
import requests
import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ----------------------------
# Config de página
# ----------------------------
st.set_page_config(page_title="Ayudas · BOE · BOJA · DOE", layout="wide")

# ----------------------------
# Constantes y headers
# ----------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

BOE_RSS = "https://www.boe.es/diario_boe/rss.php"
DOE_RSS = "https://doe.juntaex.es/rss"  # Extremadura
# BOJA: portada del último BOJA (más estable que /últimas)
BOJA_LAST = "https://www.juntadeandalucia.es/eboja/boja-ultimo.do"

KEY_FILTER = re.compile(
    r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b",
    re.I,
)

# ----------------------------
# Utilidades
# ----------------------------
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


# ----------------------------
# Fetchers robustos
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boe():
    res = []
    try:
        r = requests.get(BOE_RSS, headers=DEFAULT_HEADERS, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        for e in feed.entries:
            title = getattr(e, "title", "") or e.get("title", "")
            summary = BeautifulSoup(
                getattr(e, "summary", "") or e.get("summary", ""), "html.parser"
            ).get_text(" ")
            link = getattr(e, "link", "") or e.get("link", "")
            pub = (
                getattr(e, "published", "")
                or getattr(e, "updated", "")
                or e.get("published")
                or e.get("updated")
            )
            res.append(normalize("rss", "BOE", title, summary, link, pub))
    except Exception as ex:
        st.warning(f"BOE RSS error: {ex}")
    return res


@st.cache_data(ttl=600, show_spinner=False)
def fetch_doe():
    res = []
    try:
        r = requests.get(DOE_RSS, headers=DEFAULT_HEADERS, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        for e in feed.entries:
            title = getattr(e, "title", "") or e.get("title", "")
            summary = BeautifulSoup(
                getattr(e, "summary", "") or e.get("summary", ""), "html.parser"
            ).get_text(" ")
            link = getattr(e, "link", "") or e.get("link", "")
            pub = (
                getattr(e, "published", "")
                or getattr(e, "updated", "")
                or e.get("published")
                or e.get("updated")
            )
            res.append(normalize("rss", "DOE-EXT", title, summary, link, pub))
    except Exception as ex:
        st.warning(f"DOE RSS error: {ex}")
    return res


@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja():
    out = []
    try:
        # Usa la portada del último BOJA y sigue enlaces útiles (sumario/disposiciones)
        r = requests.get(BOJA_LAST, headers=DEFAULT_HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a[href]"):
            href = a["href"]
            if not href:
                continue
            if "eboja" in href and any(k in href for k in ("sumario", "disposiciones", ".html")):
                url = urljoin(BOJA_LAST, href)
                try:
                    p = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
                    p.raise_for_status()
                    t = BeautifulSoup(p.text, "html.parser")
                    h = t.find(["h1", "h2"])
                    title = h.get_text(" ").strip() if h else "BOJA"
                    summary = t.get_text(" ")[:1200]
                    meta = t.find("meta", {"name": "date"}) or t.find(
                        "meta", {"property": "article:published_time"}
                    )
                    pub = meta.get("content") if meta and meta.get("content") else None
                    out.append(normalize("scrape", "BOJA", title, summary, url, pub))
                except Exception:
                    continue
    except Exception as ex:
        st.warning(f"BOJA error: {ex}")
    return out


# ----------------------------
# Pipeline + diagnóstico
# ----------------------------
def run_pipeline(keywords, desde, hasta, limite, use_or=False, debug=False):
    raw_boe = fetch_boe()
    raw_boja = fetch_boja()
    raw_doe = fetch_doe()

    data = raw_boe + raw_boja + raw_doe
    base_cols = [
        "boletin",
        "source",
        "title",
        "summary",
        "url",
        "pub_date",
        "is_ayuda_subvencion",
    ]
    df = pd.DataFrame(data)
    counts = {"BOE": len(raw_boe), "BOJA": len(raw_boja), "DOE": len(raw_doe)}

    if df.empty:
        return pd.DataFrame(columns=base_cols), counts, {
            "BOE": pd.DataFrame(raw_boe)[:5],
            "BOJA": pd.DataFrame(raw_boja)[:5],
            "DOE": pd.DataFrame(raw_doe)[:5],
        }

    # Normaliza columnas
    for c in base_cols:
        if c not in df.columns:
            df[c] = None
    df["is_ayuda_subvencion"] = df["is_ayuda_subvencion"].fillna(False)
    df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce")

    # Filtro base: ayudas/subvenciones
    df = df[df["is_ayuda_subvencion"] == True]

    # Keywords
    if keywords:
        title = df["title"].fillna("")
        summ = df["summary"].fillna("")
        if use_or:
            # OR: al menos una keyword coincide
            mask_total = False
            for kw in keywords:
                m = title.str.contains(kw, case=False) | summ.str.contains(kw, case=False)
                mask_total = mask_total | m
            df = df[mask_total]
        else:
            # AND: deben coincidir todas
            for kw in keywords:
                df = df[
                    title.str.contains(kw, case=False) | summ.str.contains(kw, case=False)
                ]

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


# ----------------------------
# UI
# ----------------------------
st.title("Buscador de Ayudas y Subvenciones (BOE · BOJA · DOE)")

with st.sidebar:
    st.header("Filtros")
    c1, c2 = st.columns(2)
    desde = c1.date_input("Desde", None)
    hasta = c2.date_input("Hasta", None)
    kw = st.text_input("Palabras clave (;). Vacío = sin filtro", "")
    use_or = st.toggle("Usar OR entre palabras", value=True)
    lim = st.number_input("Límite", 0, 2000, 200, 50)

    st.header("Debug")
    debug = st.toggle("Mostrar diagnóstico (muestras crudas)", value=False)

    run = st.button("Buscar", type="primary")

if run:
    kws = [k.strip() for k in kw.split(";") if k.strip()]
    df, counts, debug_samples = run_pipeline(
        kws,
        datetime.combine(desde, datetime.min.time()) if desde else None,
        datetime.combine(hasta, datetime.max.time()) if hasta else None,
        lim,
        use_or=use_or,
        debug=debug,
    )

    st.caption(
        f"Entradas brutas → BOE: {counts['BOE']} | BOJA: {counts['BOJA']} | DOE: {counts['DOE']}"
    )

    if df.empty:
        st.warning(
            "Sin resultados. Prueba a dejar las palabras clave vacías y OR activado, "
            "o amplía el rango de fechas."
        )
    else:
        st.success(f"{len(df)} resultados")
        st.dataframe(df, use_container_width=True, height=650)

        st.download_button(
            "Descargar CSV",
            df.to_csv(index=False).encode("utf-8"),
            "ayudas.csv",
            "text/csv",
        )

    if debug and debug_samples:
        st.markdown("### Diagnóstico (primeras 5 entradas crudas por fuente)")
        t1, t2, t3 = st.tabs(["BOE", "BOJA", "DOE"])
        with t1:
            st.dataframe(debug_samples["BOE"], use_container_width=True)
        with t2:
            st.dataframe(debug_samples["BOJA"], use_container_width=True)
        with t3:
            st.dataframe(debug_samples["DOE"], use_container_width=True)

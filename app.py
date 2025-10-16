# app.py — BOJA + BOE con reintentos y búsqueda histórica
import re
from datetime import datetime, date, time, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="Ayudas/Subvenciones · BOJA + BOE", layout="wide")

# ----------------------------
# HTTP session con reintentos
# ----------------------------
def build_session():
    s = requests.Session()
    retry = Retry(
        total=5, connect=5, read=5, status=5,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    })
    return s

SESSION = build_session()

def http_get(url, timeout=(10, 30)):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r

# ----------------------------
# Constantes
# ----------------------------
# BOJA — variantes por sección (para sortear timeouts/404 puntuales)
BOJA_FEEDS_MAP = {
    "Boletín completo": [
        "https://www.juntadeandalucia.es/boja/distribucion/boja.xml",
        "https://juntadeandalucia.es/boja/distribucion/boja.xml",
        "http://www.juntadeandalucia.es/boja/distribucion/boja.xml",
    ],
    "Disposiciones generales (S51)": [
        "https://www.juntadeandalucia.es/boja/distribucion/s51.xml",
        "https://juntadeandalucia.es/boja/distribucion/s51.xml",
        "http://www.juntadeandalucia.es/boja/distribucion/s51.xml",
    ],
    # Activar solo si funcionan en tu despliegue (suelen fluctuar):
    "Otras disposiciones (S63)": [
        "https://www.juntadeandalucia.es/boja/distribucion/s63.xml",
        "https://juntadeandalucia.es/boja/distribucion/s63.xml",
        "http://www.juntadeandalucia.es/boja/distribucion/s63.xml",
    ],
    "Otros anuncios (S69)": [
        "https://www.juntadeandalucia.es/boja/distribucion/s69.xml",
        "https://juntadeandalucia.es/boja/distribucion/s69.xml",
        "http://www.juntadeandalucia.es/boja/distribucion/s69.xml",
    ],
}

def selected_boja_urls_from_keys(keys):
    # Devuelve las URLs canónicas (primer elemento de cada variante)
    return [BOJA_FEEDS_MAP[k][0] for k in keys]

def boja_index_url(year: int, num: int) -> str:
    return f"https://www.juntadeandalucia.es/boja/{year}/{str(num).zfill(3)}/index.html"

# BOE
BOE_RSS = "https://www.boe.es/rss/boe.php"
def boe_sumario_url(dt: date) -> str:
    return f"https://www.boe.es/boe/dias/{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/"

# ----------------------------
# Heurísticas y utilidades
# ----------------------------
BASE_KEY_RE = re.compile(
    r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b", re.I
)
ENTIDAD_RE = re.compile(
    r"\bayuntamiento(s)?\b|\bmunicipio(s)?\b|\bentidad(es)? (local(es)?|pública(s)?)\b|"
    r"\bmancomunidad(es)?\b|\bdiputaci(ón|ones)\b|\buniversidad(es)?\b|\basociaci(ón|ones)\b|"
    r"\bfundaci(ón|ones)\b|\bconsorcio(s)?\b|\bcámara(s)? de comercio\b", re.I
)
ORGANO_RE = re.compile(
    r"(Consejer[íi]a de [A-ZÁÉÍÓÚÑ][\w\sÁÉÍÓÚÑ\-]+|"
    r"Viceconsejer[íi]a de [A-ZÁÉÍÓÚÑ][\w\sÁÉÍÓÚÑ\-]+|"
    r"Agencia [A-ZÁÉÍÓÚÑ][\w\sÁÉÍÓÚÑ\-]+|"
    r"Servicio Andaluz [\w\sÁÉÍÓÚÑ\-]+|"
    r"Ministerio de [A-ZÁÉÍÓÚÑ][\w\sÁÉÍÓÚÑ\-]+|"
    r"Jefatura del Estado|Secretar[íi]a de Estado [\w\sÁÉÍÓÚÑ\-]+)",
    re.I
)
SPANISH_MONTHS = {
    "enero":1, "febrero":2, "marzo":3, "abril":4, "mayo":5, "junio":6,
    "julio":7, "agosto":8, "septiembre":9, "setiembre":9, "octubre":10, "noviembre":11, "diciembre":12
}
SECTION_HREF_PAT = re.compile(r"/boja/\d{4}/\d{3}/(s?\d+\.html|\d+)$")
DOC_ID_PAT = re.compile(r"txt\.php\?id=BOE-[A-Z]-\d{4}-\d+")

def parse_spanish_date(text: str):
    if not text:
        return None
    m = re.search(r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", text, re.I)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mo = SPANISH_MONTHS.get(mon)
        if mo:
            try: return datetime(y, mo, d)
            except: pass
    m2 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m2:
        d, mo, y = map(int, m2.groups())
        try: return datetime(y, mo, d)
        except: pass
    return None

def parse_date_safe(s: str):
    try:
        return dateparser.parse(s) if s else None
    except Exception:
        return None

def extract_organo(text: str):
    if not text:
        return ""
    found = set(m.group(0).strip() for m in ORGANO_RE.finditer(text))
    return "; ".join(sorted(found))

def normalize_record(boletin, source, title, summary, url, pub_date, raw=""):
    title = (title or "").strip()
    summary = (summary or "").strip()
    text_all = " ".join([title, summary, raw or ""])
    return {
        "boletin": boletin,             # "BOJA" | "BOE"
        "source": source,               # "feed" | "hist" | "rss" | "day"
        "title": title,
        "summary": summary,
        "url": url,
        "pub_date": pub_date,           # datetime | None
        "is_ayuda_subvencion": bool(BASE_KEY_RE.search(text_all)),
        "organo": extract_organo(text_all),
        "entity_mentions": "; ".join(sorted(set(m.group(0) for m in ENTIDAD_RE.finditer(text_all)))),
    }

def to_naive_dt(x):
    dt = pd.to_datetime(x, errors="coerce", utc=True)
    if pd.isna(dt): return pd.NaT
    return dt.tz_localize(None)

# ----------------------------
# BOJA — feeds con variantes + reintentos
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boja_feeds(selected_feed_urls):
    # Mapa alias → conjunto de variantes
    url_aliases = {}
    for variants in BOJA_FEEDS_MAP.values():
        for v in variants:
            url_aliases[v] = variants

    out = []
    for canonical in selected_feed_urls:
        variants = url_aliases.get(canonical, [canonical])
        last_err, ok = None, False
        for u in variants:
            try:
                r = http_get(u, timeout=(10, 30))
                feed = feedparser.parse(r.content)  # bytes: mejor encoding
                for e in feed.entries:
                    title = getattr(e, "title", "") or e.get("title", "")
                    summary_html = (
                        getattr(e, "summary", "") or e.get("summary", "") or
                        getattr(e, "subtitle", "") or e.get("subtitle", "")
                    )
                    summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
                    link = getattr(e, "link", "") or e.get("link", "")
                    pub_raw = (
                        getattr(e, "published", "") or getattr(e, "updated", "") or
                        e.get("published") or e.get("updated")
                    )
                    pub = parse_date_safe(pub_raw)
                    out.append(normalize_record("BOJA", "feed", title, summary, link, pub))
                ok = True
                break
            except Exception as ex:
                last_err = ex
                continue
        if not ok:
            st.warning(f"BOJA feed error ({canonical}): {last_err}")
    return out

# ----------------------------
# BOJA — histórico {año}/{número}
# ----------------------------
def parse_boja_section(html: str, url: str, pub_dt):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for item in soup.select("article, .disposicion, .resultado, .result, .detalle, .noticia, .listado li"):
        h = item.find(["h2","h3","a"])
        title = h.get_text(" ").strip() if h else (soup.title.get_text(" ").strip() if soup.title else "BOJA")
        a = item.find("a", href=True)
        href = a["href"] if a else url
        if href.startswith("/"):
            href = f"https://www.juntadeandalucia.es{href}"
        elif not href.startswith("http"):
            base = url.rsplit("/", 1)[0] + "/"
            href = base + href
        summary = item.get_text(" ").strip()
        records.append(normalize_record("BOJA", "hist", title, summary, href, pub_dt, raw=item.decode()))
    if not records:
        summary = soup.get_text(" ").strip()
        page_title = soup.find(["h1","h2"])
        title = page_title.get_text(" ").strip() if page_title else (soup.title.get_text(" ").strip() if soup.title else "BOJA")
        records.append(normalize_record("BOJA", "hist", title, summary, url, pub_dt, raw=soup.decode()))
    return records

def parse_boja_index(html: str, url: str):
    soup = BeautifulSoup(html, "html.parser")
    text_for_date = " ".join([
        soup.get_text(" "),
        " ".join([m.get("content","") for m in soup.select("meta[name=date], meta[property='article:published_time']")])
    ])
    pub_dt = parse_spanish_date(text_for_date) or parse_date_safe(text_for_date)

    section_links = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if SECTION_HREF_PAT.search(href):
            if href.startswith("/"):
                href = f"https://www.juntadeandalucia.es{href}"
            elif not href.startswith("http"):
                base = url.rsplit("/", 1)[0] + "/"
                href = base + href
            section_links.append(href)
    section_links = sorted(set(section_links))

    if not section_links:
        return parse_boja_section(html, url, pub_dt)

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(http_get, u, (10, 30)): u for u in section_links}
        for fut in as_completed(futures):
            u = futures[fut]
            try:
                r = fut.result()
                results.extend(parse_boja_section(r.text, u, pub_dt))
            except Exception:
                continue
    return results

def fetch_one_boja_number(year: int, num: int):
    url = boja_index_url(year, num)
    try:
        r = http_get(url, timeout=(10, 30))
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
# BOE — RSS y por fechas
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_boe_rss():
    out = []
    try:
        r = http_get(BOE_RSS, timeout=(10, 30))
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            title = getattr(e, "title", "") or e.get("title", "")
            summary_html = getattr(e, "summary", "") or e.get("summary", "")
            summary = BeautifulSoup(summary_html, "html.parser").get_text(" ")
            link = getattr(e, "link", "") or e.get("link", "")
            pub = parse_date_safe(getattr(e, "published", "") or getattr(e, "updated", "") or e.get("published") or e.get("updated"))
            out.append(normalize_record("BOE", "rss", title, summary, link, pub))
    except Exception as ex:
        st.warning(f"BOE RSS error: {ex}")
    return out

def daterange(d0: date, d1: date):
    step = 1 if d0 <= d1 else -1
    cur = d0
    while True:
        yield cur
        if cur == d1: break
        cur = cur + timedelta(days=step)

def parse_boe_sumario(html: str, url: str):
    soup = BeautifulSoup(html, "html.parser")
    text_for_date = " ".join([
        soup.get_text(" "),
        " ".join([m.get("content","") for m in soup.select("meta[name=date], meta[property='article:published_time']")])
    ])
    pub_dt = parse_spanish_date(text_for_date) or parse_date_safe(text_for_date)
    recs = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if DOC_ID_PAT.search(href):
            if href.startswith("/"):
                href = f"https://www.boe.es{href}"
            title = a.get_text(" ").strip()
            parent_text = a.find_parent().get_text(" ").strip() if a.find_parent() else title
            recs.append(normalize_record("BOE", "day", title, parent_text, href, pub_dt))
    if not recs:
        recs.append(normalize_record("BOE", "day", soup.title.get_text(" ").strip() if soup.title else "BOE", soup.get_text(" "), url, pub_dt))
    return recs

@st.cache_data(ttl=600, show_spinner=False)
def fetch_boe_by_dates(start_d: date, end_d: date, max_workers: int = 6):
    results = []
    dates = list(daterange(start_d, end_d))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(http_get, boe_sumario_url(d), (10, 30)): d for d in dates}
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                r = fut.result()
                results.extend(parse_boe_sumario(r.text, boe_sumario_url(d)))
            except Exception:
                continue
    return results

# ----------------------------
# Filtros y pipeline
# ----------------------------
def filter_and_format(df: pd.DataFrame, keywords, use_or: bool, include_all: bool, desde_dt, hasta_dt, limite: int):
    base_cols = ["boletin","source","title","summary","url","pub_date","is_ayuda_subvencion","organo","entity_mentions"]
    if df.empty:
        return pd.DataFrame(columns=base_cols)

    for c in base_cols:
        if c not in df.columns: df[c] = None

    df["pub_date"] = df["pub_date"].apply(lambda x: to_naive_dt(x))
    if not include_all:
        df = df[df["is_ayuda_subvencion"] == True]

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

    # Fechas: no expulsar NaT; solo filtra donde hay fecha
    if desde_dt is not None:
        has_date = df["pub_date"].notna()
        df = df[~has_date | (df["pub_date"] >= desde_dt)]
    if hasta_dt is not None:
        has_date = df["pub_date"].notna()
        df = df[~has_date | (df["pub_date"] <= hasta_dt)]

    df = df.drop_duplicates(subset=["url"], keep="first")
    df = df.sort_values(["pub_date","boletin"], ascending=[False, True], na_position="last")
    if limite:
        df = df.head(int(limite))
    return df.reset_index(drop=True)

def run_pipeline(opts):
    raw = []
    # BOJA
    if opts["use_boja_feeds"]:
        raw += fetch_boja_feeds(opts["boja_feed_urls"])
    if opts["use_boja_hist"]:
        raw += fetch_boja_range(opts["boja_year"], opts["boja_start"], opts["boja_end"])
    # BOE
    if opts["use_boe_rss"]:
        raw += fetch_boe_rss()
    if opts["use_boe_days"] and opts["boe_from_d"] and opts["boe_to_d"]:
        raw += fetch_boe_by_dates(opts["boe_from_d"], opts["boe_to_d"])

    df_raw = pd.DataFrame(raw)
    df = filter_and_format(
        df_raw,
        keywords=opts["keywords"],
        use_or=opts["use_or"],
        include_all=opts["include_all"],
        desde_dt=opts["desde_dt"],
        hasta_dt=opts["hasta_dt"],
        limite=opts["limite"],
    )
    counts = df_raw["boletin"].value_counts().to_dict() if not df_raw.empty else {}
    for k in ["BOJA","BOE"]:
        counts.setdefault(k, 0)
    return df, counts, df_raw

# ----------------------------
# UI
# ----------------------------
st.title("Ayudas y Subvenciones · BOJA + BOE (disposiciones/consejerías + diario)")

with st.sidebar:
    st.header("Fuentes")
    use_boja_feeds = st.checkbox("BOJA · Feeds oficiales (Atom)", value=True)
    use_boja_hist  = st.checkbox("BOJA · Histórico por número", value=False)
    use_boe_rss    = st.checkbox("BOE · RSS del día", value=True)
    use_boe_days   = st.checkbox("BOE · Por fechas (sumario)", value=False)

    st.markdown("---")
    st.header("Filtros de contenido")
    kw = st.text_input("Palabras clave extra (;). Base: ayuda|subvenci|convocatoria|bases reguladoras", "")
    use_or = st.toggle("OR entre palabras extra", value=True)
    include_all = st.toggle("Incluir TODO (ignorar filtro base de ayudas)", value=False)
    lim = st.number_input("Límite total", 0, 10000, 1000, 50)

    st.markdown("---")
    c1, c2 = st.columns(2)
    desde_d = c1.date_input("Fecha desde", None)
    hasta_d = c2.date_input("Fecha hasta", None)

    if use_boja_feeds:
        st.markdown("### BOJA · Feeds")
        boja_opts = list(BOJA_FEEDS_MAP.keys())
        sel_sections = st.multiselect(
            "Secciones",
            boja_opts,
            default=["Boletín completo", "Disposiciones generales (S51)"]  # evita S63/S69 si te dan 404
        )
        boja_feed_urls = selected_boja_urls_from_keys(sel_sections)
    else:
        boja_feed_urls = []

    if use_boja_hist:
        st.markdown("### BOJA · Histórico")
        boja_year  = st.number_input("Año", min_value=2010, max_value=datetime.now().year, value=datetime.now().year, step=1)
        boja_start = st.number_input("Número inicial", min_value=1, max_value=400, value=150, step=1)
        boja_end   = st.number_input("Número final",   min_value=1, max_value=400, value=198, step=1)
    else:
        boja_year, boja_start, boja_end = None, None, None

    if use_boe_days:
        st.markdown("### BOE · Rango de fechas")
        boe_from_d = st.date_input("BOE desde", None, key="boe_from")
        boe_to_d   = st.date_input("BOE hasta", None, key="boe_to")
    else:
        boe_from_d, boe_to_d = None, None

    run = st.button("Buscar", type="primary")

if run:
    opts = {
        "use_boja_feeds": use_boja_feeds,
        "use_boja_hist":  use_boja_hist,
        "use_boe_rss":    use_boe_rss,
        "use_boe_days":   use_boe_days,
        "boja_feed_urls": boja_feed_urls,
        "boja_year":      int(boja_year) if boja_year else None,
        "boja_start":     int(boja_start) if boja_start else None,
        "boja_end":       int(boja_end) if boja_end else None,
        "boe_from_d":     boe_from_d,
        "boe_to_d":       boe_to_d,
        "keywords":       [k.strip() for k in kw.split(";") if k.strip()],
        "use_or":         use_or,
        "include_all":    include_all,
        "desde_dt":       datetime.combine(desde_d, time.min) if desde_d else None,
        "hasta_dt":       datetime.combine(hasta_d, time.max) if hasta_d else None,
        "limite":         int(lim) if lim else 0,
    }

    df, counts, df_raw = run_pipeline(opts)

    st.caption(f"Entradas brutas → BOJA: {counts.get('BOJA',0)} | BOE: {counts.get('BOE',0)}")
    if df.empty:
        st.warning("Sin resultados. Valida fuentes: desactiva keywords extra y, si hace falta, activa 'Incluir TODO'. Si BOJA da timeouts, usa histórico por número.")
    else:
        st.success(f"{len(df)} resultados")
        st.dataframe(df, use_container_width=True, height=700)
        st.download_button("Descargar CSV", df.to_csv(index=False).encode("utf-8"), "ayudas_boja_boe.csv", "text/csv")

    with st.expander("Diagnóstico (primeras 20 entradas crudas)"):
        st.dataframe(df_raw.head(20), use_container_width=True)

import streamlit as st
from datetime import datetime
import pandas as pd
import requests, feedparser, re
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from urllib.parse import urljoin

# --- Fuentes oficiales ---
BOE_RSS = "https://www.boe.es/diario_boe/rss.php"
BOE_XML_DOC = "https://www.boe.es/diario_boe/xml.php?id={boe_id}"
BOJA_ULTIMAS = "https://www.juntadeandalucia.es/eboja/últimas"
DOE_RSS = "https://doe.juntaex.es/rss"

KEY_FILTER = re.compile(r"\bayuda(s)?\b|\bsubvenci(ón|ones)\b|\bconvocatoria(s)?\b|\bbases reguladoras\b", re.I)

def parse_date(s): 
    try: return dateparser.parse(s)
    except: return None

def normalize(src, boletin, title, summary, url, pub_date, raw=""):
    text = " ".join([title or "", summary or "", raw or ""])
    return {
        "boletin": boletin, "source": src,
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "url": url, "pub_date": parse_date(pub_date),
        "is_ayuda_subvencion": bool(KEY_FILTER.search(text))
    }

def fetch_boe():
    feed = feedparser.parse(BOE_RSS)
    res = []
    for e in feed.entries:
        res.append(normalize("rss","BOE", e.title, BeautifulSoup(e.summary, "html.parser").get_text(" "), e.link, e.published))
    return res

def fetch_boja():
    out=[]
    r=requests.get(BOJA_ULTIMAS,timeout=20)
    s=BeautifulSoup(r.text,"html.parser")
    for a in s.select("a"):
        href=a.get("href","")
        if not href: continue
        if "eboja" in href:
            url=urljoin(BOJA_ULTIMAS,href)
            try:
                page=requests.get(url,timeout=10).text
                t=BeautifulSoup(page,"html.parser")
                title=t.find(["h1","h2"])
                title=title.get_text(" ").strip() if title else "BOJA"
                summary=t.get_text(" ")[:800]
                out.append(normalize("scrape","BOJA",title,summary,url,None))
            except: pass
    return out

def fetch_doe():
    f=feedparser.parse(DOE_RSS)
    res=[]
    for e in f.entries:
        res.append(normalize("rss","DOE-EXT",e.title,BeautifulSoup(e.summary,"html.parser").get_text(" "),e.link,e.published))
    return res

def run_pipeline(keywords,desde,hasta,limite):
    data=[]
    data+=fetch_boe()
    data+=fetch_boja()
    data+=fetch_doe()
    df=pd.DataFrame(data)
    df=df[df["is_ayuda_subvencion"]==True]
    if keywords:
        for kw in keywords:
            df=df[df["summary"].str.contains(kw,case=False,na=False)|df["title"].str.contains(kw,case=False,na=False)]
    if desde:
        df=df[df["pub_date"]>=desde]
    if hasta:
        df=df[df["pub_date"]<=hasta]
    df=df.sort_values("pub_date",ascending=False)
    if limite: df=df.head(limite)
    return df.reset_index(drop=True)

# --- Interfaz Streamlit ---
st.title("Buscador de Ayudas y Subvenciones (BOE · BOJA · DOE)")

desde = st.date_input("Desde", None)
hasta = st.date_input("Hasta", None)
kw = st.text_input("Palabras clave (separa por ;)", "ayuntamiento;convocatoria")
lim = st.number_input("Límite de resultados", 0, 1000, 200)
buscar = st.button("Buscar")

if buscar:
    kws=[k.strip() for k in kw.split(";") if k.strip()]
    df=run_pipeline(kws, datetime.combine(desde, datetime.min.time()) if desde else None,
                         datetime.combine(hasta, datetime.max.time()) if hasta else None,
                         lim)
    st.success(f"{len(df)} resultados encontrados")
    st.dataframe(df, use_container_width=True)
    st.download_button("Descargar CSV", df.to_csv(index=False).encode("utf-8"), "ayudas.csv", "text/csv")

import re, io, datetime as dt, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pdfminer.high_level import extract_text

BASE = "https://www.juntadeandalucia.es"

def dias_en_rango(desde: str, hasta: str):
    d0 = dt.datetime.strptime(desde, "%d/%m/%Y").date()
    d1 = dt.datetime.strptime(hasta, "%d/%m/%Y").date()
    cur = d0
    while cur <= d1:
        yield cur
        cur += dt.timedelta(days=1)

def url_sumario_dia(fecha: dt.date):
    # Los sumarios suelen estar enlazados desde el calendario anual.
    # Estrategia: cargar el calendario del año y localizar el <a> del día.
    year = fecha.year
    cal_url = f"{BASE}/boja/{year}/"
    html = requests.get(cal_url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    # Busca el enlace <a> cuyo texto == día ('3', '16', etc.)
    for a in soup.select("a[href*='/boja/'][href$='/index.html']"):
        if a.get_text(strip=True) == str(fecha.day):
            return urljoin(BASE, a["href"])
    return None

def extraer_disposiciones(sumario_url: str):
    html = requests.get(sumario_url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    # En el sumario, cada disposición suele tener un enlace con texto y un "Descargar PDF"
    items = []
    for bloque in soup.select("a[href$='.html']"):
        href = urljoin(BASE, bloque.get("href"))
        titulo = bloque.get_text(strip=True)
        if "/boja/" in href and href.endswith(".html"):
            items.append({"titulo": titulo, "url_html": href})
    return items

def url_pdf_disposicion(url_html: str):
    html = requests.get(url_html, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a", string=re.compile(r"Descargar PDF", re.I))
    return urljoin(BASE, a["href"]) if a else None

def contiene_texto_en_html(url_html: str, patron: re.Pattern):
    html = requests.get(url_html, timeout=30).text
    texto = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return bool(patron.search(texto))

def contiene_texto_en_pdf(url_pdf: str, patron: re.Pattern):
    pdf_bytes = requests.get(url_pdf, timeout=60).content
    texto = extract_text(io.BytesIO(pdf_bytes))
    return bool(patron.search(texto))

def buscar_texto_en_boja(desde, hasta, texto_busqueda, validar_en_pdf=False):
    patron = re.compile(texto_busqueda, re.I)
    hallazgos = []
    for fecha in dias_en_rango(desde, hasta):
        sumario = url_sumario_dia(fecha)
        if not sumario:
            continue
        for disp in extraer_disposiciones(sumario):
            hit_html = contiene_texto_en_html(disp["url_html"], patron)
            hit_pdf = False
            pdf_url = None
            if hit_html and validar_en_pdf:
                pdf_url = url_pdf_disposicion(disp["url_html"])
                if pdf_url:
                    hit_pdf = contiene_texto_en_pdf(pdf_url, patron)
            if hit_html and (not validar_en_pdf or hit_pdf):
                hallazgos.append({
                    "fecha": fecha.isoformat(),
                    "titulo": disp["titulo"],
                    "url_html": disp["url_html"],
                    "url_pdf": pdf_url
                })
    return hallazgos

if __name__ == "__main__":
    resultados = buscar_texto_en_boja(
        desde="03/03/2025",
        hasta="16/03/2025",
        texto_busqueda=r"\bvivienda\b",
        validar_en_pdf=True
    )
    for r in resultados:
        print(f"[{r['fecha']}] {r['titulo']}\n  {r['url_html']}\n  {r.get('url_pdf','')}\n")

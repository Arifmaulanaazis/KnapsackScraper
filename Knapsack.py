import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, BarColumn, TimeElapsedColumn, TextColumn
import logging

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()]
)
log = logging.getLogger("knapsack")


class KnapsackScraper:
    BASE_URL = "https://www.knapsackfamily.com/knapsack_core/result.php"
    DETAIL_URL = "https://www.knapsackfamily.com/knapsack_core/information.php?word="

    def __init__(self, search_type: str = "all", keyword: str = "", max_workers: int = 5):
        self.search_type = search_type
        self.keyword = keyword
        self.max_workers = max_workers

    def build_url(self) -> str:
        encoded_keyword = quote(self.keyword)
        return f"{self.BASE_URL}?sname={self.search_type}&word={encoded_keyword}"

    def fetch_html(self, url: str) -> str:
        log.debug(f"🔗 Fetching URL: {url}")
        response = requests.get(url)
        response.raise_for_status()
        return response.text

    def parse_main_table(self, html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")

        if not table:
            log.warning("⚠️  Tabel tidak ditemukan.")
            return pd.DataFrame()

        data = []
        data.append(["C_ID", "CAS_ID", "Metabolite", "Molecular_Formula", "Mw", "Organism or InChIKey etc."])
        rows = table.find_all("tr")

        for row in rows:
            cols = row.find_all("td")
            data.append([col.get_text(strip=True) for col in cols])

        return pd.DataFrame(data[1:], columns=data[0])


    def parse_organism_table(self, organism_table) -> list:
        organisms = []
        rows = organism_table.find_all("tr")[1:]
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 4:
                organisms.append({
                    "kingdom": cols[0].get_text(strip=True),
                    "family": cols[1].get_text(strip=True),
                    "species": cols[2].get_text(strip=True),
                    "reference": cols[3].get_text(strip=True)
                })
        return organisms

    def get_detail_by_cid(self, cid: str) -> dict:
        try:
            url = f"{self.DETAIL_URL}{quote(cid)}"
            html = self.fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")

            detail = {
                "C_ID": cid,
                "InChIKey": None,
                "InChICode": None,
                "SMILES": None,
                "image_url": None,
                "Organism": None
            }

            table = soup.find("table", class_="d3")
            if table:
                for row in table.find_all("tr"):
                    header = row.find("th", class_="inf")
                    if not header:
                        continue

                    label = header.get_text(strip=True)
                    td = row.find("td")
                    value = td.get_text(strip=True) if td else None

                    if label == "InChIKey":
                        detail["InChIKey"] = value
                    elif label == "InChICode":
                        detail["InChICode"] = value
                    elif label == "SMILES":
                        detail["SMILES"] = value
                    elif label == "Organism":
                        organism_table = row.find_next("table")
                        if organism_table:
                            detail["Organism"] = self.parse_organism_table(organism_table)

            # Gambar
            image_tag = soup.find("img", attrs={"property": "image"})
            if image_tag and image_tag.get("src"):
                detail["image_url"] = f"https://www.knapsackfamily.com{image_tag['src']}"

            log.debug(f"✅ Detail OK: {cid}")
            return detail

        except Exception as e:
            log.error(f"❌ Gagal mengambil detail {cid}: {e}")
            return {
                "C_ID": cid, "InChIKey": None, "InChICode": None,
                "SMILES": None, "image_url": None, "Organism": None
            }

    def search(self) -> pd.DataFrame:
        url = self.build_url()
        log.info(f"🔍 Mencari data untuk keyword: '{self.keyword}' (tipe: {self.search_type})")
        html = self.fetch_html(url)
        df_main = self.parse_main_table(html)

        if df_main.empty:
            log.warning("😢 Tidak ada hasil ditemukan.")
            return df_main

        log.info(f"📄 {len(df_main)} entri ditemukan. Mengambil detail...")

        detail_list = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            transient=True
        ) as progress:
            task = progress.add_task("[cyan]Mengambil detail C_ID...", total=len(df_main))

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self.get_detail_by_cid, cid): cid for cid in df_main["C_ID"]}
                for future in as_completed(futures):
                    detail = future.result()
                    detail_list.append(detail)
                    progress.advance(task)

        df_detail = pd.DataFrame(detail_list)
        df_merged = pd.merge(df_main, df_detail, on="C_ID", how="left")

        log.info("✅ Semua data selesai diambil.")
        return df_merged

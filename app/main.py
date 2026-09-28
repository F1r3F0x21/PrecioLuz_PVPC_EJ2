import io
import json
import logging
import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR.parent / "data" / "pvpc.sqlite3")))
API_URL = os.getenv(
    "PVPC_API_URL",
    "https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real",
)
SOURCE_NAME = "Red Eléctrica (REE) · PVPC peninsular"
logger = logging.getLogger("pvpc")


@contextmanager
def connect_db() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connect_db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS cached_days (
                day TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL,
                source TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS hourly_prices (
                day TEXT NOT NULL,
                hour TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (day, hour),
                FOREIGN KEY (day) REFERENCES cached_days(day) ON DELETE CASCADE
            )"""
        )


def normalize_prices(payload: object) -> list[dict[str, float | str]]:
    unit_divisor = 1
    if isinstance(payload, dict) and isinstance(payload.get("included"), list):
        series = next(
            (
                item
                for item in payload["included"]
                if isinstance(item, dict)
                and (
                    item.get("type") == "PVPC"
                    or item.get("attributes", {}).get("title") == "PVPC"
                )
            ),
            None,
        )
        if series is None:
            raise ValueError("La respuesta de REE no incluye la serie PVPC")
        payload = series.get("attributes", {}).get("values", [])
        unit_divisor = 1000
    elif isinstance(payload, dict):
        payload = payload.get("prices", payload.get("data", []))
    if not isinstance(payload, list):
        raise ValueError("El proveedor ha devuelto un formato de datos inesperado")

    prices = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        hour = entry.get("hour", entry.get("time"))
        timestamp = entry.get("datetime")
        if hour is None and timestamp is not None:
            try:
                hour_number = datetime.fromisoformat(str(timestamp)).hour
            except ValueError:
                continue
            hour = f"{hour_number:02d}-{hour_number + 1:02d}"
        raw_price = entry.get("price", entry.get("value", entry.get("pvpc")))
        if hour is None or raw_price is None:
            continue
        try:
            price = float(str(raw_price).replace(",", ".")) / unit_divisor
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price):
            continue
        prices.append({"hour": str(hour), "price": price})

    prices.sort(key=lambda item: item["hour"])
    if not prices:
        raise ValueError("El proveedor no ha devuelto precios horarios")
    return prices


def fetch_prices(day: date) -> list[dict[str, float | str]]:
    params = urlencode(
        {
            "start_date": f"{day.isoformat()}T00:00",
            "end_date": f"{day.isoformat()}T23:59",
            "time_trunc": "hour",
        }
    )
    separator = "&" if "?" in API_URL else "?"
    url = f"{API_URL}{separator}{params}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "PVPC-Consulta/1.0"})
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"REE respondió con HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"No se pudo contactar con REE: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("La consulta a REE agotó el tiempo de espera") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("REE no devolvió una respuesta JSON válida") from error
    try:
        return normalize_prices(payload)
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def read_cached_day(day: date) -> list[dict[str, float | str]] | None:
    with connect_db() as connection:
        cached = connection.execute(
            "SELECT 1 FROM cached_days WHERE day = ?", (day.isoformat(),)
        ).fetchone()
        if cached is None:
            return None
        rows = connection.execute(
            "SELECT hour, price FROM hourly_prices WHERE day = ? ORDER BY hour",
            (day.isoformat(),),
        ).fetchall()
    return [dict(row) for row in rows]


def store_day(day: date, prices: list[dict[str, float | str]]) -> None:
    with connect_db() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO cached_days(day, fetched_at, source) VALUES (?, ?, ?)",
            (day.isoformat(), datetime.now(timezone.utc).isoformat(), SOURCE_NAME),
        )
        connection.execute("DELETE FROM hourly_prices WHERE day = ?", (day.isoformat(),))
        connection.executemany(
            "INSERT INTO hourly_prices(day, hour, price) VALUES (?, ?, ?)",
            [(day.isoformat(), item["hour"], item["price"]) for item in prices],
        )


def get_day_prices(day: date) -> tuple[list[dict[str, float | str]], bool]:
    cached = read_cached_day(day)
    if cached is not None:
        return cached, True
    prices = fetch_prices(day)
    store_day(day, prices)
    return prices, False


def parse_requested_date(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        requested = date.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="La fecha debe tener formato AAAA-MM-DD") from error
    if requested > date.today():
        raise HTTPException(status_code=400, detail="No hay precios publicados para fechas futuras")
    return requested


def collect_prices(day: date) -> dict:
    try:
        selected, cache_hit = get_day_prices(day)
    except RuntimeError as error:
        logger.warning("Falló la consulta para %s: %s", day, error)
        raise HTTPException(status_code=502, detail=str(error)) from error

    week = []
    missing_days = []
    for offset in range(6, -1, -1):
        current_day = day - timedelta(days=offset)
        try:
            daily_prices, _ = get_day_prices(current_day)
        except RuntimeError:
            missing_days.append(current_day.isoformat())
            continue
        week.extend({"day": current_day.isoformat(), **item} for item in daily_prices)

    selected_with_date = [{"day": day.isoformat(), **item} for item in selected]
    minimum = min(week, key=lambda item: item["price"]) if week else None
    maximum = max(week, key=lambda item: item["price"]) if week else None
    return {
        "date": day.isoformat(),
        "prices": selected_with_date,
        "week": week,
        "extremes": {"minimum": minimum, "maximum": maximum},
        "missing_days": missing_days,
        "cache_hit": cache_hit,
        "source": SOURCE_NAME,
    }


def format_eur(value: float) -> str:
    return f"{value:.5f}".replace(".", ",")


def create_pdf(report: dict) -> io.BytesIO:
    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    ink = colors.HexColor("#173b37")
    muted = colors.HexColor("#627670")
    line = colors.HexColor("#d5e2da")
    green = colors.HexColor("#18a987")
    gold = colors.HexColor("#efd15b")
    red = colors.HexColor("#df604f")
    prices = report["prices"]
    week = report["week"]
    minimum = report["extremes"]["minimum"]
    maximum = report["extremes"]["maximum"]

    pdf.setTitle(f"Precios PVPC - {report['date']}")
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(48, page_height - 52, "Precio de la luz · PVPC")
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(48, page_height - 71, f"Precios horarios del {report['date']} · zona peninsular")

    chart_left, chart_bottom, chart_width, chart_height = 76, 510, page_width - 124, 180
    if week:
        days = sorted({item["day"] for item in week})
        cell_width = chart_width / 24
        cell_height = chart_height / len(days)
        minimum_price = min(item["price"] for item in week)
        maximum_price = max(item["price"] for item in week)

        def heat_color(value: float) -> colors.Color:
            ratio = 0.5 if maximum_price == minimum_price else (value - minimum_price) / (maximum_price - minimum_price)
            start, end, amount = (green, gold, ratio * 2) if ratio <= 0.5 else (gold, red, (ratio - 0.5) * 2)
            return colors.Color(
                start.red + (end.red - start.red) * amount,
                start.green + (end.green - start.green) * amount,
                start.blue + (end.blue - start.blue) * amount,
            )

        pdf.setFont("Helvetica", 7)
        for row, day in enumerate(days):
            y = chart_bottom + (len(days) - row - 1) * cell_height
            pdf.setFillColor(ink if day == report["date"] else muted)
            pdf.drawRightString(chart_left - 7, y + cell_height / 2 - 2, day[8:10])

        for item in week:
            hour = int(item["hour"].split("-", 1)[0])
            row = days.index(item["day"])
            x = chart_left + hour * cell_width
            y = chart_bottom + (len(days) - row - 1) * cell_height
            pdf.setFillColor(heat_color(item["price"]))
            pdf.rect(x + 0.7, y + 0.7, cell_width - 1.4, cell_height - 1.4, fill=1, stroke=0)
            is_extreme = item == minimum or item == maximum
            if is_extreme:
                pdf.setStrokeColor(ink)
                pdf.setLineWidth(1.2)
                pdf.rect(x + 1, y + 1, cell_width - 2, cell_height - 2, fill=0, stroke=1)

        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 7)
        for hour in range(0, 24, 6):
            x = chart_left + hour * cell_width + cell_width / 2
            pdf.drawCentredString(x, chart_bottom - 11, f"{hour:02d}")

        ramp_width, ramp_height = 82, 5
        ramp_x = chart_left + (chart_width - ramp_width) / 2
        ramp_y = chart_bottom - 28
        for segment in range(36):
            ratio = segment / 35
            pdf.setFillColor(heat_color(minimum_price + ratio * (maximum_price - minimum_price)))
            pdf.rect(ramp_x + segment * ramp_width / 36, ramp_y, ramp_width / 36 + 0.2, ramp_height, fill=1, stroke=0)
        pdf.setFillColor(muted)
        pdf.drawString(chart_left, ramp_y, "Más barato")
        pdf.drawRightString(chart_left + chart_width, ramp_y, "Más caro")
        pdf.drawCentredString(ramp_x + ramp_width / 2, ramp_y - 9, "Borde oscuro: mínimo y máximo")

    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(48, 458, "Detalle horario")
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(page_width - 48, 458, "Importes en €/kWh")
    table_top = 438
    column_width = (page_width - 96) / 2
    row_height = 15
    for column in range(2):
        x = 48 + column * column_width
        pdf.setFillColor(ink)
        pdf.rect(x, table_top - 2, column_width - 10, 17, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(x + 7, table_top + 4, "Hora")
        pdf.drawRightString(x + column_width - 17, table_top + 4, "Precio")
    half = (len(prices) + 1) // 2
    for column, subset in enumerate((prices[:half], prices[half:])):
        x = 48 + column * column_width
        for row_number, item in enumerate(subset):
            y = table_top - 17 - row_number * row_height
            is_minimum = minimum and item["hour"] == minimum["hour"] and report["date"] == minimum["day"]
            is_maximum = maximum and item["hour"] == maximum["hour"] and report["date"] == maximum["day"]
            pdf.setFillColor(green if is_minimum else red if is_maximum else ink)
            pdf.setFont("Helvetica-Bold" if is_minimum or is_maximum else "Helvetica", 8)
            pdf.drawString(x + 7, y, str(item["hour"]))
            pdf.drawRightString(x + column_width - 17, y, f"{format_eur(item['price'])} €/kWh")
            pdf.setStrokeColor(line)
            pdf.line(x + 6, y - 4, x + column_width - 16, y - 4)

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(48, 35, f"Fuente: {report['source']} · Extremos calculados sobre los días disponibles de la semana.")
    pdf.drawRightString(page_width - 48, 35, "Consulta informativa")
    pdf.save()
    buffer.seek(0)
    return buffer


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Consulta PVPC", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/prices")
def prices(date_value: str | None = Query(default=None, alias="date")):
    return collect_prices(parse_requested_date(date_value))


@app.get("/api/export.pdf")
def export_pdf(date_value: str | None = Query(default=None, alias="date")):
    report = collect_prices(parse_requested_date(date_value))
    if not report["prices"]:
        raise HTTPException(status_code=404, detail="No hay precios para exportar en esa fecha")
    filename = f"pvpc-{report['date']}.pdf"
    return StreamingResponse(
        create_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
def health():
    return {"ok": True, "database": str(DATABASE_PATH)}
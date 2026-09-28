# Luz en claro · consulta PVPC

Aplicación web para consultar precios horarios de la tarifa PVPC en la zona peninsular, comparar los últimos siete días y descargar un informe en PDF.

## Interfaz

Usa Tabler Core 1.6.0, un kit de dashboard HTML/CSS gratuito bajo licencia MIT, para los controles y la tabla. La gráfica es una matriz de calor de siete días por 24 horas: turquesa indica los precios más bajos, amarillo los intermedios y coral los más altos; un borde marca los extremos semanales. El CSS de Tabler y las fuentes se cargan desde CDN; la aplicación conserva su hoja de estilos local como base.

## Ejecutar con Docker Desktop

Con Docker Desktop iniciado, abre PowerShell en esta carpeta y ejecuta:

```powershell
docker compose up --build -d
```

Abre <http://localhost:8001>. Para detenerla: `docker compose down`. Los datos consultados se conservan en el volumen `pvpc-data`. El puerto del equipo se puede cambiar definiendo `PVPC_PORT` antes de iniciar Compose.

## Ejecutar con Python

Requiere Python 3.11 o superior.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

La base de datos se crea en `data/pvpc.sqlite3`. `DATABASE_PATH` permite cambiar su ubicación.

## Funcionamiento

- Los precios se obtienen de la API pública de Red Eléctrica (`apidatos.ree.es`), seleccionando la serie `PVPC` del sistema peninsular. Se solicita una fecha con `start_date`, `end_date` y `time_trunc=hour`.
- REE devuelve el PVPC en €/MWh; el backend divide cada valor entre 1.000 y la aplicación lo presenta en €/kWh. Se admiten también precios negativos.
- La URL base se puede cambiar con `PVPC_API_URL`; la fuente alternativa debe ofrecer el mismo esquema JSON de REE.
- Cada fecha descargada se guarda en SQLite. Si se vuelve a consultar, se leen los datos locales y no se repite la petición.
- Para comparar siete días, se consultan la fecha elegida y los seis días anteriores. Si falla la fuente algún día, se muestran los datos disponibles y se informa del hueco.
- El PDF incluye el gráfico semanal, los precios horarios del día elegido y los extremos del periodo.

La aplicación depende de la disponibilidad y el formato de la API pública de REE. Si la fuente cambia, hay que actualizar `PVPC_API_URL` o adaptar `fetch_prices` y `normalize_prices` en `app/main.py`. Los errores de conexión y las respuestas HTTP se devuelven en la página para que no parezca que la consulta sigue cargando.
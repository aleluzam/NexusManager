# ingestion-service

Microservicio de ingesta: recibe lotes (videos + txt/docx), guarda los originales,
los corta en clips normalizados y prepara cada clip para describirlo.

## Arrancar
    cp .env.example .env      # cambia API_KEY: el servicio NO arranca sin ella
    docker compose up --build

`API_KEY` es obligatoria. Si falta, o si se deja con el valor de ejemplo
`cambia-esta-clave`, el proceso muere al arrancar con un mensaje que lo explica.
No hay clave por defecto en el código, porque una clave por defecto en un
repositorio es una clave pública. Genera una con:

    python -c "import secrets; print(secrets.token_urlsafe(32))"

## Probar
El servicio se publica en el **puerto 8001 del host** (el contenedor escucha en 8000, que
ya ocupa el backend principal).

    API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2-)

    curl -X POST http://localhost:8001/lotes \
      -H "x-api-key: $API_KEY" \
      -F "files=@entrevista.mp4" -F "files=@contexto.txt"

    curl http://localhost:8001/lotes/1 -H "x-api-key: $API_KEY"

Documentación interactiva: http://localhost:8001/docs

## Tests
Se ejecutan dentro del contenedor, que ya tiene las dependencias de la app:

    docker compose run --rm \
      -v ./tests:/srv/tests \
      -v ./requirements-dev.txt:/srv/requirements-dev.txt:ro \
      api sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"

Dos advertencias que cuestan un disgusto si no se saben:

- **Los tests corren contra el `app/` de la IMAGEN, no contra tu directorio de
  trabajo.** El `Dockerfile` hace `COPY app ./app`, así que la copia queda
  congelada en el momento del build: si editas `app/` y lanzas los tests sin
  reconstruir, se ejecutan contra el código viejo y el resultado no dice nada.
  Reconstruye primero: `docker compose build`.
- El comando tiene que ser `python -m pytest`: `pytest` a secas desde este
  directorio no encuentra el paquete `app`.

Usan su propia base de datos (`ingesta_test`) y `tmp_path` para el storage, nunca
la de desarrollo.

## Estructura de storage
    storage/originales/lote_1/...        videos y documentos originales
    storage/clips/lote_1/video_1/...     clips normalizados
    storage/modelos/...                  cache de modelos de HuggingFace (HF_HOME)

## Siguiente paso
`procesar_clip` en app/tasks.py tiene los TODO: transcripción, descripción visual,
ficha JSON y embedding.

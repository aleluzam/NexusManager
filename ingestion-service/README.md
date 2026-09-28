# ingestion-service

Microservicio de ingesta: recibe lotes (videos + txt/docx), guarda los originales,
los corta en clips normalizados y prepara cada clip para describirlo.

## Arrancar
    cp .env.example .env      # cambia API_KEY
    docker compose up --build

## Probar
    curl -X POST http://localhost:8000/lotes \
      -H "x-api-key: cambia-esta-clave" \
      -F "files=@entrevista.mp4" -F "files=@contexto.txt"

    curl http://localhost:8000/lotes/1 -H "x-api-key: cambia-esta-clave"

Documentación interactiva: http://localhost:8000/docs

## Estructura de storage
    storage/originales/lote_1/...        videos y documentos originales
    storage/clips/lote_1/video_1/...     clips normalizados

## Siguiente paso
`procesar_clip` en app/tasks.py tiene los TODO: transcripción, descripción visual,
ficha JSON y embedding.

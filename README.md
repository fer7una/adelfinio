# Adelfinio - TikTok Historia de Espana (MVP tecnico)

Base tecnica para un sistema de produccion diaria de 4 videos (2 principales + 2 teasers) con revision humana previa al envio para revision manual en TikTok.

## Stack

- Docker + Docker Compose
- n8n self-hosted
- PostgreSQL (persistencia)
- Redis (colas/locks)
- MinIO (assets)
- Python 3.11+ (utilidades)

## Estructura

- `workflows/`: workflows de n8n versionables
- `schemas/`: JSON Schemas del dominio
- `data/`: estado de personajes, timeline, episodios y planes diarios
- `prompts/`: prompts y plantillas de generacion
- `scripts/`: utilidades operativas
- `docs/`: arquitectura, roadmap, operating model y supuestos

## Requisitos previos

1. Docker Engine 24+ y Docker Compose v2
2. Python 3.11+
3. Git

## Arranque en frio

1. Clonar o abrir este repo en VS Code.
2. Copiar variables de entorno:

```bash
cp .env.example .env
```

3. Completar secretos en `.env` (todos los `REPLACE_WITH_*` y `TODO_*`).
4. Levantar servicios:

```bash
docker compose up -d
```

5. Comprobar estado:

```bash
docker compose ps
```

6. Acceder a:
- n8n: `http://localhost:5678`
- MinIO API: `http://localhost:9000`
- MinIO Console: `http://localhost:9001`

## Scripts utiles

- Validar JSON contra schemas:

```bash
python3 scripts/validate_json.py
```

- Generar plan diario (2 principales + 2 teasers):

```bash
python3 scripts/generate_daily_plan.py --date 2026-03-15
```

- Bootstrap rapido:

```bash
bash scripts/bootstrap.sh
```

## Workflows n8n

Los archivos en `workflows/` son esqueletos importables y versionables.

- Import manual: UI de n8n -> Workflows -> Import from file.
- Import/export por API: ver `scripts/import_workflows.sh` y `scripts/export_workflows.sh`.

## Git flow recomendado

- Rama estable: `main`
- Rama de integracion: `develop`
- Features: `feature/<scope>`
- Hotfixes: `hotfix/<scope>`

Detalles en `CONTRIBUTING.md`.

# Local Video Pipeline (step by step)

Objetivo: validar rapido el flujo local de produccion de video sin depender aun de TikTok API.

Objetivos de duracion:

- Episodio principal: 90-120 segundos (maximo 180)
- Teaser: 15-30 segundos

## Requisitos

1. Python 3.11+
2. ffmpeg instalado en el sistema (`ffmpeg -version`)
3. Dependencia de validacion opcional: `python3 -m pip install jsonschema`
4. Dependencia para generacion IA (imagen + voz): `python3 -m pip install openai`

Instalacion rapida de ffmpeg:

- Ubuntu/WSL: `sudo apt update && sudo apt install -y ffmpeg`
- macOS (Homebrew): `brew install ffmpeg`
- Windows (winget): `winget install Gyan.FFmpeg`

## Paso 1: validar contratos base

```bash
python3 scripts/validate_json.py
```

## Paso 2: extraer eventos desde cronicas

```bash
python3 scripts/extract_source_events.py --sources \
  docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf \
  --overwrite
```

Nota importante:

- Si el PDF viene con DRM/cifrado (EBX_HANDLER), exporta antes a `.txt` o `.md` y usa ese archivo como source.

## Paso 3: construir personajes, emociones y arco

```bash
python3 scripts/build_character_bible.py --events data/timeline/source_events.json --overwrite
```

Salida esperada:

- `data/characters/<character_id>.json`
- `data/characters/timelines/<character_id>.json`

Validar:

```bash
python3 scripts/validate_generated_characters.py
```

## Paso 4: generar plan diario

```bash
python3 scripts/generate_daily_plan.py --date 2026-03-15 --reset-progression
```

Salida esperada:

- `data/daily_plan/plan-20260315.json`

Notas:

- El script corre en modo estricto: sin fuente valida no crea plan.
- El avance lineal entre dias se guarda en `data/timeline/progression_state.json`.
- Para volver al inicio de la cronologia, usar `--reset-progression`.

## Paso 5: generar episodios (2 main + 2 teaser)

```bash
python3 scripts/generate_episodes_from_plan.py --plan data/daily_plan/plan-20260315.json
```

Salida esperada:

- `data/episodes/generated/plan-20260315/*.json`

Validar episodios:

```bash
python3 scripts/validate_generated_episodes.py --dir data/episodes/generated/plan-20260315
```

## Paso 6: generar video final (imagen IA + voz IA + montaje)

Generar assets por escena (OpenAI):

```bash
python3 scripts/generate_scene_assets.py \
  --episode data/episodes/generated/plan-20260315/main-20260315-linea_01.json
```

Componer MP4 final con subtitulos incrustados:

```bash
python3 scripts/compose_final_video.py \
  --episode data/episodes/generated/plan-20260315/main-20260315-linea_01.json
```

SVG opcionales para narracion/dialogo/grito:

- `assets/video_overlays/narration.svg`
- `assets/video_overlays/dialogue.svg`
- `assets/video_overlays/shout.svg`
- Tambien puedes cambiar la carpeta con `--overlay-assets-dir` o `VIDEO_OVERLAY_ASSETS_DIR`.

Salida esperada:

- `artifacts/scene_assets/<episode_id>/scenes/*.png`
- `artifacts/scene_assets/<episode_id>/scenes/*.mp3`
- `artifacts/videos/final/<episode_id>.mp4`
- `artifacts/subtitles/final/<episode_id>.srt`

Ejecucion en lote (todos los episodios del plan):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315
```

Directo desde el `plan.json`:

```bash
bash scripts/run_final_ai_video_pipeline_from_plan.sh data/daily_plan/plan-20260315.json
```

Modo mock sin llamadas a API (solo para test de pipeline):

```bash
bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315 --mock
```

## Smoke test de un comando

```bash
bash scripts/run_local_first_batch.sh 2026-03-15 --reset-progression
```

Este comando ejecuta:

1. Generacion de `daily_plan`
2. Construccion de character bible
3. Generacion de 4 episodios JSON
4. Render final mock de 1 principal + 1 teaser

## Que valida este pipeline

- Estructura de datos y consistencia de IDs
- Presencia de plot twist en episodios `main`
- Continuidad emocional y de arco por personaje (`character_beats`)
- Generacion de assets por escena y composicion final
- Punto de integracion para voz, imagen IA y subida TikTok

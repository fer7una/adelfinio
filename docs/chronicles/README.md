# Chronicles Source Folder

Fuente canonica para extraer eventos historicos.

## Regla operativa

- Archivo principal actual:
  - `01-La gran aventura del reino de Asturias.pdf`
- El pipeline trabaja en modo estricto: sin fuente valida no se genera plan ni episodios.

## Formatos soportados por `scripts/extract_source_events.py`

- `.txt`
- `.md`
- `.json` (eventos ya normalizados)
- `.pdf` (texto embebido o sidecar OCR)

## PDF escaneado (sin texto)

Si el PDF no tiene capa de texto, usa sidecar OCR:

1. Generar texto OCR:

```bash
bash scripts/ocr_pdf_to_sidecar.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\" \
  \"docs/chronicles/sidecar_text/01-La gran aventura del reino de Asturias.txt\" \
  1 120
```

2. Extraer eventos desde PDF + sidecar:

```bash
python3 scripts/extract_source_events.py \
  --sources \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\" \
  --pdf-sidecar-dir docs/chronicles/sidecar_text \
  --overwrite
```

## Pipeline grafico completo desde libro

```bash
bash scripts/run_graphic_story_from_book.sh \
  \"docs/chronicles/01-La gran aventura del reino de Asturias.pdf\" \
  2026-03-15 \
  --reset-progression
```

Este comando encadena:

1. Ingesta de eventos
2. Character bible + arcos emocionales
3. Plan diario 2+2
4. Episodios JSON con beats de personaje
5. Render visual (si `ffmpeg` esta instalado)

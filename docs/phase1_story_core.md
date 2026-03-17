# Fase 1 - Story Core Fuente-First

## Alcance

1. Ingesta de cronicas a `source_pack.json` con OCR y chunks trazables
2. Revision humana obligatoria antes de cualquier generacion narrativa
3. Construccion de `character_bible.json` por chat con evidencia e inferencia etiquetada
4. Generacion de `story_catalog.json` lineal
5. Generacion de `episode.json` dramatico final con trazabilidad por escena
6. Duracion target:
   - Main: 90-150s (max 180)

## Scripts clave

- `scripts/build_source_pack.py`
- `scripts/review_source_pack.py`
- `scripts/generate_character_bible.py`
- `scripts/generate_story_catalog.py`
- `scripts/generate_episode_from_story.py`
- `scripts/validate_generated_episodes.py`

## Contratos nuevos

- `schemas/source_pack.schema.json`
- `schemas/character_bible.schema.json`
- `schemas/story_catalog.schema.json`
- `schemas/character_timeline.schema.json`
- `schemas/character.schema.json` (compatibilidad de render)
- `schemas/episode.schema.json` (ampliado con evidencia, casting y beats dramaticos)

## Restricciones strict-mode

- Sin `source_pack.review.status=approved`: no hay personajes, catalogo ni episodio.
- Sin `story_id` seleccionado: no hay episodio final.
- Sin evidencia o inferencia etiquetada: no hay escena valida.

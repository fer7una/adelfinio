# Fase 1 - Story Core (implementado)

## Alcance

1. Ingesta de cronicas a timeline normalizada (`source_events.json`)
2. Construccion de character bible por actor
3. Registro de emociones y arco por personaje/evento
4. Generacion de episodios con `character_beats`
5. Duraciones target:
   - Main: 90-120s (max 180)
   - Teaser: 15-30s

## Scripts clave

- `scripts/extract_source_events.py`
- `scripts/build_character_bible.py`
- `scripts/generate_daily_plan.py`
- `scripts/generate_episodes_from_plan.py`
- `scripts/validate_generated_episodes.py`

## Contratos nuevos

- `schemas/character_timeline.schema.json`
- `schemas/character.schema.json` (extendido con `emotional_state` y `arc_state`)
- `schemas/episode.schema.json` (duraciones por tipo y `character_beats` en principales)

## Restricciones strict-mode

- Sin eventos de fuente: no hay plan.
- Sin actor en evento: no hay episodio.
- Sin archivo de personaje/timeline para actor: no hay episodio.

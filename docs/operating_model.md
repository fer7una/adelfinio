# Operating Model Diario

## Objetivo

- Producir una historia historica de alta fidelidad a partir de una fuente canonica revisada.

## Ciclo diario

1. 08:00 - Ingesta de nueva fuente y construccion de `source_pack`
2. 08:30 - Revision humana del OCR, chunks y cronologia
3. 09:00 - Generacion de `character_bible`
4. 10:00 - Generacion de `story_catalog`
5. 10:30 - Seleccion manual del `story_id`
6. 11:00 - Generacion del `episode.json` final
7. 12:00 - Render y revision humana narrativa/visual
8. 17:00 - Revision de metricas, errores, costes y backlog

## Roles

- IA Automation Lead: mantiene workflows, calidad tecnica y observabilidad
- Editor humano: aprueba fuente, elige historia y valida el episodio final
- Operaciones: monitoriza SLAs de produccion diaria

## Estados operativos

- `draft` -> `generated` -> `pending_review` -> `approved_for_inbox` -> `sent_to_tiktok_inbox`
- Estado alternativo: `needs_changes` con razon y accion recomendada

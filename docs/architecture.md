# Arquitectura MVP

## Diagrama textual

1. `docs/chronicles/*` -> Ingesta y normalizacion (`01_ingesta_normalizacion`)
2. Eventos normalizados -> timeline lineal (`data/timeline/*`)
3. Story engine (`02_story_engine_lineal`) crea episodios candidatos y actualiza estado de personajes (`data/characters/*`)
4. Generacion de videos principales (`03_generacion_principales`) con plot twist obligatorio
5. Generacion de teasers (`04_generacion_teasers`) a partir de principales
6. Revision humana (`05_revision_humana`) con estados: `pending_review`, `approved`, `needs_changes`
7. Envio a TikTok para revision manual/inbox (`06_publicacion_tiktok_inbox`)
8. Observabilidad y reintentos (`07_observabilidad_reintentos`) con metricas, alertas y control de costes

## Componentes

- n8n: orquestacion de workflows
- PostgreSQL: persistencia de ejecuciones y metadatos n8n
- Redis: colas/locks y soporte para escalado posterior
- MinIO: almacenamiento de assets intermedios/finales
- Scripts Python: validacion de contratos JSON, normalizacion y planificacion diaria

## Principios de diseno

- Contratos JSON primero (schemas versionados)
- Flujo determinista cuando sea posible
- Human-in-the-loop obligatorio antes de envio
- Observabilidad desde Fase 0

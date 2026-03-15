# Operating Model Diario

## Objetivo

- Producir 4 piezas/dia: 2 principales + 2 teasers

## Ciclo diario

1. 08:00 - Ingesta de nuevas fuentes y normalizacion
2. 08:30 - Generacion de plan diario (`daily_plan`)
3. 09:00 - Generacion de 2 principales con plot twist
4. 11:00 - Generacion de 2 teasers
5. 12:00 - Revision humana (contenido + cumplimiento)
6. 13:00 - Envio a TikTok en modo revision manual/inbox
7. 17:00 - Revision de metricas, errores, costes y backlog

## Roles

- IA Automation Lead: mantiene workflows, calidad tecnica y observabilidad
- Editor humano: aprueba o solicita cambios antes de TikTok
- Operaciones: monitoriza SLAs de produccion diaria

## Estados operativos

- `draft` -> `generated` -> `pending_review` -> `approved_for_inbox` -> `sent_to_tiktok_inbox`
- Estado alternativo: `needs_changes` con razon y accion recomendada

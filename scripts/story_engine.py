#!/usr/bin/env python3
"""Shared storytelling helpers for daily plans and episodes."""

from __future__ import annotations

import re

META_NOISE_HINTS = [
    "en este episodio",
    "plot twist",
    "fuente secundaria",
    "narracion dominante",
    "continuidad narrativa",
    "cta al episodio",
    "contexto historico contrastado",
]
HISTORIOGRAPHY_NOISE_HINTS = [
    "la crónica de albelda",
    "crónica de albelda",
    "fechada en 881",
    "fechada en 882",
    "en tiempos de alfonso iii",
    "lo relató así",
    "dicen las viejas crónicas",
    "podemos reconstruirlo",
    "muchos historiadores",
]
COMMON_OCR_FIXES = {
    r"\bAsi\b": "Así",
    r"\bAs i\b": "Así",
    r"\bsigo\s+VIII\b": "siglo VIII",
    r"\brendicion\b": "rendición",
    r"\bmurio\b": "murió",
}
SPANISH_STOPWORDS = {
    "a",
    "al",
    "algo",
    "ante",
    "bajo",
    "cada",
    "como",
    "con",
    "contra",
    "cual",
    "cuando",
    "de",
    "del",
    "desde",
    "donde",
    "dos",
    "el",
    "ella",
    "ellas",
    "ellos",
    "en",
    "entre",
    "era",
    "es",
    "esa",
    "ese",
    "eso",
    "esta",
    "este",
    "estos",
    "fue",
    "ha",
    "hasta",
    "hay",
    "la",
    "las",
    "le",
    "les",
    "lo",
    "los",
    "mas",
    "más",
    "mientras",
    "muy",
    "ni",
    "no",
    "nos",
    "o",
    "para",
    "pero",
    "por",
    "que",
    "se",
    "segun",
    "según",
    "si",
    "sin",
    "sobre",
    "su",
    "sus",
    "tambien",
    "también",
    "todo",
    "todos",
    "tras",
    "un",
    "una",
    "uno",
    "y",
    "ya",
}
TWIST_MARKERS = [
    "pero",
    "sin embargo",
    "hasta que",
    "entonces",
    "ocurría",
    "ocurria",
    "treta",
    "traidor",
    "se negó",
    "murió",
    "murio",
    "derrota",
    "diezm",
]
ROLE_HOLD_SECONDS = {
    "hook": 2,
    "setup": 2,
    "pressure": 2,
    "terrain": 3,
    "turn": 2,
    "choice": 2,
    "clash": 2,
    "outcome": 3,
    "bridge": 2,
    "question": 2,
    "legacy": 2,
}
ROLE_TRANSITIONS = {
    ("hook", "setup"): "El conflicto ya está abierto, pero el verdadero problema aún no se ha visto entero.",
    ("setup", "pressure"): "La amenaza se concreta ahora en una desventaja difícil de sostener.",
    ("pressure", "terrain"): "Cuando todo parece inclinarse, el terreno cambia las reglas.",
    ("terrain", "turn"): "La montaña contiene el golpe, pero alguien intenta romper la defensa por otro lado.",
    ("turn", "choice"): "La oferta no cierra nada: fuerza una decisión sin vuelta atrás.",
    ("choice", "clash"): "La respuesta ya está dada; falta ver cuánto cuesta sostenerla.",
    ("clash", "outcome"): "El choque abre el desenlace, pero todavía no muestra todo su precio.",
    ("question", "question"): "Cada pregunta no resuelve la anterior: la vuelve más peligrosa.",
    ("question", "bridge"): "Las preguntas ya empujan el relato hacia una grieta aún mayor.",
    ("bridge", "legacy"): "El retroceso termina justo donde empieza el siguiente abismo.",
}


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def repair_common_ocr_issues(text: str) -> str:
    repaired = text
    for pattern, replacement in COMMON_OCR_FIXES.items():
        repaired = re.sub(pattern, replacement, repaired, flags=re.IGNORECASE)
    return normalize_ws(repaired)


def trim_text(text: str, max_len: int) -> str:
    compact = repair_common_ocr_issues(text)
    if len(compact) <= max_len:
        return compact
    cut = compact[: max_len + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = cut[:max_len]
    return cut.rstrip(".,;: ") + "..."


def clean_scene_sentence(sentence: str, max_len: int = 220) -> str:
    base = trim_text(repair_common_ocr_issues(sentence), max_len)
    base = re.sub(r"^(as[ií]),?\s+en\s+fin,?\s*", "", base, flags=re.IGNORECASE)
    base = re.sub(r"^(pero|y|pues|entonces)\s+", "", base, flags=re.IGNORECASE)
    base = base.strip(" ,;:")
    if base:
        base = base[0].upper() + base[1:]
    if base and base[-1] not in ".!?":
        base = base + "."
    return base


def actor_display_name(actor_id: str) -> str:
    parts = actor_id.split("_")
    if parts and parts[-1] in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}:
        return " ".join([p.capitalize() for p in parts[:-1]] + [parts[-1].upper()])
    return " ".join(p.capitalize() for p in parts)


def actor_is_explicitly_in_summary(label: str, summary: str) -> bool:
    low = repair_common_ocr_issues(summary).lower()
    label_low = label.lower()
    if label_low == "pelayo":
        return "pelayo" in low
    if label_low == "don oppas":
        return "oppas" in low or "obispo" in low
    if label_low == "al qama":
        return "al qama" in low or "alkama" in low
    compact = re.sub(r"\s+", r"\\s+", re.escape(label_low))
    return re.search(rf"\b{compact}\b", low) is not None


def explicit_story_actor_ids(actor_ids: list[str], summary: str) -> list[str]:
    filtered = [actor_id for actor_id in actor_ids if actor_is_explicitly_in_summary(actor_display_name(actor_id), summary)]
    return filtered or actor_ids


def story_cast(actor_names: list[str], summary: str) -> list[str]:
    cast: list[str] = []
    for name in actor_names:
        norm = normalize_ws(name)
        if norm and norm not in cast and actor_is_explicitly_in_summary(norm, summary):
            cast.append(norm)
    lower = summary.lower()
    inferred = [
        ("Pelayo", r"\bpelayo\b"),
        ("Don Oppas", r"\boppas\b|\bobispo\b"),
        ("Al Qama", r"\bal\s*qama\b"),
        ("Alfonso I", r"\balfonso\s*i\b"),
        ("Alfonso II", r"\balfonso\s*ii\b"),
    ]
    for label, pattern in inferred:
        if re.search(pattern, lower) and label not in cast:
            cast.append(label)
    if not cast:
        cast.append("Pelayo")
    return cast


def pick_story_roles(cast_names: list[str]) -> dict:
    lead = next((name for name in cast_names if "pelayo" in name.lower()), cast_names[0] if cast_names else "Pelayo")
    rival = next((name for name in cast_names if "qama" in name.lower()), "Al Qama")
    bishop = next((name for name in cast_names if "oppas" in name.lower() or "obispo" in name.lower()), "Don Oppas")
    ally = next((name for name in cast_names if name not in {lead, rival, bishop}), "Guerrero astur")
    return {
        "lead": lead,
        "rival": rival,
        "bishop": bishop,
        "ally": ally,
        "captain": f"Capitán de {rival}",
        "chronicler": "Cronista",
    }


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def infer_story_mode(summary: str) -> str:
    low = repair_common_ocr_issues(summary).lower()
    if summary.count("?") >= 2 or "serie de preguntas" in low or "vamos a contestar" in low:
        return "inquiry"
    return "chronicle"


def split_candidate_sentences(summary: str) -> list[str]:
    clean = repair_common_ocr_issues(summary.replace("...", ". "))
    chunks = re.split(r"(?<=[.!?])\s+|;\s+", clean)
    out: list[str] = []
    for chunk in chunks:
        sentence = chunk.strip(" \"'“”")
        if len(sentence) < 30:
            continue
        if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", sentence)) < 8:
            continue
        low = sentence.lower()
        if any(meta in low for meta in META_NOISE_HINTS):
            continue
        if any(meta in low for meta in HISTORIOGRAPHY_NOISE_HINTS):
            continue
        out.append(sentence)
    return out


def split_story_fragments(summary: str) -> list[str]:
    base_sentences = split_candidate_sentences(summary)
    fragments: list[str] = []
    for sentence in base_sentences:
        parts = re.split(r",\s+|:\s+|\s+y\s+|\s+pero\s+", normalize_ws(sentence))
        for part in parts:
            clean = normalize_ws(part).strip(" .;:")
            if len(clean) < 28:
                continue
            if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", clean)) < 5:
                continue
            fragments.append(clean)
    if not fragments:
        return base_sentences
    deduped: list[str] = []
    seen: set[str] = set()
    for frag in fragments:
        key = frag.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(frag)
    return deduped


def extract_questions(summary: str) -> list[str]:
    repaired = repair_common_ocr_issues(summary)
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in repaired.split("?"):
        segment = normalize_ws(raw).strip(" \"'“”")
        if not segment:
            continue
        match = re.search(r"(cómo|quién(?:es)?|qué)[^.!?]*$", segment, flags=re.IGNORECASE)
        if not match:
            continue
        question = normalize_ws(match.group(0))
        question = re.sub(r"^[Yy],?\s*", "", question)
        key = question.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(question)
    return deduped


def words_in_text(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", normalize_ws(text))


def estimate_block_seconds(text: str, speaker: str = "", delivery: str = "normal") -> int:
    words = len(words_in_text(text))
    base_wps = 2.55
    low = normalize_ws(speaker).lower()
    if "guerrero" in low or "capit" in low:
        base_wps = 2.7
    elif "obispo" in low or "oppas" in low:
        base_wps = 2.2
    elif speaker:
        base_wps = 2.45
    delivery_factor = 1.12 if delivery == "shout" else 1.0
    effective_wps = max(1.2, base_wps * delivery_factor)
    commas = len(re.findall(r"[,;:]", text))
    sentence_ends = len(re.findall(r"[.!?]", text))
    duration = (words / effective_wps) + (0.18 * commas) + (0.28 * sentence_ends) + 0.35
    if words <= 3:
        duration = max(duration, 1.25)
    elif words <= 8:
        duration = max(duration, 2.0)
    return max(1, round(duration))


def estimate_scene_timing(narration: str, dialogue: list[dict], story_role: str) -> dict:
    narration_seconds = estimate_block_seconds(narration, "", "normal")
    dialogue_seconds = 0
    dialogue_words = 0
    for row in dialogue:
        line = normalize_ws(str(row.get("line", "")))
        speaker = normalize_ws(str(row.get("speaker", "")))
        delivery = normalize_ws(str(row.get("delivery", "normal"))) or "normal"
        dialogue_seconds += estimate_block_seconds(line, speaker, delivery)
        dialogue_words += len(words_in_text(line))
    narration_words = len(words_in_text(narration))
    hold_seconds = ROLE_HOLD_SECONDS.get(story_role, 2)
    total = narration_seconds + dialogue_seconds + hold_seconds
    return {
        "narration_seconds": narration_seconds,
        "dialogue_seconds": dialogue_seconds,
        "visual_hold_seconds": hold_seconds,
        "target_duration_seconds": total,
        "narration_words": narration_words,
        "dialogue_words": dialogue_words,
        "total_words": narration_words + dialogue_words,
    }


def infer_scene_phase(text: str, mode: str) -> str:
    low = repair_common_ocr_issues(text).lower()
    if mode == "inquiry":
        if contains_any(low, ["pregunta", "explicar", "aclarar", "saber", "contestar", "origen", "por qué", "por que"]):
            return "question"
        if contains_any(low, ["retroced", "volver atrás", "volver atras", "empezar por la primera"]):
            return "bridge"
        if contains_any(low, ["después de covadonga", "despues de covadonga", "qué pasó", "que paso"]):
            return "legacy"
        return "question"
    if contains_any(low, ["diezm", "victoria", "derrota", "desenlace", "resultado"]):
        return "outcome"
    if contains_any(low, ["obispo", "oppas", "entreg", "rendición", "rendicion", "acuerdo diplomático"]):
        return "turn"
    if contains_any(low, ["terreno", "valles", "montes", "carreteras", "puentes", "desfiladero", "palmo"]):
        return "terrain"
    if contains_any(low, ["pocos", "alimentos", "muchos", "armados", "efectivos", "armamento"]):
        return "pressure"
    if contains_any(low, ["negó", "nego", "rechaza", "respuesta"]):
        return "choice"
    if contains_any(low, ["batalla", "combate", "choque", "luchar", "lucha"]):
        return "clash"
    return "setup"


def compress_source_line(fragment: str, max_len: int = 150) -> str:
    return trim_text(clean_scene_sentence(fragment), max_len)


def role_visual_focus(story_role: str, source_beat: str, location: str, roles: dict) -> str:
    lead = roles["lead"]
    rival = roles["rival"]
    bishop = roles["bishop"]
    low = source_beat.lower()
    if story_role == "hook":
        return f"Plano general de {location}, valle cerrado y ambos bandos ya comprometidos con el choque."
    if story_role == "pressure" and "pocos" in low:
        return f"Primeros planos de la gente de {lead}, cansada, con víveres escasos y armas desiguales."
    if story_role == "pressure":
        return f"Columna de {rival}, más numerosa y mejor armada, avanzando con seguridad."
    if story_role == "terrain":
        if "palmo" in low:
            return f"Exploradores de {lead} moviéndose por senderos ocultos y dominando cada recodo."
        return f"Montañas, desfiladeros y pasos estrechos de {location} frenando un avance masivo."
    if story_role == "turn":
        if "oppas" in low or "obispo" in low:
            return f"{bishop} avanzando bajo bandera de parlamento hacia la posición de {lead}."
        return "El mando enemigo frenando el choque y apostando por una salida diplomática."
    if story_role == "choice":
        return f"Primer plano de {lead} rechazando la rendición frente a sus hombres."
    if story_role == "clash":
        return f"Choque cerrado en altura, órdenes cruzadas y ventaja defensiva en las laderas de {location}."
    if story_role == "outcome":
        return f"Retirada rota del enemigo, caos en sus filas y sensación de victoria en torno a {lead}."
    if story_role == "legacy":
        return "Cierre con la batalla ya resuelta y el relato abriéndose hacia sus consecuencias."
    if story_role == "bridge":
        return "Mapas rasgados, fechas marcadas y una amenaza que se abre antes de que nadie pueda detenerla."
    return f"Viñeta narrativa en {location} centrada en {source_beat.rstrip('.')}"


def dialogue_for_scene(story_role: str, source_beat: str, roles: dict, mode: str) -> list[dict]:
    if mode == "inquiry":
        return []

    lead = roles["lead"]
    rival = roles["rival"]
    bishop = roles["bishop"]
    ally = roles["ally"]
    captain = roles["captain"]
    low = source_beat.lower()

    if story_role == "terrain" and "palmo" in low:
        return [
            {"speaker": ally, "line": "Conocemos cada sendero de estas montañas.", "delivery": "normal"},
            {"speaker": lead, "line": "Entonces cerradles el paso donde no puedan maniobrar.", "delivery": "normal"},
        ]
    if story_role == "turn" and ("oppas" in low or "obispo" in low):
        return [
            {"speaker": bishop, "line": f"{lead}, ríndete y evita la matanza.", "delivery": "normal"},
        ]
    if story_role == "turn":
        return [
            {"speaker": captain, "line": "Así no avanzaremos rápido por estas montañas.", "delivery": "normal"},
            {"speaker": rival, "line": "Entonces obligadles a rendirse antes del choque.", "delivery": "normal"},
        ]
    if story_role == "choice":
        return [
            {"speaker": lead, "line": "No habrá entrega.", "delivery": "normal"},
            {"speaker": ally, "line": "Entonces pelearemos aquí.", "delivery": "normal"},
        ]
    if story_role == "clash":
        return [
            {"speaker": lead, "line": "Ahora. Cerradles la salida.", "delivery": "shout"},
            {"speaker": ally, "line": "Ya caen por el paso estrecho.", "delivery": "normal"},
        ]
    if story_role == "outcome":
        return [
            {"speaker": ally, "line": "Su línea se rompe.", "delivery": "normal"},
            {"speaker": lead, "line": "No aflojéis hasta el final.", "delivery": "shout"},
        ]
    return []


def make_scene(
    story_role: str,
    source_beat: str,
    narration: str,
    visual_focus: str,
    dialogue: list[dict],
) -> dict:
    clean_dialogue: list[dict] = []
    for row in dialogue:
        speaker = trim_text(str(row.get("speaker", "")).strip(), 80)
        line = trim_text(clean_scene_sentence(str(row.get("line", "")).strip()), 140)
        delivery = normalize_ws(str(row.get("delivery", "normal")).lower()) or "normal"
        if speaker and line:
            clean_dialogue.append({"speaker": speaker, "line": line, "delivery": "shout" if delivery == "shout" else "normal"})
    clean_narration = clean_scene_sentence(narration)
    timing = estimate_scene_timing(clean_narration, clean_dialogue, story_role)
    return {
        "story_role": story_role,
        "source_beat": compress_source_line(source_beat, 180),
        "visual_focus": trim_text(visual_focus, 220),
        "narration": trim_text(clean_narration, 220),
        "dialogue": clean_dialogue,
        "timing": timing,
        "target_duration_seconds": timing["target_duration_seconds"],
    }


def summarize_premise(summary: str, location: str, roles: dict, mode: str) -> str:
    lead = roles["lead"]
    rival = roles["rival"]
    bishop = roles["bishop"]
    low = repair_common_ocr_issues(summary).lower()
    if mode == "inquiry":
        return trim_text(
            f"La narración parte de Covadonga y retrocede para explicar qué fuerzas hicieron posible ese punto de inflexión en {location}.",
            220,
        )
    if contains_any(low, ["oppas", "obispo", "entreg", "rendición", "rendicion"]):
        return trim_text(
            f"Antes del choque en {location}, {lead} debe resistir la presión militar y la oferta de rendición que trae {bishop}.",
            220,
        )
    return trim_text(
        f"En {location}, {lead} intenta frenar a {rival} convirtiendo la desventaja material en ventaja táctica.",
        220,
    )


def summarize_dramatic_question(summary: str, location: str, roles: dict, mode: str) -> str:
    lead = roles["lead"]
    bishop = roles["bishop"]
    low = repair_common_ocr_issues(summary).lower()
    if mode == "inquiry":
        return "¿Qué hay que entender antes de Covadonga para que esa victoria tenga sentido histórico?"
    if contains_any(low, ["oppas", "obispo", "entreg", "rendición", "rendicion"]):
        return trim_text(
            f"¿Puede {lead} mantener la resistencia en {location} cuando {bishop} ofrece rendición antes de la batalla?",
            200,
        )
    return trim_text(f"¿Puede {lead} sostener la posición en {location} antes de que el enemigo imponga su fuerza?", 200)


def question_scene_line(question: str, order: int) -> str:
    cleaned = normalize_ws(question).rstrip(" ?")
    ordinals = ["Primero", "Después", "A continuación", "Luego", "Al final"]
    prefix = ordinals[min(order - 1, len(ordinals) - 1)]
    low = cleaned.lower()
    if "después de covadonga" in low and "qué pasó" in low:
        return f"{prefix}, queda abierta la pregunta que más empuja la historia: qué ocurrió después de Covadonga."
    if low.startswith("cómo"):
        return f"{prefix}, la grieta decisiva está en {low}."
    if low.startswith("quiénes") or low.startswith("quién"):
        return f"{prefix}, el relato se tensa al preguntar {low}."
    if low.startswith("qué"):
        return f"{prefix}, todo queda pendiente de {low}."
    return f"{prefix}, el relato se detiene en {cleaned.lower()}."


def build_inquiry_outline(summary: str, location: str, roles: dict) -> list[dict]:
    low = repair_common_ocr_issues(summary).lower()
    questions = extract_questions(summary)
    scenes = [
        make_scene(
            "hook",
            "Covadonga aparece como una victoria fundacional que obliga a mirar atrás.",
            "Covadonga abre la historia como una victoria que aún necesita explicación.",
            f"Viñeta de Covadonga tras el combate, con eco de victoria y preguntas pendientes sobre {location}.",
            [],
        ),
        make_scene(
            "bridge",
            "La victoria obliga a retroceder a sus causas.",
            "La victoria aún resuena, pero el corte siguiente cae justo sobre el origen del desastre.",
            "Mapa altomedieval retrocediendo desde Covadonga hacia la invasión inicial.",
            [],
        ),
    ]
    if "primer revés" in low or "primer reves" in low:
        scenes.append(
            make_scene(
                "setup",
                "Fue el primer revés que sufrían los moros desde la invasión.",
                "Ese triunfo fue el primer gran revés que sufrían los invasores desde su entrada en la península.",
                "Viñeta comparando la victoria astur con el avance previo musulmán sobre Hispania.",
                [],
            )
        )
    if "711" in low:
        scenes.append(
            make_scene(
                "bridge",
                "La invasión había empezado en 711.",
                "Para entender Covadonga hay que volver al instante de 711 en que todo empezó a ceder.",
                "Mapa de la península con flechas de invasión y ciudades cayendo en cadena.",
                [],
            )
        )
    if "serie de preguntas" in low or "vamos a contestar" in low:
        scenes.append(
            make_scene(
                "question",
                "La propia batalla abre una cadena de preguntas históricas.",
                "La victoria no se basta sola: abre una cadena de preguntas que el relato debe ordenar.",
                "Montaje de rostros, mapas y símbolos políticos que convierten la victoria en interrogante.",
                [],
            )
        )
    for idx, question in enumerate(questions, start=1):
        line = question_scene_line(question, idx)
        scenes.append(
            make_scene(
                "question",
                question,
                line,
                role_visual_focus("question", question, location, roles),
                [],
            )
        )
    if "empezar por la primera" in low:
        scenes.append(
            make_scene(
                "bridge",
                "Vamos a empezar por la primera pregunta.",
                "El retroceso ya ha elegido su herida inicial: la caída visigoda está a un corte de distancia.",
                "Viñeta de transición con la pregunta principal ocupando el centro del tablero histórico.",
                [],
            )
        )
    scenes.append(
        make_scene(
            "legacy",
            "El siguiente tramo arranca con la caída del reino visigodo.",
            "Y justo cuando la victoria parecía clara, la historia se abre sobre la caída del reino visigodo.",
            "Cierre de transición con corte hacia la crisis visigoda que antecede a Covadonga.",
            [],
        )
    )
    return scenes


def chronicle_scene_specs(summary: str, location: str, roles: dict) -> list[dict]:
    low = repair_common_ocr_issues(summary).lower()
    specs: list[dict] = []

    def add(story_role: str, source_beat: str, narration: str) -> None:
        specs.append(
            make_scene(
                story_role,
                source_beat,
                narration,
                role_visual_focus(story_role, source_beat, location, roles),
                dialogue_for_scene(story_role, source_beat, roles, "chronicle"),
            )
        )

    if "se planteó la batalla" in low or "se planteo la batalla" in low:
        add(
            "hook",
            "Así se planteó la batalla.",
            f"La batalla queda planteada en {location}: los dos bandos ya han aceptado el choque.",
        )
    if "pocos y sin alimentos" in low:
        add(
            "pressure",
            "Los cristianos, pocos y sin alimentos.",
            f"{roles['lead']} resiste con pocos hombres y casi sin comida.",
        )
    if "muchos y bien armados" in low:
        add(
            "pressure",
            "Los moros, muchos y bien armados.",
            f"Frente a ellos, {roles['rival']} llega con más efectivos y mejor armamento.",
        )
    if "terreno jugaba a favor de los cristianos" in low or "laberinto asturiano de valles y montes" in low:
        add(
            "terrain",
            "El terreno jugaba a favor de los cristianos.",
            f"El relieve astur rompe la ventaja numérica y convierte cada valle en una trampa.",
        )
    if "sin carreteras ni puentes" in low:
        add(
            "terrain",
            "Sin carreteras ni puentes, mover un gran ejército era un calvario.",
            "Sin carreteras ni puentes, avanzar por la montaña cuesta tiempo, orden y sangre.",
        )
    if "conocían el terreno palmo a palmo" in low:
        add(
            "terrain",
            "Los rebeldes conocían el terreno palmo a palmo.",
            f"La gente de {roles['lead']} conoce cada paso y puede elegir dónde golpear.",
        )
    if "acuerdo diplomático" in low:
        add(
            "turn",
            "Los moros intentaron un acuerdo diplomático.",
            "Antes del choque, el mando enemigo intenta resolver la crisis sin pelear en ese terreno.",
        )
    if "obispo traidor" in low or "oppas" in low:
        add(
            "turn",
            f"Enviaron a {roles['bishop']} para exigir la rendición.",
            f"El emisario elegido es {roles['bishop']}, enviado a quebrar la resistencia con palabras.",
        )
    if "debía entregarse" in low or "abandonar toda resistencia" in low or "pelayo se negó" in low:
        add(
            "choice",
            f"{roles['lead']} debía entregarse y abandonar toda resistencia.",
            f"{roles['lead']} rechaza la rendición y deja claro que no habrá retirada.",
        )
    if "dio la batalla" in low:
        add(
            "clash",
            f"{roles['lead']} se negó y dio la batalla.",
            "Con la negociación rota, la lucha estalla justo donde la defensa quería librarla.",
        )
    if "diezmadas" in low:
        add(
            "outcome",
            f"Las tropas de {roles['rival']} terminarían siendo diezmadas.",
            f"El desenlace llega con la línea de {roles['rival']} rota y su avance deshecho.",
        )
    return specs


def fallback_chronicle_outline(summary: str, location: str, roles: dict) -> list[dict]:
    fragments = split_story_fragments(summary)
    scenes: list[dict] = []
    for fragment in fragments:
        role = infer_scene_phase(fragment, "chronicle")
        scenes.append(
            make_scene(
                role if role != "setup" else "setup",
                fragment,
                compress_source_line(fragment, 180),
                role_visual_focus(role if role != "setup" else "setup", fragment, location, roles),
                dialogue_for_scene(role, fragment, roles, "chronicle"),
            )
        )
        if len(scenes) >= 10:
            break
    return scenes


def ensure_scene_indices_and_transitions(scenes: list[dict]) -> list[dict]:
    out: list[dict] = []
    total = len(scenes)
    for idx, scene in enumerate(scenes, start=1):
        current = dict(scene)
        current["scene_index"] = idx
        next_role = ""
        if idx < total:
            next_role = str(scenes[idx].get("story_role", ""))
        current_role = str(scene.get("story_role", ""))
        transition = ROLE_TRANSITIONS.get((current_role, next_role))
        if not transition and idx < total:
            transition = "La imagen corta antes de cerrar el problema y empuja a la siguiente viñeta."
        elif not transition:
            transition = "La escena se corta cuando la historia pide la siguiente respuesta."
        current["transition_note"] = transition
        out.append(current)
    return out


def build_main_storytelling(event: dict) -> dict:
    summary = str(event.get("summary", "")).strip()
    title = str(event.get("title", "")).strip()
    location = str(event.get("location", "Asturias")).strip() or "Asturias"
    actor_ids = [str(item) for item in (event.get("actors") or []) if isinstance(item, str)]
    actor_names = [actor_display_name(actor_id) for actor_id in actor_ids]
    cast_names = story_cast(actor_names, summary or title)
    roles = pick_story_roles(cast_names)
    mode = infer_story_mode(summary or title)

    if mode == "inquiry":
        scenes = build_inquiry_outline(summary or title, location, roles)
    else:
        scenes = chronicle_scene_specs(summary or title, location, roles)
        if not scenes:
            scenes = fallback_chronicle_outline(summary or title, location, roles)
        if scenes and scenes[-1]["story_role"] != "outcome":
            scenes.append(
                make_scene(
                    "legacy",
                    "El episodio cierra con la consecuencia inmediata del choque.",
                    "La victoria resuelve este choque, pero deja en el aire el siguiente movimiento del reino.",
                    role_visual_focus("legacy", "consecuencia inmediata", location, roles),
                    [],
                )
            )

    indexed = ensure_scene_indices_and_transitions(scenes)
    total_seconds = sum(int(scene["target_duration_seconds"]) for scene in indexed)
    return {
        "narrative_mode": mode,
        "premise": summarize_premise(summary or title, location, roles, mode),
        "dramatic_question": summarize_dramatic_question(summary or title, location, roles, mode),
        "ending_payoff": indexed[-1]["narration"] if indexed else trim_text(title, 180),
        "scene_outline": indexed,
        "scene_count": len(indexed),
        "target_duration_seconds": total_seconds,
    }


def build_teaser_storytelling(parent_episode: dict, strategy: str) -> dict:
    parent_scenes = parent_episode.get("scenes") or []
    if len(parent_scenes) >= 2:
        open_scene = parent_scenes[0]
        pivot_scene = parent_scenes[min(2, len(parent_scenes) - 1)]
    else:
        open_scene = {"narration": parent_episode.get("hook", ""), "visual_focus": "Primer plano tenso.", "dialogue": []}
        pivot_scene = {"narration": parent_episode.get("hook", ""), "visual_focus": "Cierre en tensión.", "dialogue": []}

    strategy_lines = {
        "cliffhanger": "Y lo verdaderamente decisivo todavía no había empezado.",
        "question": "La pregunta que importa aparece justo después de este corte.",
        "quote": "Una sola frase basta para empujar la historia al borde.",
    }

    scenes = ensure_scene_indices_and_transitions(
        [
            make_scene(
                "hook",
                str(open_scene.get("source_beat") or open_scene.get("narration", "")),
                trim_text(str(open_scene.get("narration", "")), 180),
                trim_text(str(open_scene.get("visual_focus") or "Primer plano tenso antes del giro."), 220),
                list(open_scene.get("dialogue") or [])[:1],
            ),
            make_scene(
                "legacy",
                str(pivot_scene.get("source_beat") or pivot_scene.get("narration", "")),
                trim_text(f"{trim_text(str(pivot_scene.get('narration', '')), 130)} {strategy_lines.get(strategy, strategy_lines['cliffhanger'])}", 180),
                "Cierre con tensión contenida, sombras largas y promesa del siguiente giro.",
                [],
            ),
        ]
    )
    return {
        "narrative_mode": "teaser",
        "premise": trim_text(parent_episode.get("hook", ""), 220),
        "dramatic_question": strategy_lines.get(strategy, strategy_lines["cliffhanger"]),
        "ending_payoff": scenes[-1]["narration"],
        "scene_outline": scenes,
        "scene_count": len(scenes),
        "target_duration_seconds": sum(int(scene["target_duration_seconds"]) for scene in scenes),
    }


def sentence_keywords(sentence: str, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", sentence)
    selected: list[str] = []
    for word in words:
        low = word.lower()
        if low in SPANISH_STOPWORDS:
            continue
        if low in selected:
            continue
        selected.append(low)
        if len(selected) >= max_words:
            break
    return ", ".join(selected)


def build_scene_visual_prompt(
    scene_outline: dict,
    location: str,
    actor_names: list[str],
) -> str:
    cast = ", ".join(actor_names[:4]) if actor_names else "sin personajes en primer plano"
    visual_focus = str(scene_outline.get("visual_focus", "")).strip()
    narration = str(scene_outline.get("narration", "")).strip()
    role = str(scene_outline.get("story_role", "setup")).strip()
    keywords = sentence_keywords(f"{visual_focus} {narration}")
    return trim_text(
        (
            f"Novela grafica historica en {location}, escena {role}. "
            f"{visual_focus} Personajes visibles: {cast}. "
            f"Claves visuales: {keywords}. "
            "Composicion vertical 9:16, dramatismo cinematografico, textura de tinta y grano fino, sin texto incrustado."
        ),
        320,
    )


def derive_plot_twist(summary: str, scene_lines: list[str]) -> dict:
    sentences = split_candidate_sentences(summary)
    if not sentences:
        setup = scene_lines[0]
        reveal = scene_lines[2] if len(scene_lines) > 2 else scene_lines[-1]
        payoff = scene_lines[-1]
        return {"setup": setup, "reveal": reveal, "payoff": payoff}

    setup = sentences[0]
    reveal = None
    for sentence in sentences[1:]:
        low = sentence.lower()
        if any(marker in low for marker in TWIST_MARKERS):
            reveal = sentence
            break
    if not reveal:
        reveal = sentences[min(1, len(sentences) - 1)]
    payoff = sentences[-1]
    return {
        "setup": trim_text(setup, 380),
        "reveal": trim_text(reveal, 380),
        "payoff": trim_text(payoff, 380),
    }

#!/usr/bin/env python3
"""Generate main and teaser episode JSON files from a daily plan.

Strict mode:
- Requires normalized source events
- Requires character files and timeline records for actors
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMELINE = ROOT / "data" / "timeline" / "source_events.json"
DEFAULT_EPISODES_DIR = ROOT / "data" / "episodes" / "generated"
DEFAULT_CHAR_DIR = ROOT / "data" / "characters"
DEFAULT_CHAR_TIMELINES = ROOT / "data" / "characters" / "timelines"

META_NOISE_HINTS = [
    "en este episodio",
    "plot twist",
    "fuente secundaria",
    "narracion dominante",
    "continuidad narrativa",
    "cta al episodio",
    "contexto historico contrastado",
]
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
DEFAULT_MAIN_SCENE_COUNT = 12
DEFAULT_MAIN_TARGET_SECONDS = 120


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def trim_text(text: str, max_len: int) -> str:
    compact = normalize_ws(text)
    if len(compact) <= max_len:
        return compact
    cut = compact[: max_len + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = cut[:max_len]
    return cut.rstrip(".,;: ") + "..."


def actor_display_name(actor_id: str) -> str:
    parts = actor_id.split("_")
    if parts and parts[-1] in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}:
        return " ".join([p.capitalize() for p in parts[:-1]] + [parts[-1].upper()])
    return " ".join(p.capitalize() for p in parts)


def lower_first(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    return cleaned[0].lower() + cleaned[1:]


def split_candidate_sentences(summary: str) -> list[str]:
    clean = normalize_ws(summary.replace("...", ". "))
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


def clean_scene_sentence(sentence: str, max_len: int = 220) -> str:
    base = trim_text(sentence, max_len)
    base = re.sub(r"^(as[ií]),?\s+en\s+fin,?\s*", "", base, flags=re.IGNORECASE)
    base = re.sub(r"^(pero|y|pues|entonces)\s+", "", base, flags=re.IGNORECASE)
    base = base.strip(" ,;:")
    if base and base[-1] not in ".!?":
        base = base + "."
    return base


def source_sentence_cycle(summary: str, scene_count: int) -> list[str]:
    sentences = split_story_fragments(summary)
    if not sentences:
        sentences = [
            "el combate se prepara entre hambre, miedo y orgullo en las montañas.",
            "la tensión sube y nadie puede retirarse sin pagar un precio.",
        ]
    cleaned = [clean_scene_sentence(item) for item in sentences]
    while len(cleaned) < scene_count:
        cleaned.append(cleaned[len(cleaned) % len(cleaned)])
    return cleaned[:scene_count]


def story_cast(actor_names: list[str], summary: str) -> list[str]:
    cast: list[str] = []
    for name in actor_names:
        norm = normalize_ws(name)
        if norm and norm not in cast:
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
    return cast


def pick_story_roles(cast_names: list[str]) -> dict:
    lead = next((name for name in cast_names if "pelayo" in name.lower()), cast_names[0] if cast_names else "Pelayo")
    rival = next((name for name in cast_names if "qama" in name.lower()), "Al Qama")
    bishop = next((name for name in cast_names if "oppas" in name.lower() or "obispo" in name.lower()), "Don Oppas")
    alfonso = next((name for name in cast_names if "alfonso" in name.lower()), "Alfonso")
    ally = next(
        (name for name in cast_names if name not in {lead, rival, bishop}),
        alfonso if alfonso not in {lead, rival, bishop} else "Guerrero astur",
    )
    return {
        "lead": lead,
        "rival": rival,
        "bishop": bishop,
        "ally": ally,
        "captain": f"Capitan de {rival}",
        "chronicle": "Cronista de Albelda",
    }


def derive_scene_lines(summary: str, location: str, actor_names: list[str], scene_count: int = DEFAULT_MAIN_SCENE_COUNT) -> list[str]:
    selected = source_sentence_cycle(summary, scene_count)
    cast_names = story_cast(actor_names, summary)
    roles = pick_story_roles(cast_names)
    lead = roles["lead"]
    rival = roles["rival"]
    bishop = roles["bishop"]

    beat_prefixes = [
        f"En {location}, la jornada se abre con dos bandos en tension:",
        f"{lead} recorre la linea cristiana y aprieta la mandibula:",
        f"{rival} manda avanzar por el valle sin frenar:",
        f"{bishop} entra en escena con una oferta de rendicion:",
        f"{lead} responde de inmediato y endurece la resistencia:",
        "Los senderos estrechos rompen la marcha de los invasores:",
        "Cada ladera favorece a quienes conocen la montana:",
        "La diplomacia se quiebra y las palabras dejan paso al acero:",
        "Y entonces todo cambia:",
        "El choque crece en las gargantas y la iniciativa gira:",
        f"Las filas de {rival} empiezan a deshacerse:",
        "Cuando cae la noche, la cronica fija el desenlace:",
    ]

    while len(beat_prefixes) < scene_count:
        beat_prefixes.append("El frente no concede un respiro:")

    lines: list[str] = []
    for idx in range(scene_count):
        source_line = lower_first(selected[idx])
        line = f"{beat_prefixes[idx]} {source_line}"
        lines.append(trim_text(line, 240))
    return lines


def build_scene_dialogue(scene_index: int, cast_names: list[str]) -> list[dict]:
    roles = pick_story_roles(cast_names)
    lead = roles["lead"]
    rival = roles["rival"]
    bishop = roles["bishop"]
    ally = roles["ally"]
    captain = roles["captain"]
    chronicle = roles["chronicle"]

    templates: list[list[tuple[str, str]]] = [
        [(lead, "Hoy no hay retirada. Asturias se sostiene aqui."), (ally, "Entonces pelearemos roca por roca.")],
        [(rival, "Quiero ese paso tomado antes del ocaso."), (captain, "El terreno nos quiebra la formacion, senor.")],
        [(lead, "Que nadie rompa la linea. Aguantad juntos."), (ally, "Aguantaremos, aunque falte el pan.")],
        [(bishop, "Pelayo, rindete y salvaras a los tuyos."), (lead, "No entregare esta tierra mientras respire.")],
        [(bishop, "La rendicion te dara clemencia."), (lead, "Prefiero la batalla a vivir arrodillado.")],
        [(rival, "Forzad el desfiladero, no les deis tiempo."), (captain, "Cada ladera es una emboscada.")],
        [(ally, "Conocemos cada senda de esta sierra."), (lead, "Entonces golpead y movedos antes de que reaccionen.")],
        [(rival, "Traedme la cabeza de Pelayo."), (lead, "Que venga a buscarla si se atreve.")],
        [(bishop, "Aun puedes detener esta sangre."), (lead, "Llegaste tarde: la decision ya esta hecha.")],
        [(rival, "Cerrad filas, no cedais un palmo."), (captain, "Nos desbordan desde lo alto, general.")],
        [(lead, "Ahora. Empujad con todo, no aflojeis."), (ally, "Caen, Pelayo. Caen en el barranco.")],
        [(chronicle, "Asi lo dejo escrito la cronica en 881."), (lead, "Que recuerden que aqui empezamos a resistir.")],
    ]

    pair = templates[(scene_index - 1) % len(templates)]
    out: list[dict] = []
    for speaker, line in pair:
        out.append({"speaker": speaker, "line": trim_text(line, 140)})
    return out


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


def build_scene_visual_prompt(line: str, location: str, actor_names: list[str], scene_idx: int) -> str:
    cast = ", ".join(actor_names[:3]) if actor_names else "sin personajes en primer plano"
    keywords = sentence_keywords(line)
    camera = "zoom in lento y dramatico" if scene_idx % 2 == 1 else "traveling corto con tension"
    return trim_text(
        (
            f"Panel de comic historico en {location}, {camera}, intriga politica y atmosfera de guerra. "
            f"Elenco: {cast}. Claves visuales: {keywords}. "
            "Ilustracion narrativa epica, contraste alto, grano sutil, sin texto incrustado."
        ),
        320,
    )


def event_map(timeline_file: Path) -> dict[str, dict]:
    if not timeline_file.exists():
        raise RuntimeError(
            f"Missing timeline file: {timeline_file}. "
            "Strict mode is enabled: no source -> no episodes."
        )
    payload = load_json(timeline_file)
    if not isinstance(payload, list):
        raise RuntimeError(f"Timeline file must be a JSON array: {timeline_file}")
    out: dict[str, dict] = {}
    for item in payload:
        if isinstance(item, dict) and "event_id" in item:
            out[item["event_id"]] = item
    if not out:
        raise RuntimeError(
            "Timeline has no valid source events. "
            "Strict mode is enabled: no source -> no episodes."
        )
    return out


def load_character_file(character_id: str, char_dir: Path) -> dict:
    path = char_dir / f"{character_id}.json"
    if not path.exists():
        raise RuntimeError(
            f"Missing character file for '{character_id}': {path}. "
            "Run scripts/build_character_bible.py first."
        )
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid character file format: {path}")
    return payload


def load_character_timeline(character_id: str, timelines_dir: Path) -> dict:
    path = timelines_dir / f"{character_id}.json"
    if not path.exists():
        raise RuntimeError(
            f"Missing character timeline for '{character_id}': {path}. "
            "Run scripts/build_character_bible.py first."
        )
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid character timeline format: {path}")
    return payload


def long_text(base: str, min_length: int = 20) -> str:
    if len(base) >= min_length:
        return base
    return (base + " " + "Contexto historico contrastado para continuidad narrativa.")[: max(min_length, 20)]


def beat_for_actor_event(character_id: str, event_id: str, timelines_dir: Path) -> dict:
    timeline_payload = load_character_timeline(character_id, timelines_dir)
    records = timeline_payload.get("records", [])
    for rec in records:
        if rec.get("event_id") == event_id:
            return {
                "character_id": character_id,
                "emotion_before": rec["emotion_before"],
                "emotion_after": rec["emotion_after"],
                "arc_stage_before": rec["arc_stage_before"],
                "arc_stage_after": rec["arc_stage_after"],
                "beat_summary": rec["change_summary"][:300],
            }
    raise RuntimeError(
        f"No timeline record for character '{character_id}' in event '{event_id}'. "
        "Character arc continuity requires per-event records."
    )


def build_main_scenes(
    event_summary: str,
    location: str,
    actor_names: list[str],
    scene_count: int,
    scene_seconds: int,
) -> list[dict]:
    cast_names = story_cast(actor_names, event_summary)
    lines = derive_scene_lines(event_summary, location, cast_names, scene_count=scene_count)
    scenes: list[dict] = []
    for idx, line in enumerate(lines, start=1):
        scenes.append(
            {
                "scene_index": idx,
                "narration": line,
                "visual_prompt": build_scene_visual_prompt(line, location, cast_names, idx),
                "dialogue": build_scene_dialogue(idx, cast_names),
                "estimated_seconds": scene_seconds,
            }
        )
    return scenes


def create_main_episode(main_row: dict, events: dict[str, dict], char_dir: Path, timelines_dir: Path) -> dict:
    source_event_ids = main_row["source_event_ids"]
    lead_id = source_event_ids[0]
    if lead_id not in events:
        raise RuntimeError(f"Source event '{lead_id}' not found in timeline.")

    lead_event = events[lead_id]
    event_title = lead_event.get("title", f"Evento {lead_id}")
    event_summary = str(lead_event.get("summary", "")).strip()
    location = lead_event.get("location", "Asturias")
    actors = lead_event.get("actors") or []
    if not actors:
        raise RuntimeError(f"Source event '{lead_id}' has no actors. Character continuity is required.")

    for actor in actors:
        load_character_file(actor, char_dir)

    actor_names = [actor_display_name(actor) for actor in actors]
    character_beats = [beat_for_actor_event(actor, lead_id, timelines_dir) for actor in actors]
    raw_scene_count = int(main_row.get("scene_count", DEFAULT_MAIN_SCENE_COUNT))
    scene_count = max(6, min(20, raw_scene_count))
    target_seconds = int(main_row.get("target_duration_seconds", DEFAULT_MAIN_TARGET_SECONDS))
    target_seconds = max(60, min(180, target_seconds))
    scene_seconds = max(6, min(20, round(target_seconds / scene_count)))
    scenes = build_main_scenes(
        event_summary or event_title,
        location,
        actor_names,
        scene_count=scene_count,
        scene_seconds=scene_seconds,
    )
    plot_twist = derive_plot_twist(event_summary or event_title, [s["narration"] for s in scenes])
    duration = sum(scene["estimated_seconds"] for scene in scenes)

    hook = trim_text(f"{scenes[0]['narration']} {scenes[2]['narration']}", 300)
    clean_title = re.sub(r"^(as[ií]),?\s+en\s+fin,?\s*", "", trim_text(event_title, 88), flags=re.IGNORECASE)
    if clean_title:
        clean_title = clean_title[0].upper() + clean_title[1:]

    return {
        "episode_id": main_row["episode_id"],
        "episode_type": "main",
        "status": "generated",
        "language": "es",
        "narrative_tone": "epico_historico_oscuro",
        "timeline_event_ids": source_event_ids,
        "parent_episode_id": None,
        "title": trim_text(clean_title, 120),
        "hook": hook,
        "plot_twist": plot_twist,
        "scenes": scenes,
        "characters": actors,
        "character_beats": character_beats,
        "duration_seconds": duration,
        "human_review": {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "notes": "Pendiente revision historica y narrativa antes de cualquier publicacion.",
        },
        "asset_refs": {
            "audio": f"local://audio/{main_row['episode_id']}.wav",
            "video": f"local://video/{main_row['episode_id']}.mp4",
            "subtitles": f"local://subtitles/{main_row['episode_id']}.srt",
        },
        "created_at": now_iso(),
    }


def create_teaser_episode(teaser_row: dict, parent_main: dict) -> dict:
    strategy = teaser_row.get("strategy", "cliffhanger")

    line_by_strategy = {
        "cliffhanger": "Y justo cuando parecía decidido, la crónica abre una herida nueva.",
        "question": "Si esta versión era la oficial, ¿quién tenía interés en ocultar el giro?",
        "quote": "Una frase de la crónica basta para encender de nuevo la guerra.",
    }

    parent_scenes = parent_main.get("scenes") or []
    open_line = parent_scenes[0]["narration"] if parent_scenes else parent_main["hook"]
    twist_line = parent_main.get("plot_twist", {}).get("reveal") or line_by_strategy["cliffhanger"]

    scenes = [
        {
            "scene_index": 1,
            "narration": trim_text(open_line, 220),
            "visual_prompt": trim_text(
                "Panel de comic historico, primer plano tenso, gesto de alarma y estandartes en movimiento, sin texto.",
                220,
            ),
            "estimated_seconds": 13,
        },
        {
            "scene_index": 2,
            "narration": trim_text(
                f"{trim_text(twist_line, 170)} {line_by_strategy.get(strategy, line_by_strategy['cliffhanger'])}",
                220,
            ),
            "visual_prompt": trim_text(
                "Panel de cierre con sombras largas, tension militar y promesa de choque inminente, sin texto.",
                220,
            ),
            "estimated_seconds": 12,
        },
    ]

    return {
        "episode_id": teaser_row["episode_id"],
        "episode_type": "teaser",
        "status": "generated",
        "language": "es",
        "narrative_tone": "epico_historico_oscuro",
        "timeline_event_ids": parent_main["timeline_event_ids"][:1],
        "parent_episode_id": teaser_row["parent_episode_id"],
        "title": trim_text(f"Teaser: {parent_main['title']}", 120),
        "hook": trim_text(parent_main["hook"], 300),
        "scenes": scenes,
        "characters": parent_main["characters"][:2],
        "duration_seconds": 25,
        "human_review": {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "notes": "Validar gancho, CTA y coherencia cronologica.",
        },
        "asset_refs": {
            "audio": f"local://audio/{teaser_row['episode_id']}.wav",
            "video": f"local://video/{teaser_row['episode_id']}.mp4",
            "subtitles": f"local://subtitles/{teaser_row['episode_id']}.srt",
        },
        "created_at": now_iso(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Path to daily plan JSON")
    parser.add_argument("--timeline", default=str(DEFAULT_TIMELINE), help="Path to source events JSON")
    parser.add_argument("--output-dir", default=str(DEFAULT_EPISODES_DIR), help="Directory for generated episodes")
    parser.add_argument("--characters-dir", default=str(DEFAULT_CHAR_DIR), help="Directory of character JSON files")
    parser.add_argument(
        "--character-timelines-dir",
        default=str(DEFAULT_CHAR_TIMELINES),
        help="Directory of character timeline JSON files",
    )
    args = parser.parse_args()

    try:
        plan_path = Path(args.plan)
        timeline_path = Path(args.timeline)
        output_dir = Path(args.output_dir)
        char_dir = Path(args.characters_dir)
        timelines_dir = Path(args.character_timelines_dir)

        plan = load_json(plan_path)
        events = event_map(timeline_path)

        for main_row in plan["main_episodes"]:
            for event_id in main_row["source_event_ids"]:
                if event_id not in events:
                    raise RuntimeError(
                        f"Plan references event '{event_id}' that does not exist in timeline '{timeline_path}'."
                    )

        date_compact = plan["date"].replace("-", "")
        plan_bucket = output_dir / f"plan-{date_compact}"

        main_map: dict[str, dict] = {}
        written_files: list[Path] = []

        for main_row in plan["main_episodes"]:
            episode = create_main_episode(main_row, events, char_dir, timelines_dir)
            episode_path = plan_bucket / f"{episode['episode_id']}.json"
            dump_json(episode_path, episode)
            main_map[episode["episode_id"]] = episode
            written_files.append(episode_path)

        for teaser_row in plan["teaser_episodes"]:
            parent_id = teaser_row["parent_episode_id"]
            parent = main_map.get(parent_id)
            if not parent:
                raise RuntimeError(f"Parent main episode missing for teaser: {parent_id}")
            teaser = create_teaser_episode(teaser_row, parent)
            teaser_path = plan_bucket / f"{teaser['episode_id']}.json"
            dump_json(teaser_path, teaser)
            written_files.append(teaser_path)

        print(f"Generated {len(written_files)} episode files in {plan_bucket}")
        for path in written_files:
            print(path)
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

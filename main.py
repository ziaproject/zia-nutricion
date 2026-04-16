from __future__ import annotations
import os
"""
ZIA — Agente de nutrición familiar.
Plan semanal con recetas rápidas (<20 min) y productos preparados del supermercado.
"""


import base64
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openai import OpenAI

# Enlaces a tiendas online: solo si existe acuerdo comercial.
ACUERDO_COMERCIAL_ENLACES = False

# Preferible: export OPENAI_API_KEY=... y no commitear secretos.
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-4o"

MEMORIA_PATH = Path(__file__).resolve().parent / "memoria.json"


def memoria_por_defecto() -> dict[str, Any]:
    return {
        "perfil": {},
        "recetas_gustaron": [],
        "recetas_no_gustaron": [],
        "ultimo_plan": "",
        "historial_listas_compra": [],
        "seguimiento_semanal": [],
        "perfil_deporte": {},
        "preferencia_dieta": {},
        "aprobacion_gastos": {"historial": [], "ultima_decision": ""},
        "mini_lista_faltantes": [],
        "reservas_restaurante": [],
        "ultima_reserva_restaurante": {},
        "recordatorios_reserva": [],
    }


def cargar_memoria() -> dict[str, Any]:
    if not MEMORIA_PATH.is_file():
        return memoria_por_defecto()
    try:
        raw = MEMORIA_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return memoria_por_defecto()
    base = memoria_por_defecto()
    for k in base:
        if k in data:
            base[k] = data[k]
    if not isinstance(base["perfil"], dict):
        base["perfil"] = {}
    for lista in (
        "recetas_gustaron",
        "recetas_no_gustaron",
        "historial_listas_compra",
        "seguimiento_semanal",
    ):
        if not isinstance(base[lista], list):
            base[lista] = []
    if not isinstance(base["ultimo_plan"], str):
        base["ultimo_plan"] = ""
    if not isinstance(base.get("perfil_deporte"), dict):
        base["perfil_deporte"] = {}
    if not isinstance(base.get("preferencia_dieta"), dict):
        base["preferencia_dieta"] = {}
    if not isinstance(base.get("aprobacion_gastos"), dict):
        base["aprobacion_gastos"] = {"historial": [], "ultima_decision": ""}
    else:
        base["aprobacion_gastos"].setdefault("historial", [])
        base["aprobacion_gastos"].setdefault("ultima_decision", "")
    if not isinstance(base.get("mini_lista_faltantes"), list):
        base["mini_lista_faltantes"] = []
    if not isinstance(base.get("reservas_restaurante"), list):
        base["reservas_restaurante"] = []
    if not isinstance(base.get("ultima_reserva_restaurante"), dict):
        base["ultima_reserva_restaurante"] = {}
    if not isinstance(base.get("recordatorios_reserva"), list):
        base["recordatorios_reserva"] = []
    return base


def guardar_memoria(memoria: dict[str, Any]) -> None:
    MEMORIA_PATH.write_text(
        json.dumps(memoria, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def perfil_tiene_datos(perfil: dict[str, str]) -> bool:
    return bool(perfil.get("nombre", "").strip())


def ofrecer_perfil_guardado(memoria: dict[str, Any]) -> str:
    nombre = memoria.get("perfil", {}).get("nombre", "usuario").strip() or "usuario"
    print(f"\nZIA: ¡Hola, {nombre}! Tengo tu perfil guardado.")
    print(
        "ZIA: ¿Quieres continuar con ese perfil o empezar de nuevo?\n"
        "     Escribe «continuar» o «nuevo».\n"
    )
    while True:
        r = input("Tú: ").strip().lower()
        if r in ("salir",):
            raise SystemExit(0)
        if r in ("continuar", "c", "sí", "si", "yes", "1"):
            return "continuar"
        if r in ("nuevo", "empezar", "de nuevo", "n", "2", "reset"):
            return "nuevo"
        print('ZIA: Escribe «continuar» para usar tu perfil guardado o «nuevo» para el onboarding otra vez.')


def reset_memoria_tras_nuevo(memoria: dict[str, Any]) -> None:
    memoria["perfil"] = {}
    memoria["recetas_gustaron"] = []
    memoria["recetas_no_gustaron"] = []
    memoria["ultimo_plan"] = ""
    memoria["seguimiento_semanal"] = []
    memoria["perfil_deporte"] = {}
    memoria["preferencia_dieta"] = {}
    memoria["aprobacion_gastos"] = {"historial": [], "ultima_decision": ""}
    memoria["mini_lista_faltantes"] = []
    memoria["reservas_restaurante"] = []
    memoria["ultima_reserva_restaurante"] = {}
    memoria["recordatorios_reserva"] = []
    guardar_memoria(memoria)


def registrar_feedback_recetas(texto: str, memoria: dict[str, Any]) -> bool:
    t = texto.strip()
    if t.startswith("+") and len(t) > 1:
        memoria["recetas_gustaron"].append(t[1:].strip())
        return True
    if t.startswith("-") and len(t) > 1:
        memoria["recetas_no_gustaron"].append(t[1:].strip())
        return True
    m = re.match(r"(?i)me\s+(?:ha\s+)?gust(?:ó|a)\s+(.+)", t)
    if m:
        memoria["recetas_gustaron"].append(m.group(1).strip())
        return True
    m = re.match(r"(?i)no\s+me\s+(?:ha\s+)?gust(?:ó|a)\s+(.+)", t)
    if m:
        memoria["recetas_no_gustaron"].append(m.group(1).strip())
        return True
    return False


def contexto_memoria_para_prompt(memoria: dict[str, Any]) -> str:
    bloques: list[str] = []
    g = memoria.get("recetas_gustaron") or []
    ng = memoria.get("recetas_no_gustaron") or []
    if g:
        bloques.append("Recetas/platos que al usuario le han gustado (prioriza variaciones o similares): " + "; ".join(g[-20:]))
    if ng:
        bloques.append("Evita repetir o insistir en estos platos/recetas: " + "; ".join(ng[-20:]))
    ultimo = (memoria.get("ultimo_plan") or "").strip()
    if ultimo:
        max_chars = 6000
        trozo = ultimo if len(ultimo) <= max_chars else ultimo[:max_chars] + "\n[…texto truncado…]"
        bloques.append(
            "Último plan generado (no repitas los mismos platos principales ni la misma estructura semanal; cambia menús y lista):\n"
            + trozo
        )
    if not bloques:
        return ""
    return "\n\nMemoria y preferencias:\n" + "\n".join(bloques)


def añadir_lista_al_historial(memoria: dict[str, Any], texto_plan_completo: str) -> None:
    entrada = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "texto": texto_plan_completo,
    }
    memoria.setdefault("historial_listas_compra", []).append(entrada)
    guardar_memoria(memoria)


ONBOARDING_QUESTIONS_INDIVIDUAL: list[tuple[str, str]] = [
    (
        "nombre",
        "¿Cómo te llamamos?",
    ),
    (
        "datos_fisicos",
        "Para personalizar tu plan necesito algunos datos. Dímelos en un mensaje:\nGénero, edad, peso (kg) y altura (cm)\nEjemplo: hombre, 35 años, 80 kg, 178 cm",
    ),
    (
        "objetivo",
        "¿Cuál es tu objetivo principal? Elige solo uno:\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía",
    ),
    (
        "presupuesto",
        "¿Cuánto quieres gastar a la semana en comida? (en euros, ej.: 80)",
    ),
    (
        "supermercado",
        "¿En qué supermercado sueles comprar? (Mercadona, Lidl, Carrefour…)",
    ),
    (
        "restricciones",
        "¿Tienes alguna alergia, intolerancia o preferencia alimentaria? (ej.: sin gluten, vegetariano…)\nSi no hay ninguna, escribe «ninguna».",
    ),
    (
        "tiempo_cocina",
        "¿Cuánto tiempo tienes para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tengo tiempo, me gusta cocinar",
    ),
]

ONBOARDING_QUESTIONS_FAMILIAR: list[tuple[str, str]] = [
    (
        "nombre",
        "¿Cómo te llamamos?",
    ),
    (
        "num_personas",
        "¿Cuántas personas coméis habitualmente en casa?",
    ),
    (
        "ninos_edades",
        "¿Hay niños en casa? Si es que sí, indica sus edades.\nSi no hay, escribe «no».",
    ),
    (
        "gustos_familia",
        "Cuéntame los gustos o comidas favoritas de la familia y si hay algo que no le guste a alguien.\nEjemplo: al mayor no le gusta el pescado, a los niños les encanta la pasta.",
    ),
    (
        "restricciones",
        "¿Hay alergias o intolerancias en casa? (ej.: celiaquía, sin lactosa, vegetariano…)\nSi no hay ninguna, escribe «ninguna».",
    ),
    (
        "presupuesto",
        "¿Cuánto queréis gastar a la semana en la compra? (en euros, ej.: 150)",
    ),
    (
        "supermercado",
        "¿En qué supermercado soléis comprar? (Mercadona, Lidl, Carrefour…)",
    ),
    (
        "tiempo_cocina",
        "¿Cuánto tiempo tenéis para cocinar al día?\n1️⃣ Menos de 20 minutos\n2️⃣ Entre 20 y 40 minutos\n3️⃣ Tenemos tiempo, nos gusta cocinar",
    ),
]

# Compatibilidad con código existente
ONBOARDING_QUESTIONS = ONBOARDING_QUESTIONS_INDIVIDUAL

SYSTEM_BASE = """Eres ZIA, la mejor nutricionista del mundo, especializada en nutrición personalizada en España.
- Eres como una nutricionista de élite que además es amiga cercana: cercana, directa, sin postureo ni jerga innecesaria.
- Tienes acceso al perfil físico completo del usuario (género, edad, peso, altura, objetivo) y SIEMPRE lo usas para calcular sus necesidades calóricas y de macronutrientes con la fórmula de Mifflin-St Jeor.
- SIEMPRE incluye gramos exactos en cada comida: proteínas (g), carbohidratos (g), grasas (g) y calorías totales del día.
- SIEMPRE indica el tiempo de preparación en minutos para cada receta (ej: 15 min).
- En planes semanales incluye los 7 días completos (lunes a domingo) con desayuno, comida, merienda y cena, con gramos exactos.
- En listas de la compra: indica el supermercado elegido por el usuario, cantidades totales por producto (ej: 600g pechuga de pollo) y precio orientativo por producto en ese supermercado. Al final muestra TOTAL ESTIMADO.
- Después de cada lista de la compra pregunta: ¿Confirmas la compra en [supermercado] o quieres comparar con otros supermercados?
- Actúa como coach motivacional: detecta cuando el usuario está decaído, desmotivado o con dudas y responde con apoyo emocional genuino, frases motivadoras y recordatorios de sus objetivos.
- Adapta porciones al número de personas y a niños si los hay.
- Respeta restricciones y presupuesto; si algo no encaja, ofrece alternativa concreta.
- En modo NEVERA INTELIGENTE, propón recetas solo con ingredientes disponibles.
- En modo NUTRICIÓN DEPORTE, incluye comidas pre y post entreno con timing exacto.
- No uses markdown con asteriscos para negrita; usa títulos en MAYÚSCULAS o líneas en blanco para separar secciones.
- Suplementos: NO los menciones salvo que el usuario lo pida explícitamente."""

INSTRUCCION_CIERRE_ZIA = """
CIERRE (respuestas generales; no aplica a listas de la compra en pasos dedicados):
- NO cierres con frases genéricas de asistente («si tienes alguna pregunta», «no dudes en decírmelo», «estoy aquí para ayudarte», despedidas largas ni avisos legales largos).
- NO muestres menús numerados, botones ficticios ni listas tipo «1️⃣ 2️⃣ 3️⃣» salvo que el usuario pida explícitamente pasos.
- Termina casi siempre con UNA pregunta breve y natural que avance el tema (comida, compra, energía, tiempo de cocina), no con cierres vacíos.
- Puedes cerrar a veces con una línea breve de ánimo con [nombre] del perfil (o «tú») si encaja, pero la prioridad es la pregunta que conecte con el siguiente paso.
"""

# Solo cuando el usuario pide suplementos: el modelo debe replicar esta estructura y el patrón de URLs (tag afiliado Amazon).
FORMATO_SUPLEMENTOS_AFILIADOS_REFERENCIA = """
Cuando recomiendes suplementos (solo si el usuario lo ha pedido), para CADA suplemento usa exactamente esta estructura de líneas (título en MAYÚSCULAS, viñetas con •):

PROTEÍNA EN POLVO (sin gluten, sin lactosa)
• Cuándo: Después del entrenamiento con agua o leche vegetal
• Dosis: 1 scoop (25-30g)
• 🛒 Comprar → https://www.amazon.es/s?k=proteina+en+polvo+sin+gluten&i=amazonfresh&tag=zia-nutricion-21

OMEGA-3
• Cuándo: Con una comida que contenga grasa, en el almuerzo o cena
• Dosis: 1-2 cápsulas al día
• 🛒 Comprar → https://www.amazon.es/s?k=omega+3&tag=zia-nutricion-21

CREATINA
• Cuándo: Antes o después del entrenamiento
• Dosis: 5g al día
• 🛒 Comprar → https://www.amazon.es/s?k=creatina&tag=zia-nutricion-21

VITAMINA D
• Cuándo: Con el desayuno con algo de grasa
• Dosis: 1000-2000 UI al día
• 🛒 Comprar → https://www.amazon.es/s?k=vitamina+d3&tag=zia-nutricion-21

MULTIVITAMÍNICO
• Cuándo: Por la mañana con el desayuno
• Dosis: 1 cápsula al día
• 🛒 Comprar → https://www.amazon.es/s?k=multivitaminico&tag=zia-nutricion-21

Tras el último suplemento listado, añade SIEMPRE la alternativa MyProtein en una línea:
• 💊 MyProtein → https://www.myprotein.es/?affil=zia

Para otros suplementos no listados aquí, construye la URL de búsqueda Amazon como:
https://www.amazon.es/s?k=TÉRMINOS+DE+BÚSQUEDA&tag=zia-nutricion-21
(usa + entre palabras en k=; añade &i=amazonfresh solo si encaja con producto alimentario/polvo en tienda fresh).
"""

REGLA_SUPLEMENTOS_ENLACES_AFILIADOS = """
REGLA SUPLEMENTOS Y AFILIADOS:
- Prohibido hablar de suplementos, polvos proteicos, omega, vitaminas de herbolario o enlaces MyProtein/Amazon de suplementos si el usuario NO lo ha pedido explícitamente.
- Si el usuario SÍ pide suplementos o suplementación (incluye preguntas sobre creatina, proteína en polvo, omega 3, vitamina D, multivitamínico, «qué comprar» en contexto deportivo de suplementos, etc.), debes dar el detalle completo y DESPUÉS de la descripción de cada suplemento incluir la línea «• 🛒 Comprar → » con URL Amazon en formato de búsqueda y SIEMPRE el parámetro &tag=zia-nutricion-21 (o ?tag=zia-nutricion-21 si es el único parámetro tras la ruta).
- Al final del bloque de suplementos incluye SIEMPRE: • 💊 MyProtein → https://www.myprotein.es/?affil=zia
- Sigue el patrón de ejemplo del system (títulos, Cuándo, Dosis, Comprar).
""" + FORMATO_SUPLEMENTOS_AFILIADOS_REFERENCIA

SYSTEM_LISTA_COMPRA = (
    SYSTEM_BASE
    + "\n\nTAREA ÚNICA: generar solo la LISTA DE LA COMPRA indicada en el mensaje de usuario. "
    "NO añadas frase motivacional ni menú. NO suplementos en la lista salvo que el usuario los pida explícitamente; "
    "si los pide, incluye cada uno con el formato de enlaces Amazon (tag=zia-nutricion-21) y línea MyProtein del system. "
    "NO preguntas al final."
)

SYSTEM_SOLO_TOTALES = (
    "Eres ZIA. Cumple exactamente el formato pedido en el mensaje de usuario. "
    "Sin suplementos. Sin texto antes ni después de las líneas pedidas."
)


def imprimir_zia_conversacion(texto: str) -> None:
    """Imprime un turno de ZIA sin menús ni botones."""
    print(f"\nZIA: {texto.rstrip()}\n")


def nombre_supermercado_perfil(perfil: dict[str, str]) -> str:
    ids = ids_supermercados_detectados(perfil.get("supermercado", ""))
    cid = ids[0] if ids else "mercadona"
    return SUPER_TIENDA_URL[cid][0]


def parsear_items_y_total_lista(texto: str) -> tuple[list[str], str | None]:
    items: list[str] = []
    total: str | None = None
    for line in (texto or "").splitlines():
        s = line.strip()
        if s.startswith("•"):
            items.append(s[1:].strip())
        if re.search(r"(?i)total", s) and re.search(r"\d+[.,]\d{2}", s):
            m = re.search(r"(\d+[.,]\d{2})\s*€?", s)
            if m:
                total = m.group(1).replace(",", ".")
    if total is None:
        m = re.search(
            r"TOTAL\s*(?:ESTIMADO)?\s*[:\s]*(\d+[.,]\d{2})\s*€?",
            texto or "",
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            total = m.group(1).replace(",", ".")
    return items, total


def _linea_es_resumen_compra_o_url(linea: str) -> bool:
    t = linea.strip()
    if not t:
        return True
    if "http://" in t or "https://" in t:
        return True
    if ACUERDO_COMERCIAL_ENLACES:
        return False
    if re.search(r"(?i)comprar\s+en", t) and "→" in t:
        return True
    return False


def imprimir_vista_carrito_tras_lista(
    lista_txt: str,
    perfil: dict[str, str],
    nombre_tienda: str | None = None,
) -> None:
    nombre = nombre_tienda or nombre_supermercado_perfil(perfil)
    items, total = parsear_items_y_total_lista(lista_txt)
    print(f"\n🛒 TU CARRITO - {nombre.upper()}")
    print("─────────────────────────")
    if items:
        for it in items:
            print(f"• {it}")
    else:
        for line in (lista_txt or "").splitlines():
            if _linea_es_resumen_compra_o_url(line):
                continue
            tl = line.strip()
            if re.match(r"(?i)^total\b", tl) and re.search(r"\d+[.,]\d{2}", tl):
                continue
            print(line.rstrip())
    print("─────────────────────────")
    if total:
        print(f"💰 TOTAL: {total}€")
    else:
        print("💰 TOTAL: — (revisa la lista generada arriba si no hay importe)")
    print()


def presupuesto_semanal_euros(perfil: dict[str, str]) -> float | None:
    """Importe semanal en € desde el campo presupuesto del perfil, o None."""
    raw = str(perfil.get("presupuesto", "") or "")
    m = re.search(r"(\d+(?:[.,]\d+)?)", raw.replace(",", "."))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
        return v if v > 0 else None
    except ValueError:
        return None


def total_lista_a_float(lista_txt: str) -> float | None:
    _, t = parsear_items_y_total_lista(lista_txt)
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def print_pregunta_donde_comprar(perfil: dict[str, str]) -> None:
    """Compat: redirige al flujo con precio / cercanía / entrega."""
    print_pregunta_comparar_o_cadena_tras_lista(perfil)


def print_confirmacion_super_y_presupuesto_antes_lista(perfil: dict[str, str]) -> None:
    """Antes de generar la lista: confirma super habitual y avisa si hay presupuesto en perfil."""
    ns = nombre_supermercado_perfil(perfil)
    pres = presupuesto_semanal_euros(perfil)
    print(f"\nZIA: Uso tu super habitual ({ns}) para estimar todos los precios de la lista.")
    if pres is not None:
        print(
            f"   Tu presupuesto semanal en el perfil: {pres:.2f}€. "
            "Si el total estimado lo supera, te avisaré y podrás aprobar el extra o ajustar la lista.\n"
        )
    else:
        print(
            "   No tengo presupuesto guardado en el perfil; los importes son orientativos.\n"
        )


def print_pregunta_comparar_o_cadena_tras_lista(perfil: dict[str, str]) -> None:
    """Tras confirmar la compra: comparar por precio, cercanía o entrega, o elegir cadena."""
    ns = nombre_supermercado_perfil(perfil)
    print(
        "\nZIA: ¿Quieres comparar esta cesta con otras opciones?\n"
        "   • Precio — escribe «comparar precios» y te muestro el total estimado en varias cadenas.\n"
        "   • Cercanía — escribe «cercanía» y te oriento sobre qué suele ser más fácil tener cerca (según tu ciudad del perfil).\n"
        "   • Entrega / rapidez — escribe «entrega» u «online» para orientación sobre reparto y compra online.\n"
        f"   • O dime una cadena concreta (p. ej. Lidl) y preparo el carrito solo con precios de esa tienda.\n"
        f"   • Si te vale con {ns}, di «me quedo en mi super» o «así está bien».\n"
    )


def registrar_evento_aprobacion_gastos(
    memoria: dict[str, Any],
    decision: str,
    total: float | None,
    presupuesto: float | None,
    detalle: str = "",
) -> None:
    entrada = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "total_euros": total,
        "presupuesto_euros": presupuesto,
        "detalle": detalle,
    }
    memoria.setdefault("aprobacion_gastos", {"historial": [], "ultima_decision": ""})
    memoria["aprobacion_gastos"].setdefault("historial", []).append(entrada)
    memoria["aprobacion_gastos"]["ultima_decision"] = decision
    guardar_memoria(memoria)


def mensaje_ajustar_lista_al_presupuesto(
    perfil: dict[str, str],
    lista_actual: str,
    presupuesto_euros: float,
) -> str:
    return f"""Ajusta esta LISTA DE LA COMPRA para que el TOTAL ESTIMADO no supere {presupuesto_euros:.2f}€ (presupuesto semanal del usuario).

Lista actual:
---
{lista_actual[:12000]}
---

Perfil: {perfil_a_texto(perfil)}

TAREA:
1) Primero una sección CAMBIOS REALIZADOS con viñetas • indicando qué productos quitaste, sustituiste por opciones más baratas o redujiste en cantidad.
2) Luego la lista ajustada en secciones FRESCO y PREPARADO con • producto (cantidad) → XX.XX€
3) Línea TOTAL ESTIMADO: XX.XX€ (debe ser ≤ {presupuesto_euros:.2f}€).
NO suplementos salvo que ya estuvieran en la lista original."""


def generar_lista_ajustada_presupuesto(
    client: OpenAI,
    perfil: dict[str, str],
    lista_txt: str,
    presupuesto_euros: float,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_LISTA_COMPRA},
        {
            "role": "user",
            "content": mensaje_ajustar_lista_al_presupuesto(perfil, lista_txt, presupuesto_euros),
        },
    ]
    return completar(client, messages, temperature=0.45, max_tokens=4096)


def encolar_aprobacion_post_lista(
    lista_txt: str,
    perfil: dict[str, str],
    nombre_tienda: str | None = None,
    omitir_vista_carrito: bool = False,
    origen: str = "semanal",
) -> dict[str, Any]:
    """
    Tras generar una lista: compara total vs presupuesto y muestra confirmación u opciones de sobrepreso.
    origen: «semanal» | «dieta» | «nevera» | «mini_faltas» → tras confirmar, pregunta dónde comprar.
    «tienda_carrito» → el usuario ya eligió cadena; tras confirmar, mensaje de listo sin repetir la pregunta.
    Devuelve el contexto que debe guardar main() hasta resolver la aprobación.
    """
    pres = presupuesto_semanal_euros(perfil)
    tot = total_lista_a_float(lista_txt)
    ctx: dict[str, Any] = {
        "modo": "confirmar",
        "lista_txt": lista_txt,
        "total": tot,
        "presupuesto": pres,
        "nombre_tienda": nombre_tienda,
        "origen": origen,
    }
    if pres is not None and tot is not None and tot > pres + 0.01:
        diff = round(tot - pres, 2)
        print(
            f"\n⚠️ Atención presupuesto: el total estimado es {tot:.2f}€ y tu presupuesto semanal es {pres:.2f}€ "
            f"(vas {diff:.2f}€ por encima). Con ese presupuesto no alcanza tal cual.\n"
            "¿Apruebas el gasto extra o quieres que ajuste la lista para ceñirme al presupuesto?\n"
            "1️⃣ Aprobar igualmente\n"
            "2️⃣ Ajustar lista al presupuesto\n"
        )
        ctx["modo"] = "sobre_presupuesto"
        return ctx
    if not omitir_vista_carrito:
        imprimir_vista_carrito_tras_lista(lista_txt, perfil, nombre_tienda=nombre_tienda)
    print("ZIA: ¿Confirmas esta compra? (sí/no)\n")
    return ctx


SYSTEM_FALTA_INGREDIENTE = (
    "Eres ZIA. Tu respuesta es SOLO para parseo interno: líneas INGREDIENTE, COMIDA, CANTIDAD, PRECIO. "
    "Prohibido: ojalá pudiera, no puedo comprar, no puedo ayudar, ve al supermercado, o cualquier excusa. "
    "Siempre rellenas cantidad y precio orientativo en € (España)."
)


def dia_semana_hoy_es() -> str:
    nombres = (
        "LUNES",
        "MARTES",
        "MIÉRCOLES",
        "JUEVES",
        "VIERNES",
        "SÁBADO",
        "DOMINGO",
    )
    return nombres[date.today().weekday()]


def detectar_falta_ingrediente(texto: str) -> bool:
    """Usuario indica que le falta algún ingrediente o producto para cocinar."""
    tn = texto_sin_acentos(texto.lower().strip())
    if not tn:
        return False
    if re.search(r"(?i)no tengo\s+tiempo\b", tn):
        return False
    if any(
        x in tn
        for x in (
            "no me falta",
            "no falta nada",
            "no me falta nada",
            "nada me falta",
            "no es que falte",
            "no falta ",
        )
    ):
        return False
    if re.search(
        r"(?i)\b(me falta|me faltan|falta el|falta la|faltan los|faltan las|faltan |no tengo el|no tengo la|no tengo los|no tengo las|no tengo |se me acabo|se me acabó|no me queda|me hace falta)\b",
        tn,
    ):
        return True
    if re.search(r"(?i)\bnecesito\s+(?:el|la|los|las|un|una|unos|unas)\b", tn):
        return True
    if "ingrediente" in tn and any(x in tn for x in ("falta", "faltan", "no tengo", "sin ")):
        return True
    return False


def mensaje_inferir_falta_ingrediente(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    mensaje_usuario: str,
) -> str:
    plan = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
    dia = dia_semana_hoy_es()
    return f"""El usuario dice que le falta algo para cocinar o para una comida.

Día de la semana hoy: {dia}

Mensaje del usuario:
«{mensaje_usuario.strip()}»

Contexto (plan/menú guardado; puede estar vacío):
---
{plan[:12000] if plan else "(sin plan: infiere solo del mensaje)"}
---

TAREA: Identifica UN ingrediente o producto principal que le falta (el más relevante si nombra varios).
Indica para qué comida o plato (COMIDA: ej. la cena de hoy, el guiso, el desayuno, la receta X).
CANTIDAD razonable para comprar (ej. 400 g, 1 brik, 2 unidades).
PRECIO orientativo en euros (supermercado España).

Responde ÚNICAMENTE estas 4 líneas, sin más texto:
INGREDIENTE: <nombre>
COMIDA: <para qué>
CANTIDAD: <cantidad>
PRECIO: <X.XX>

Perfil: {perfil_a_texto(perfil)}
"""


def parsear_inferencia_falta_ingrediente(texto: str) -> dict[str, str]:
    d = {
        "ingredient": "",
        "meal": "tu comida",
        "quantity": "1 ud",
        "price": "1.00",
    }
    for line in (texto or "").splitlines():
        s = line.strip()
        m = re.match(r"(?i)INGREDIENTE\s*:\s*(.+)$", s)
        if m:
            d["ingredient"] = m.group(1).strip()
            continue
        m = re.match(r"(?i)COMIDA\s*:\s*(.+)$", s)
        if m:
            d["meal"] = m.group(1).strip()
            continue
        m = re.match(r"(?i)CANTIDAD\s*:\s*(.+)$", s)
        if m:
            d["quantity"] = m.group(1).strip()
            continue
        m = re.match(r"(?i)PRECIO\s*:\s*(.+)$", s)
        if m:
            raw_p = m.group(1).strip()
            mp = re.search(r"(\d+(?:[.,]\d+)?)", raw_p.replace(",", "."))
            if mp:
                d["price"] = mp.group(1).replace(",", ".")
    return d


def extraer_ingrediente_heuristico(texto: str) -> str | None:
    m = re.search(
        r"(?i)(?:me falta|me faltan|no tengo|falta)\s+(?:el |la |los |las )?([^.,;\n]+?)(?:\s+para|\s+en|$)",
        texto.strip(),
    )
    if m:
        return m.group(1).strip()[:80] or None
    return None


def formatear_precio_euros(precio_raw: str) -> str:
    s = str(precio_raw).strip().replace(",", ".")
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return s


def imprimir_bloque_falta_ingrediente_exacto(
    ingredient: str,
    meal: str,
    quantity: str,
    price: str,
) -> None:
    ps = formatear_precio_euros(price)
    ing = ingredient.strip() or "el ingrediente"
    ml = meal.strip() or "tu comida"
    q = quantity.strip() or "1 ud"
    print(f"\nTe falta {ing} para {ml}.\n")
    print("¿Qué prefieres?")
    print("1️⃣ 🔄 Sustituirlo — te doy una alternativa con lo que tienes")
    print(f"2️⃣ 🛒 Comprarlo — {ing} ({q}) → {ps}€\n")


def texto_bloque_falta_ingrediente_exacto(
    ingredient: str,
    meal: str,
    quantity: str,
    price: str,
) -> str:
    ps = formatear_precio_euros(price)
    ing = ingredient.strip() or "el ingrediente"
    ml = meal.strip() or "tu comida"
    q = quantity.strip() or "1 ud"
    return (
        f"Te falta {ing} para {ml}.\n\n"
        "¿Qué prefieres?\n"
        "1️⃣ 🔄 Sustituirlo — te doy una alternativa con lo que tienes\n"
        f"2️⃣ 🛒 Comprarlo — {ing} ({q}) → {ps}€"
    )


def es_opcion_falta_sustituir(texto: str) -> bool:
    t = texto.strip().lower()
    if t in ("1", "1️⃣"):
        return True
    if t in ("2", "2️⃣"):
        return False
    return "sustitu" in t or "alternativ" in t


def intencion_confirmar_compra_falta(texto: str) -> bool:
    """Confirma que quiere comprar el faltante (opción 2 o frases naturales)."""
    ts = texto_sin_acentos(texto.lower().strip())
    if ts in ("si", "sí", "dale", "ok", "vale", "yes", "claro"):
        return True
    return any(
        x in ts
        for x in (
            "pidelo ya",
            "pedirlo",
            "pidemelo",
            "haz el pedido",
            "quiero comprarlo",
            "lo compro",
            "compralo",
            "comprelo",
            "anadelo",
            "añadelo",
            "añádelo",
        )
    )


def es_opcion_falta_comprar(texto: str) -> bool:
    t = texto.strip().lower()
    ts = texto_sin_acentos(t)
    if t in ("2", "2️⃣"):
        return True
    if t in ("1", "1️⃣"):
        return False
    if "sustitu" in ts or "alternativ" in ts:
        return False
    if "no compro" in ts or "no lo compro" in ts:
        return False
    if intencion_confirmar_compra_falta(texto):
        return True
    return (
        "compr" in ts
        or "carrito" in ts
        or "pedido" in ts
        or "lo pido" in ts
        or "añad" in ts
    )


def url_busqueda_consum(query: str) -> str:
    q = quote((query or "producto").strip())
    return f"https://tienda.consum.es/supermercado/buscar?q={q}"


def texto_mini_pedido_faltantes(memoria: dict[str, Any], ultima_linea_anotada: str) -> str:
    """Texto del mini pedido con enlace de búsqueda en Consum (última línea = ítem recién anotado)."""
    items = memoria.get("mini_lista_faltantes") or []
    total = 0.0
    bullets: list[str] = []
    query_busqueda = "compra"
    for it in items:
        if isinstance(it, dict):
            ln = str(it.get("line", ""))
            pr = it.get("price", "0")
            ing = str(it.get("ingredient", "")).strip()
            if ing:
                query_busqueda = ing
        else:
            ln = str(it)
            pr = "0"
        if ln:
            bullets.append(f"• {ln}")
        try:
            total += float(str(pr).replace(",", "."))
        except ValueError:
            pass
    if items and isinstance(items[-1], dict):
        last_ing = str(items[-1].get("ingredient", "")).strip()
        if last_ing:
            query_busqueda = last_ing
    link = url_busqueda_consum(query_busqueda)
    body = "\n".join(bullets) if bullets else "• (vacío)"
    return (
        f"✅ Anotado. {ultima_linea_anotada}\n\n"
        "📦 MINI PEDIDO:\n"
        f"{body}\n"
        "─────────────────\n"
        f"💰 Total: {total:.2f}€\n"
        f"🏪 Consum → {link}\n\n"
        "¿Faltan más ingredientes o lo dejamos así?"
    )


def intencion_no_mas_ingredientes_mini(texto: str) -> bool:
    """Usuario cierra el mini pedido: no necesita más ítems."""
    tn = texto_sin_acentos(texto.lower().strip())
    if not tn:
        return False
    if detectar_falta_ingrediente(texto):
        return False
    if tn in ("no", "nop", "nope"):
        return True
    if tn in ("listo", "listo."):
        return True
    return any(
        x in tn
        for x in (
            "nada mas",
            "nada más",
            "lo dejamos asi",
            "lo dejamos así",
            "dejamos asi",
            "dejamos así",
            "ya esta",
            "ya está",
            "es todo",
            "suficiente",
            "no falta nada",
            "no falta mas",
            "no falta más",
            "ninguno mas",
            "ninguno más",
            "por hoy no",
            "eso es todo",
            "cerramos",
            "todo listo",
        )
    )


def generar_inferencia_falta_ingrediente(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    mensaje_usuario: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_FALTA_INGREDIENTE},
        {
            "role": "user",
            "content": mensaje_inferir_falta_ingrediente(perfil, memoria, mensaje_usuario),
        },
    ]
    return completar(client, messages, temperature=0.25, max_tokens=400)


def ejecutar_inferencia_y_ctx_falta_ingrediente(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    texto: str,
) -> dict[str, Any]:
    raw = generar_inferencia_falta_ingrediente(client, perfil, memoria, texto)
    parsed = parsear_inferencia_falta_ingrediente(raw)
    if not parsed["ingredient"]:
        h = extraer_ingrediente_heuristico(texto)
        parsed["ingredient"] = (h or "el producto").strip()
    imprimir_bloque_falta_ingrediente_exacto(
        parsed["ingredient"],
        parsed["meal"],
        parsed["quantity"],
        parsed["price"],
    )
    return {
        "ingredient": parsed["ingredient"],
        "meal": parsed["meal"],
        "quantity": parsed["quantity"],
        "price": parsed["price"],
        "raw_llm": raw,
    }


def generar_sustituto_falta_ingrediente(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    ingredient: str,
    meal: str,
) -> str:
    prompt = f"""Le falta {ingredient} para {meal}. No va a comprarlo ahora.
Propón la MEJOR alternativa práctica (un solo bloque breve: qué usar en su lugar o qué plato similar hacer) usando solo lo que suele haber en una cocina española (despensa, básicos) o sin ese ingrediente.
Prohibido: decir que no puedes ayudar, mandar a una tienda, o «ojalá pudiera».
Perfil: {perfil_a_texto(perfil)}
{contexto_memoria_para_prompt(memoria)}
Sin suplementos."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt},
    ]
    return completar(client, messages, temperature=0.45, max_tokens=900)


def construir_texto_mini_lista_faltantes(memoria: dict[str, Any]) -> tuple[str, float]:
    items = memoria.get("mini_lista_faltantes") or []
    lineas: list[str] = []
    total = 0.0
    for it in items:
        if isinstance(it, dict):
            ln = str(it.get("line", ""))
            pr = it.get("price", "0")
        else:
            ln = str(it)
            pr = "0"
        if ln:
            lineas.append(f"• {ln}")
        try:
            total += float(str(pr).replace(",", "."))
        except ValueError:
            pass
    body = "\n".join(lineas)
    if not body:
        body = "(vacío)"
    txt = f"MINI LISTA — FALTANTES\n{body}\nTOTAL ESTIMADO: {total:.2f}€"
    return txt, total


SYSTEM_TRES_SUGERENCIAS_RESTAURANTE = (
    "Eres ZIA. El mensaje de usuario te dirá explícitamente la ciudad y, si aplica, el tipo de cocina/plato. "
    "Si hay cocina concreta: solo 3 restaurantes que se especialicen en ESA cocina; nunca otros estilos. "
    "Si no hay cocina única, puedes variar. "
    "El tercer local lleva «DESTACADO» y «patrocinado». "
    "Ratings creíbles (4,0–5,0). Responde en español. Sin prefijo «ZIA:». "
    "Sigue el formato y la pregunta final que pide el encargo."
)


def reserva_restaurante_ctx_vacio() -> dict[str, str]:
    return {
        "fecha": "",
        "hora": "",
        "personas": "",
        "nombre_reserva": "",
        "telefono": "",
        "restricciones": "",
        "sugerencias_mostrada": "",
        "restaurante_elegido": "",
        "preferencia_cocina": "",
        "sugerencias_bloque": "",
        "mostrada_nudges": "",
        "recordatorio_una_hora": "",
    }


def detectar_intencion_reserva_restaurante(texto: str) -> bool:
    """Usuario quiere reservar mesa o habla de comer/cenar fuera."""
    tn = texto_sin_acentos(texto.lower().strip())
    if not tn:
        return False
    if "reserva" in tn:
        return True
    if "restaurante" in tn:
        return True
    if any(
        x in tn
        for x in (
            "cenar fuera",
            "comer fuera",
            "cenamos fuera",
            "comemos fuera",
            "salir a cenar",
            "salir a comer",
            "comer fuera de casa",
            "cenar fuera de casa",
            "quiero cenar fuera",
            "quiero comer fuera",
        )
    ):
        return True
    if "mesa" in tn and any(
        x in tn for x in ("reserv", "pedir", "booking", "book", "para cenar", "para comer")
    ):
        return True
    if re.search(r"mesa\s+para\s+\d", tn):
        return True
    return False


def detectar_consulta_mi_reserva_guardada(texto: str) -> bool:
    """Usuario pregunta por el resumen de una reserva ya guardada (no inicia reserva nueva)."""
    tn = texto_sin_acentos(texto.lower().strip())
    if len(tn) < 6:
        return False
    return any(
        p in tn
        for p in (
            "mi reserva",
            "que reserva tengo",
            "qué reserva tengo",
            "cual es mi reserva",
            "cuál es mi reserva",
            "resumen de mi reserva",
            "resumen de la reserva",
            "datos de mi reserva",
            "la reserva que hiciste",
            "la reserva que tengo",
            "reserva guardada",
            "confirmaste la reserva",
            "tengo reserva",
            "alguna reserva pendiente",
            "recuerdas mi reserva",
        )
    )


def extraer_preferencia_cocina_desde_texto_libre(texto: str) -> str:
    """
    Detecta cocina/plato pedido en lenguaje natural (p. ej. «quiero comer paella»)
    cuando el modelo no rellena preferencia_cocina. Orden: frases largas antes que cortas.
    """
    tn = texto_sin_acentos((texto or "").lower())
    if len(tn) < 3:
        return ""
    frases = (
        (r"\bpaella valenciana\b", "paella valenciana"),
        (r"\barroz (?:a la cubana|negro|caldo|meloso|band)\b", "arroz"),
        (r"\bpaella\b", "paella"),
        (r"\barroz\b", "arroz"),
        (r"\bsushi\b", "sushi"),
        (r"\bramen\b", "ramen"),
        (r"\bpizza\b", "pizza"),
        (r"\bpasta\b", "pasta italiana"),
        (r"\btacos?\b", "mexicano / tacos"),
        (r"\btapas\b", "tapas"),
        (r"\bhamburgues", "hamburguesas"),
        (r"\bmarisco|marisquer", "marisco"),
        (r"\bpescado\b", "pescado"),
        (r"\bcocina (?:italiana|italiano)\b", "cocina italiana"),
        (r"\bitalian[oa]\b", "cocina italiana"),
        (r"\bcocina (?:japonesa|japones)\b", "cocina japonesa"),
        (r"\bjapones[oa]?\b", "cocina japonesa"),
        (r"\bcocina (?:mexicana|mexicano)\b", "cocina mexicana"),
        (r"\bmexican[oa]?\b", "cocina mexicana"),
        (r"\bcocina (?:china|chino)\b", "cocina china"),
        (r"\bchino|china\b", "cocina china"),
        (r"\bcocina (?:india|indio)\b", "cocina india"),
        (r"\bindio|india\b", "cocina india"),
        (r"\bthai|tailandes[ea]?\b", "cocina tailandesa"),
        (r"\bcocina (?:peruana|peruano)\b", "cocina peruana"),
        (r"\bperuan[oa]?\b", "cocina peruana"),
        (r"\bcocina (?:griega|griego)\b", "cocina griega"),
        (r"\bcocina (?:francesa|frances)\b", "cocina francesa"),
        (r"\bvegan[oa]?\b", "vegano"),
        (r"\bvegetarian[oa]?\b", "vegetariano"),
        (r"\bkebab\b", "kebab"),
        (r"\bbrasas?\b|parrilla|asador", "brasa / parrilla"),
    )
    for pat, etiqueta in frases:
        if re.search(pat, tn):
            return etiqueta
    return ""


def parsear_json_desde_llm(texto: str) -> dict[str, Any] | None:
    t = (texto or "").strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        if m:
            t = m.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def extraer_numero_personas_respuesta_corta(texto: str) -> str | None:
    """
    Respuesta corta solo con el número de comensales (p. ej. «4» o «cuatro»)
    tras «¿Cuántas personas seréis?» — evita que se pierda y se repita la pregunta.
    """
    raw = (texto or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{1,2}", raw):
        n = int(raw)
        if 1 <= n <= 40:
            return str(n)
    tn = texto_sin_acentos(raw.lower())
    palabras = {
        "uno": "1",
        "dos": "2",
        "tres": "3",
        "cuatro": "4",
        "cinco": "5",
        "seis": "6",
        "siete": "7",
        "ocho": "8",
        "nueve": "9",
        "diez": "10",
    }
    if tn in palabras:
        return palabras[tn]
    return None


def menciona_numero_personas_reserva(texto: str) -> bool:
    """True si el usuario indica cuántas personas van a esta reserva (no uses el perfil familiar)."""
    if extraer_numero_personas_respuesta_corta(texto):
        return True
    tn = texto_sin_acentos(texto.lower())
    if re.search(
        r"\b\d{1,2}\s*(personas|persona|comensales|comensal|gente|plazas|pax)\b",
        tn,
    ):
        return True
    if re.search(
        r"\b(somos|seremos|vamos|iremos|para)\s+\d{1,2}\b",
        tn,
    ):
        return True
    if re.search(
        r"\b\d{1,2}\s+(adultos|niños|niñas|mayores|personas)\b",
        tn,
    ):
        return True
    if re.search(
        r"\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+(personas|comensales)\b",
        tn,
    ):
        return True
    if re.search(r"\b(pareja|solo yo|solamente yo|yo solo|yo sola)\b", tn):
        return True
    return False


def fusionar_reserva_restaurante_ctx(
    client: OpenAI,
    perfil: dict[str, str],
    texto_usuario: str,
    ctx_actual: dict[str, Any],
    etapa: str = "cita",
) -> dict[str, str]:
    """
    etapa:
    - "cita": solo fecha, hora, personas y tipo de cocina / preferencia (antes de elegir restaurante).
    - "todo": compatibilidad; rellena todos los campos (no usar en el flujo guiado).
    """
    base = reserva_restaurante_ctx_vacio()
    for k in base:
        v = ctx_actual.get(k)
        if v is not None and str(v).strip():
            base[k] = str(v).strip()
    prompt = f"""Actualiza los datos de una reserva de restaurante.

Perfil (solo ciudad/zona si sirve; IGNORA num_personas y tamaño del hogar):
{perfil_a_texto(perfil)}

Estado actual (JSON):
{json.dumps(base, ensure_ascii=False)}

Nuevo mensaje del usuario:
«{texto_usuario.strip()}»

Devuelve SOLO un JSON con exactamente estas claves string:
"fecha", "hora", "personas", "nombre_reserva", "telefono", "restricciones", "preferencia_cocina"

Reglas:
- Conserva valores previos no vacíos si el usuario no los cambia (salvo la regla de personas abajo).
- Si un dato no se menciona, usa cadena vacía "".
- preferencia_cocina: si el usuario nombra comida o cocina (incluido en frases como «quiero comer paella», «cenar sushi», «me apetece italiano»), copia esa petición en una frase MUY breve. NUNCA uses «cocina variada» si el usuario ya indicó un tipo concreto. Si no menciona cocina en este mensaje, usa "" (el programa conservará la cocina dicho antes).
- personas: NUNCA uses el tamaño del hogar del perfil. Solo si el usuario indica cuántas personas van a ESTA reserva.
- nombre_reserva, telefono, restricciones: si estamos solo en fase de cita/mesa, déjalos vacíos salvo que el mensaje sea claramente solo eso.

JSON válido únicamente, sin markdown."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Respondes únicamente un objeto JSON válido. Sin texto fuera del JSON."},
        {"role": "user", "content": prompt},
    ]
    raw = completar(client, messages, temperature=0.2, max_tokens=600)
    parsed = parsear_json_desde_llm(raw)
    out = reserva_restaurante_ctx_vacio()
    if isinstance(parsed, dict):
        for k in out:
            v = parsed.get(k, "")
            if v is not None:
                out[k] = str(v).strip()
    prev_personas = str(ctx_actual.get("personas", "") or "").strip()
    solo_n = extraer_numero_personas_respuesta_corta(texto_usuario)
    if solo_n:
        out["personas"] = solo_n
    elif not menciona_numero_personas_reserva(texto_usuario):
        out["personas"] = prev_personas
    if etapa == "cita":
        out["nombre_reserva"] = ""
        out["telefono"] = ""
        out["restricciones"] = ""
        prev_pref = str(ctx_actual.get("preferencia_cocina", "") or "").strip()
        pref_llm = (out.get("preferencia_cocina") or "").strip()
        out["preferencia_cocina"] = pref_llm or prev_pref
        cocina_heur = extraer_preferencia_cocina_desde_texto_libre(texto_usuario)
        pref = (out.get("preferencia_cocina") or "").strip()
        if cocina_heur and (
            not pref or pref.lower()
            in ("cocina variada", "variada", "cualquiera", "lo que sea", "da igual")
        ):
            out["preferencia_cocina"] = cocina_heur
    return out


def fusionar_contacto_reserva_restaurante(
    client: OpenAI,
    perfil: dict[str, str],
    texto_usuario: str,
    ctx_actual: dict[str, Any],
) -> dict[str, str]:
    base = reserva_restaurante_ctx_vacio()
    for k in base:
        v = ctx_actual.get(k)
        if v is not None and str(v).strip():
            base[k] = str(v).strip()
    prompt = f"""El usuario responde a la pregunta: «¿A nombre de quién hago la reserva y qué teléfono dejo?»

Perfil (referencia): {perfil_a_texto(perfil)}

Contexto reserva (JSON):
{json.dumps(base, ensure_ascii=False)}

Mensaje del usuario:
«{texto_usuario.strip()}»

Extrae nombre_reserva (nombre para la mesa) y telefono (teléfono; dígitos y prefijo si los dice).

Devuelve SOLO JSON: "nombre_reserva", "telefono". Cadenas vacías si no se deduce."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Respondes únicamente un objeto JSON válido."},
        {"role": "user", "content": prompt},
    ]
    raw = completar(client, messages, temperature=0.2, max_tokens=400)
    parsed = parsear_json_desde_llm(raw)
    out = {**base}
    if isinstance(parsed, dict):
        for k in ("nombre_reserva", "telefono"):
            v = parsed.get(k, "")
            if v is not None and str(v).strip():
                out[k] = str(v).strip()
    return out


def fusionar_restricciones_reserva_restaurante(
    client: OpenAI,
    texto_usuario: str,
    ctx_actual: dict[str, Any],
) -> dict[str, str]:
    base = reserva_restaurante_ctx_vacio()
    for k in base:
        v = ctx_actual.get(k)
        if v is not None and str(v).strip():
            base[k] = str(v).strip()
    prompt = f"""Actualiza solo el campo restricciones (alergias, celiaquía, vegano, sin gluten, etc.).

Estado (JSON):
{json.dumps(base, ensure_ascii=False)}

Mensaje:
«{texto_usuario.strip()}»

JSON: "restricciones" solamente. Si no aplica o dice que ninguna, usa "Ninguna"."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Respondes únicamente un objeto JSON válido."},
        {"role": "user", "content": prompt},
    ]
    raw = completar(client, messages, temperature=0.2, max_tokens=300)
    parsed = parsear_json_desde_llm(raw)
    out = {**base}
    if isinstance(parsed, dict):
        v = parsed.get("restricciones", "")
        if v is not None and str(v).strip():
            out["restricciones"] = str(v).strip()
    return out


def reserva_cita_completa(ctx: dict[str, str]) -> bool:
    return all(
        str(ctx.get(k, "") or "").strip()
        for k in ("fecha", "hora", "personas")
    )


def pregunta_siguiente_reserva_cita(ctx: dict[str, str]) -> str:
    """Solo fecha/hora y personas; no nombre ni teléfono hasta elegir restaurante."""
    if not ctx.get("fecha", "").strip() or not ctx.get("hora", "").strip():
        return "ZIA: ¡Qué plan! ¿Para qué día y a qué hora te va bien?\n"
    if not ctx.get("personas", "").strip():
        return "ZIA: ¿Cuántas personas seréis?\n"
    return "ZIA: ¡Qué plan! ¿Para qué día y a qué hora te va bien?\n"


def intencion_ninguna_restriccion(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower().strip())
    return tn in (
        "ninguna",
        "nada",
        "no",
        "nop",
        "no hay",
        "sin alergias",
        "ninguno",
    ) or ("ninguna" in tn and "alerg" in tn)


def es_preferencia_cocina_concreta(pref: str) -> bool:
    p = (pref or "").strip().lower()
    if not p:
        return False
    return p not in (
        "cocina variada",
        "variada",
        "cualquiera",
        "lo que sea",
        "sin preferencia",
        "da igual",
    )


def generar_tres_sugerencias_restaurantes(
    client: OpenAI,
    perfil: dict[str, str],
    ctx: dict[str, str],
    *,
    disculpa_correccion: str | None = None,
) -> str:
    ciudad = ciudad_para_urls_reserva(perfil)
    pref = (ctx.get("preferencia_cocina") or "").strip() or "cocina variada"
    restrict = es_preferencia_cocina_concreta(pref)
    cuisine_label = pref.strip()
    if restrict:
        nucleo = f"""The user wants to eat {cuisine_label} in {ciudad}.

Recommend ONLY 3 restaurants that specialize in {cuisine_label}.
NEVER recommend restaurants with other cuisines.
If the user said 'paella' or asked for paella/arroz, only show paella and arroz-focused restaurants.
If the user said 'sushi', only show Japanese / sushi restaurants.
If the user said 'pizza' or Italian food, only show Italian restaurants that match that specialty.

Write the full answer in Spanish for the user. Use realistic names in Spain.

Format each restaurant exactly like this (blank line between blocks):

🥘 [Restaurant name] ⭐X,X
• Especialidad: (must match {cuisine_label} — the house must be known for this)
• Ambiente: ...
• Precio medio: ...€/persona

The third restaurant must include «DESTACADO» and «patrocinado» in the title line (e.g. 🏆 Name DESTACADO - patrocinado ⭐4,7).

Start with this title line:
«Te recomiendo estos para {cuisine_label} en {ciudad}:»

End with this question exactly:
«¿Cuál te gusta o quieres que busque más opciones?»

No «ZIA:» prefix. Plausible ratings 4,0–5,0."""
    else:
        nucleo = f"""The user wants restaurant ideas in {ciudad}. Cuisine preference: {pref} (broad or unspecified — you may suggest 3 restaurants with different styles if it fits the city).

Write the full answer in Spanish.

Format each restaurant:
🥘 [Nombre] ⭐X,X
• Especialidad: ...
• Ambiente: ...
• Precio medio: ...€/persona

Third restaurant: include «DESTACADO» and «patrocinado» in the title.

Title: «Te recomiendo estos en {ciudad}:» (adapt if pref gives a hint).

End with: «¿Cuál te gusta o quieres que busque más opciones?»
No «ZIA:» prefix."""
    if disculpa_correccion:
        nucleo = f"{disculpa_correccion.strip()}\n\n{nucleo}"
    temp = 0.38 if restrict else 0.55
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_TRES_SUGERENCIAS_RESTAURANTE},
        {"role": "user", "content": nucleo},
    ]
    return completar(client, messages, temperature=temp, max_tokens=1600).strip()


def extraer_correccion_cocina_reserva(
    client: OpenAI,
    texto_usuario: str,
    preferencia_actual: str,
) -> tuple[bool, str]:
    """
    Detecta si el usuario dice que las sugerencias no eran de la cocina pedida y devuelve la cocina correcta.
    """
    prompt = f"""Estado: el usuario veía 3 sugerencias de restaurante para la petición: «{preferencia_actual or "cocina variada"}».

Mensaje del usuario:
«{texto_usuario.strip()}»

¿Indica que las sugerencias NO encajaban con la cocina que quería (demasiado genéricas, mal tipo, otra cocina) y aclara o repite qué cocina busca?
Responde SOLO JSON: {{"es_correccion": true/false, "nueva_cocina": "texto breve, ej. paella valenciana, sushi"}}

Reglas:
- Si solo elige un restaurante («el primero», un nombre de la lista) sin quejarse de la cocina → es_correccion false, nueva_cocina "".
- Si pide «más opciones» sin queja de cocina → es_correccion false.
- Si dice que no era sushi/paella/italiano y quería otra cosa → es_correccion true y rellena nueva_cocina."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Respondes únicamente JSON válido."},
        {"role": "user", "content": prompt},
    ]
    raw = completar(client, messages, temperature=0.1, max_tokens=220)
    parsed = parsear_json_desde_llm(raw)
    if not isinstance(parsed, dict):
        return False, ""
    if not parsed.get("es_correccion"):
        return False, ""
    nueva = str(parsed.get("nueva_cocina", "") or "").strip()
    return True, nueva


def procesar_reserva_tras_fusion_cita(
    client: OpenAI,
    perfil: dict[str, str],
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], str | None, str]:
    """
    Tras fusionar datos de cita. Devuelve ctx actualizado, nuevo estado (p. ej. elegiendo_restaurante)
    o None si sigue en recolectando, y texto a mostrar al usuario.
    """
    ctx = dict(ctx)
    if reserva_cita_completa(ctx) and not (ctx.get("sugerencias_mostrada") or "").strip():
        try:
            bloque_sug = generar_tres_sugerencias_restaurantes(client, perfil, ctx)
            ctx["sugerencias_bloque"] = bloque_sug
            ctx["sugerencias_mostrada"] = "1"
            return ctx, "elegiendo_restaurante", bloque_sug
        except Exception as e:
            return (
                ctx,
                None,
                f"ZIA: No pude sugerir restaurantes ahora ({e}). ¿Lo intentamos de nuevo?\n",
            )
    if not reserva_cita_completa(ctx):
        return ctx, None, pregunta_siguiente_reserva_cita(ctx)
    return ctx, None, "ZIA: Sigo con tu reserva…\n"


def interpretar_eleccion_restaurante(
    client: OpenAI,
    texto_usuario: str,
    bloque_sugerencias: str,
) -> tuple[str | None, bool]:
    """
    Devuelve (nombre_corto_del_restaurante_elegido, quiere_mas_opciones).
    nombre_corto: texto identificable para el restaurante elegido.
    """
    prompt = f"""Lista de sugerencias previa del asistente:
---
{bloque_sugerencias[:12000]}
---

Mensaje del usuario:
«{texto_usuario.strip()}»

Responde SOLO JSON:
{{"restaurante": "nombre exacto o abreviado del local elegido de la lista, o null si no elige",
"mas_opciones": true si pide más opciones, buscar otras, o similar; false si no}}

Si elige por posición («el primero», «el 1», «la segunda»), mapea al nombre de la lista."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Respondes únicamente JSON válido."},
        {"role": "user", "content": prompt},
    ]
    raw = completar(client, messages, temperature=0.15, max_tokens=200)
    parsed = parsear_json_desde_llm(raw)
    if not isinstance(parsed, dict):
        return None, False
    mas = bool(parsed.get("mas_opciones"))
    if mas:
        return None, True
    r = parsed.get("restaurante")
    if r is None:
        return None, False
    nombre = str(r).strip()
    if not nombre or nombre.lower() in ("null", "ninguno", "ninguna"):
        return None, False
    return nombre, False


def url_thefork_busqueda_restaurante(nombre_rest: str, ciudad: str) -> str:
    q = quote((nombre_rest or "").strip())
    slug = slug_ciudad_url(ciudad)
    return f"https://www.thefork.es/buscar?q={q}&city={slug}"


def bloque_reserva_enviada_por_zia(
    perfil: dict[str, str],
    ctx: dict[str, Any],
) -> str:
    """Confirmación cuando el usuario pide que ZIA gestione la reserva (sin enlaces externos)."""
    _ = perfil
    nombre_r = str(ctx.get("restaurante_elegido", "") or "").strip()
    fecha = str(ctx.get("fecha", "") or "").strip()
    hora = str(ctx.get("hora", "") or "").strip()
    pers = str(ctx.get("personas", "") or "").strip()
    nom_pers = str(ctx.get("nombre_reserva", "") or "").strip()
    tel = str(ctx.get("telefono", "") or "").strip()
    restr = str(ctx.get("restricciones", "") or "").strip()
    linea_restr = ""
    if restr and restr.lower() not in ("ninguna", "nada", ""):
        linea_restr = f"\n📝 A notar: {restr}"
    return (
        f"✅ Reserva enviada a {nombre_r}.\n\n"
        f"Te confirmarán en los próximos minutos.\n\n"
        f"📋 RESUMEN DE TU RESERVA:\n"
        f"📅 Fecha: {fecha}\n"
        f"🕐 Hora: {hora}\n"
        f"👥 Personas: {pers}\n"
        f"🍽️ Restaurante: {nombre_r}\n"
        f"👤 Nombre: {nom_pers}\n"
        f"📱 Teléfono: {tel}"
        f"{linea_restr}\n\n"
        f"⏰ Te recuerdo 1 hora antes de la reserva.\n\n"
        f"¡Que lo disfrutes! 🥘"
    )


def guardar_reserva_zia_enviada_y_recordatorio(
    memoria: dict[str, Any],
    ctx: dict[str, Any],
    texto_confirmacion: str,
) -> None:
    """Persiste resumen + recordatorio 1 h antes en memoria.json."""
    base_ctx = {k: str(v) if v is not None else "" for k, v in ctx.items()}
    entrada: dict[str, Any] = {
        **base_ctx,
        "texto_resumen": texto_confirmacion,
        "reserva_enviada_por_zia": True,
        "fecha_registro": datetime.now(timezone.utc).isoformat(),
    }
    memoria.setdefault("reservas_restaurante", []).append(entrada)
    memoria["ultima_reserva_restaurante"] = entrada
    alerta: dict[str, Any] = {
        "tipo": "reserva_restaurante",
        "fecha_reserva_texto": str(ctx.get("fecha", "")),
        "hora_reserva_texto": str(ctx.get("hora", "")),
        "personas": str(ctx.get("personas", "")),
        "nombre_reserva": str(ctx.get("nombre_reserva", "")),
        "telefono_contacto": str(ctx.get("telefono", "")),
        "restricciones": str(ctx.get("restricciones", "")),
        "recordatorio_activo": True,
        "anticipacion_horas": 1,
        "origen": "zia_reserva_enviada",
        "creado": datetime.now(timezone.utc).isoformat(),
    }
    memoria.setdefault("recordatorios_reserva", []).append(alerta)
    guardar_memoria(memoria)


def generar_bloque_reserva_final_un_restaurante(
    perfil: dict[str, str],
    ctx: dict[str, str],
) -> str:
    """Resumen final tras elegir un local: datos + enlace TheFork (sin repetir lista de 3)."""
    ciudad = ciudad_para_urls_reserva(perfil)
    nombre_r = (ctx.get("restaurante_elegido") or "").strip()
    restr = ctx.get("restricciones", "").strip() or "Ninguna"
    url_tf = url_thefork_busqueda_restaurante(nombre_r, ciudad)
    return (
        f"📋 RESERVA LISTA:\n"
        f"📅 Fecha: {ctx.get('fecha', '')}\n"
        f"🕐 Hora: {ctx.get('hora', '')}\n"
        f"👥 Personas: {ctx.get('personas', '')}\n"
        f"🍽️ Restaurante: {nombre_r}\n"
        f"👤 Nombre: {ctx.get('nombre_reserva', '')}\n"
        f"📱 Teléfono: {ctx.get('telefono', '')}\n\n"
        f"📝 A notar al reservar: {restr}\n\n"
        f"📲 Reservar en TheFork → {url_tf}\n\n"
        f"¿Hago yo la reserva o prefieres hacerla tú?"
    )


def guardar_reserva_restaurante_en_memoria(
    memoria: dict[str, Any],
    ctx: dict[str, str],
    bloque_generado: str,
) -> None:
    entrada: dict[str, Any] = {
        **ctx,
        "texto_resumen": bloque_generado,
        "fecha_registro": datetime.now(timezone.utc).isoformat(),
    }
    memoria.setdefault("reservas_restaurante", []).append(entrada)
    memoria["ultima_reserva_restaurante"] = entrada
    guardar_memoria(memoria)


def ciudad_para_urls_reserva(perfil: dict[str, str]) -> str:
    c = str(perfil.get("ciudad", "") or perfil.get("ubicacion", "") or "").strip()
    return c if c else "Madrid"


def slug_ciudad_url(ciudad: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", texto_sin_acentos(ciudad.lower()))
    return s if s else "madrid"


def extraer_primer_nombre_restaurante_desde_bloque(bloque: str) -> str:
    m = re.search(
        r"Restaurantes recomendados:\s*\n•\s*([^\n]+)",
        bloque,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ""
    line = m.group(1).strip()
    if line.startswith("•"):
        line = line[1:].strip()
    part = line.split(" - ")[0].strip() if " - " in line else line
    part = re.sub(r"\s*-\s*DESTACADO.*$", "", part, flags=re.IGNORECASE).strip()
    part = part.replace("🏆", "").strip()
    part = re.sub(r"\s*\[[^\]]*\]\s*$", "", part).strip()
    return part


def extraer_primer_telefono_restaurante_desde_bloque(bloque: str) -> str:
    idx = bloque.find("🍽️")
    if idx == -1:
        return ""
    tail = bloque[idx:]
    m = re.search(r"📞\s*([\d+\s().-]{9,})", tail)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1).strip())


def limpiar_pregunta_llamo_al_final_reserva(bloque: str) -> str:
    """Quita la línea final de cierre sobre quién hace la reserva (pregunta al restaurante)."""
    lines = (bloque or "").strip().splitlines()
    if lines:
        last = lines[-1].strip()
        last_l = last.lower()
        if last.startswith("¿") and (
            "llamo" in last_l
            or "hago yo la reserva" in last_l
            or "prefieres hacerla" in last_l
        ):
            lines = lines[:-1]
    return "\n".join(lines).rstrip()


def texto_bloque_reservar_ahora_y_recordatorio(
    memoria: dict[str, Any],
    perfil: dict[str, str],
) -> str:
    ult = memoria.get("ultima_reserva_restaurante") or {}
    resumen = limpiar_pregunta_llamo_al_final_reserva(str(ult.get("texto_resumen", "")))
    ciudad = ciudad_para_urls_reserva(perfil)
    city_slug = slug_ciudad_url(ciudad)
    nombre_r = str(ult.get("restaurante_elegido", "") or "").strip()
    if not nombre_r:
        nombre_r = extraer_primer_nombre_restaurante_desde_bloque(resumen)
    if not nombre_r:
        nombre_r = f"restaurante {ciudad}"
    tel_r = extraer_primer_telefono_restaurante_desde_bloque(resumen)
    q_tf = quote(nombre_r)
    url_tf = f"https://www.thefork.es/buscar?q={q_tf}&city={city_slug}"
    url_goog = f"https://www.google.com/search?q={quote(nombre_r + ' reserva ' + ciudad)}"
    partes: list[str] = [
        resumen,
        "",
        "📲 RESERVAR AHORA:",
        f"• TheFork → {url_tf}",
        f"• Google → {url_goog}",
        "",
    ]
    if tel_r:
        partes.append(f"O llama directamente: {tel_r}")
    else:
        partes.append(
            "O llama directamente al teléfono del restaurante que elijas en la lista de arriba."
        )
    partes.extend(["", "¿Quieres que te recuerde la reserva 2 horas antes? ⏰"])
    return "\n".join(partes)


def guardar_recordatorio_reserva_restaurante(
    memoria: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    horas = (
        1 if str(ctx.get("recordatorio_una_hora", "") or "").strip() == "1" else 2
    )
    alerta: dict[str, Any] = {
        "tipo": "reserva_restaurante",
        "fecha_reserva_texto": str(ctx.get("fecha", "")),
        "hora_reserva_texto": str(ctx.get("hora", "")),
        "personas": str(ctx.get("personas", "")),
        "nombre_reserva": str(ctx.get("nombre_reserva", "")),
        "telefono_contacto": str(ctx.get("telefono", "")),
        "restricciones": str(ctx.get("restricciones", "")),
        "recordatorio_activo": True,
        "anticipacion_horas": horas,
        "creado": datetime.now(timezone.utc).isoformat(),
    }
    memoria.setdefault("recordatorios_reserva", []).append(alerta)
    guardar_memoria(memoria)


def intencion_afirmativo_recordatorio_reserva(texto: str) -> bool:
    ts = texto_sin_acentos(texto.lower().strip())
    if ts in ("si", "sí", "dale", "ok", "vale", "claro", "quiero", "por favor", "yes"):
        return True
    return ts.startswith("sí ") or ts.startswith("si ")


def intencion_negativo_recordatorio_reserva(texto: str) -> bool:
    ts = texto_sin_acentos(texto.lower().strip())
    return ts in ("no", "nop", "nope", "no gracias", "mejor no", "nah")


def intencion_reserva_usuario_llama(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    return any(
        x in tn
        for x in (
            "llamo yo",
            "yo llamo",
            "prefiero llamar yo",
            "prefiero llamar",
            "lo llamo yo",
            "yo prefiero",
            "llamo por mi cuenta",
        )
    )


def intencion_usuario_quiere_que_zia_guie_thefork(texto: str) -> bool:
    """
    Usuario quiere que ZIA gestione la reserva (respuesta a «¿Hago yo la reserva o prefieres hacerla tú?»).
    """
    if intencion_reserva_usuario_llama(texto):
        return False
    tn = texto_sin_acentos((texto or "").lower().strip())
    if not tn:
        return False
    if "no quiero que" in tn or "no prefiero que" in tn:
        return False
    if tn in ("tu", "tú", "zia", "si tu", "sí tu", "ok tu", "vale tu"):
        return True
    return any(
        x in tn
        for x in (
            "llama tu",
            "llamas tu",
            "llamalo tu",
            "llámalo tú",
            "que llames tu",
            "hazlo tu",
            "hazla tu",
            "hazla tú",
            "que la hagas tu",
            "que la hagas tú",
            "haz la reserva",
            "haz tu la reserva",
            "reserva por mi",
            "reserva por mí",
            "reserva tu",
            "prefiero que llames",
            "prefiero que reserves",
            "buscalo tu",
            "búscalo tú",
            "encargate tu",
            "encárgate tu",
        )
    )


def resumen_ingredientes_voz(ing_txt: str) -> str:
    """Texto corto para hablar de lo que hay en la nevera."""
    items: list[str] = []
    for line in (ing_txt or "").splitlines():
        s = line.strip()
        if s.startswith("•"):
            items.append(s[1:].strip())
    if items:
        sjoin = ", ".join(items[:10])
        return sjoin + ("…" if len(items) > 10 else "")
    t = (ing_txt or "").strip()
    if len(t) > 200:
        return t[:200] + "…"
    return t or "varias cosas"


def intencion_comparar_otras_tiendas(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if re.search(r"\bno\s+(?:quiero\s+)?(?:ver\s+)?(?:compar|mirar\s+precios)", tn):
        return False
    if any(
        x in tn
        for x in (
            "mas barato",
            "más barato",
            "mas barata",
            "barato en otro",
            "comparar",
            "compara",
            "otra cadena",
            "otras cadenas",
            "otras tiendas",
            "otro sitio",
            "donde sale",
            "donde es mas barato",
            "ver precios",
            "ver si sale",
            "precios en",
            "echar un vistazo",
            "mirar en",
            "todas las tiendas",
        )
    ):
        return True
    if tn in ("otro", "otra", "no se", "no sé", "míralo", "mira a ver"):
        return True
    return False


def intencion_comparar_cercania(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if "comparar" in tn and "precio" in tn:
        return False
    if "mas barato" in tn or "más barato" in tn:
        return False
    return any(
        x in tn
        for x in (
            "cercania",
            "cercanía",
            "cerca de casa",
            "mas cerca",
            "más cerca",
            "tienda cerca",
            "donde hay mas tiendas",
            "donde hay más tiendas",
            "distancia",
        )
    )


def intencion_comparar_entrega_online(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    return any(
        x in tn
        for x in (
            "entrega",
            "reparto",
            "domicilio",
            "rapidez",
            "rapido",
            "rápido",
            "compra online",
            "online",
            "recogida",
            "click",
            "envio",
            "envío",
            "a domicilio",
        )
    )


def texto_orientacion_cercania_supermercados(perfil: dict[str, str]) -> str:
    ciudad = (perfil.get("ciudad") or perfil.get("ubicacion") or "tu zona").strip() or "tu zona"
    ns = nombre_supermercado_perfil(perfil)
    return (
        f"En {ciudad}, la cercanía depende del barrio: Mercadona, Carrefour y Consum suelen tener muchas tiendas; "
        f"Lidl y Aldi también son frecuentes. Tu cadena habitual ({ns}) es buena opción si ya compras ahí cerca. "
        "Para ver cuál está más cerca de casa, revisa Maps o la app de la cadena.\n"
    )


def texto_orientacion_entrega_supermercados() -> str:
    return (
        "Orientación rápida: Amazon Fresh y Carrefour suelen tener entrega en muchas zonas; "
        "Mercadona y Consum también en muchas ciudades con franja; Lidl y Aldi suelen tener menos cobertura de reparto. "
        "Comprueba tu código postal en la web de cada cadena.\n"
    )


def intencion_quedarse_super_habitual(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if intencion_comparar_otras_tiendas(texto):
        return False
    if any(
        x in tn
        for x in (
            "mi super",
            "el de siempre",
            "habitual",
            "donde suelo",
            "me quedo",
            "así está bien",
            "asi esta bien",
            "en el mio",
            "en el mío",
            "donde compro",
            "te lo pido",
            "pide en",
            "quedamos",
            "en mercadona",
            "en lidl",
            "en aldi",
            "en carrefour",
            "en el corte",
            "en dia",
            "en consum",
            "en amazon",
        )
    ):
        return True
    if tn in ("sí", "si", "vale", "ok", "claro", "bueno", "de acuerdo", "perfecto", "genial", "sip"):
        return True
    return False


def respuesta_es_demasiado_ambigua(texto: str) -> bool:
    tn = texto_sin_acentos(texto.strip().lower())
    return len(tn) <= 3 and tn in ("sí", "si", "ok", "vale", "no")


def dispara_bloqueo_cocina_sin_ideas(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    return any(
        f in tn
        for f in (
            "no se que cocinar",
            "no sé qué cocinar",
            "no tengo ideas",
            "que hago con lo que tengo",
            "qué hago con lo que tengo",
            "estoy aburrido",
            "siempre lo mismo",
            "aburrido de siempre",
            "rutina de comida",
        )
    )


def generar_respuesta_bloqueo_cocina(
    client: OpenAI,
    perfil: dict[str, str],
    texto_usuario: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": system_zia_completo()
            + "\n\nEl usuario está bloqueado, sin ideas o aburrido de la rutina en la cocina. "
            "Responde con calidez y cercanía (varias frases cortas). "
            "Sugiere que si quiere puede mandarte una foto de la nevera para ideas concretas (📸). "
            "No menciones suplementos ni enlaces de compra salvo que el usuario los pida explícitamente en este mensaje. "
            "Cierra con UNA pregunta natural. NO uses menús numerados ni botones.",
        },
        {"role": "user", "content": texto_usuario},
    ]
    return completar(client, messages, temperature=0.7, max_tokens=600)


def raiz_proyecto() -> Path:
    return Path(__file__).resolve().parent


def productos_desde_texto_plan(texto: str) -> list[str]:
    """Extrae nombres de productos desde líneas tipo • ítem → precio en planes/listas."""
    out: list[str] = []
    for line in (texto or "").splitlines():
        s = line.strip()
        if not s.startswith("•"):
            continue
        s = s[1:].strip()
        if "→" in s:
            s = s.split("→")[0].strip()
        s = re.sub(r"\s+", " ", s)
        if len(s) > 2 and not s.upper().startswith("TOTAL"):
            out.append(s)
    return out[:120]


def url_carrito_desde_memoria(memoria: dict[str, Any]) -> str:
    """URL file:// a carrito.html con ?items= si hay productos deducibles del último plan."""
    html = raiz_proyecto() / "carrito.html"
    base = html.as_uri()
    items = productos_desde_texto_plan(str(memoria.get("ultimo_plan") or ""))
    if not items:
        return base
    payload = ",".join(name.replace(" ", "+") for name in items)
    return f"{base}?items={quote(payload, safe=',+')}"


def system_zia_completo() -> str:
    return SYSTEM_BASE + "\n\n" + INSTRUCCION_CIERRE_ZIA + "\n\n" + REGLA_SUPLEMENTOS_ENLACES_AFILIADOS


# Prioridad máxima en llamadas con imagen (debe ir antes que el resto del system).
SYSTEM_VISION_CORE = (
    "You are analyzing this image. Describe exactly what food you see and provide the recipe. "
    "Never say you cannot see images."
)


def system_para_vision() -> str:
    return (
        SYSTEM_VISION_CORE
        + "\n\n"
        "The image is attached to this conversation. You can see it. Begin your answer by describing the food "
        "or products visible. Do not refuse, hedge, or apologize for vision. Forbidden in any language: claiming "
        "you cannot see images, photos, or attachments; saying you lack visual access; or similar disclaimers.\n\n"
        + system_zia_completo()
        + "\n\nAnálisis de imagen (comida/plato/nevera): no recomiendes suplementos ni enlaces de compra de suplementos "
        "salvo que el mensaje del usuario pida explícitamente suplementos."
    )


def prefijo_instruccion_vision_usuario() -> str:
    """Refuerzo en el mensaje de usuario (la imagen va en el mismo turno)."""
    return (
        "You are analyzing this image. Describe exactly what food you see and provide the recipe. "
        "Never say you cannot see images.\n"
        "Empieza describiendo con seguridad lo que ves en la foto. Prohibido decir que no puedes ver imágenes.\n\n"
    )


def crear_cliente() -> OpenAI:
    return OpenAI(api_key=API_KEY)


def perfil_a_texto(perfil: dict[str, str]) -> str:
    return json.dumps(perfil, ensure_ascii=False, indent=2)


def texto_sin_acentos(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


# id interno → nombre visible, URL única de la tienda (sin búsqueda por producto)
SUPER_TIENDA_URL: dict[str, tuple[str, str]] = {
    "mercadona": ("Mercadona", "https://tienda.mercadona.es"),
    "lidl": ("Lidl", "https://www.lidl.es"),
    "aldi": ("Aldi", "https://www.aldi.es"),
    "carrefour": ("Carrefour", "https://www.carrefour.es"),
    "eci": ("El Corte Inglés", "https://www.elcorteingles.es/supermercado"),
    "dia": ("Dia", "https://www.dia.es"),
    "consum": ("Consum", "https://tienda.consum.es"),
    "amazon": ("Amazon Fresh", "https://www.amazon.es"),
}

# Factor multiplicador del precio vs una cesta de referencia tipo Mercadona (base 1.00)
FACTOR_PRECIO_VS_MERCADONA: dict[str, float] = {
    "mercadona": 1.00,
    "lidl": 0.88,
    "aldi": 0.85,
    "carrefour": 1.05,
    "eci": 1.20,
    "dia": 1.00,
    "consum": 1.00,
    "amazon": 1.10,
}

# Orden fijo de bloques en modo multi-supermercado
ORDEN_CADENAS_EN_PROMPT: list[str] = [
    "mercadona",
    "lidl",
    "aldi",
    "carrefour",
    "eci",
    "dia",
    "consum",
    "amazon",
]


def texto_factores_precio_supermercados() -> str:
    """Texto reutilizable para prompts (lista de factores vs Mercadona)."""
    lineas = [
        "Factores de precio respecto a una referencia tipo Mercadona (base Mercadona = 1,00):",
    ]
    for cid in ORDEN_CADENAS_EN_PROMPT:
        nombre, _ = SUPER_TIENDA_URL[cid]
        f = FACTOR_PRECIO_VS_MERCADONA[cid]
        lineas.append(f"- {nombre}: ×{f:.2f}")
    lineas.append(
        "Para cada supermercado, estima el precio de cada ítem como: precio_referencia_mercadona × factor_de_esa_tienda "
        "(primero fija precios referencia Mercadona por ítem coherentes con la lista; luego aplica el factor)."
    )
    return "\n".join(lineas)


def texto_urls_base_supermercados() -> str:
    """Lista nombre → URL para prompts que deben enlazar a la tienda sin inventar dominios."""
    lineas = ["URLs oficiales (copia exacta al cerrar «Comprar en …»):"]
    for cid in ORDEN_CADENAS_EN_PROMPT:
        nombre, url = SUPER_TIENDA_URL[cid]
        lineas.append(f"- {nombre} → {url}")
    return "\n".join(lineas)


def texto_instruccion_seccion_afiliados() -> str:
    """Suplementos: enlaces Amazon (tag zia-nutricion-21) y MyProtein solo si el usuario lo pide; ver REGLA_SUPLEMENTOS_ENLACES_AFILIADOS."""
    return ""


def ids_supermercados_detectados(texto: str) -> list[str]:
    """Detecta cadenas mencionadas en el campo supermercado del perfil. Vacío o ambiguo → se asume multi."""
    tn = texto_sin_acentos((texto or "").lower())
    ids: list[str] = []

    def add(x: str) -> None:
        if x not in ids:
            ids.append(x)

    compact = tn.replace(" ", "")
    if "elcorteingles" in compact or "corteingles" in compact or "elcorteingles" in tn:
        add("eci")
    elif "corteingles" in compact or re.search(r"el[\s]+corte[\s]+ingles", tn):
        add("eci")
    elif "corte ingles" in tn:
        add("eci")
    if "mercadona" in tn:
        add("mercadona")
    if "carrefour" in tn:
        add("carrefour")
    if "lidl" in tn:
        add("lidl")
    if "aldi" in tn:
        add("aldi")
    if re.search(r"\bdia\b", tn):
        add("dia")
    if "consum" in tn:
        add("consum")
    if "amazon" in tn:
        add("amazon")
    return ids


def bloque_enlaces_supermercados(perfil: dict[str, str]) -> str:
    """Ya no se usan bloques largos de enlaces en prompts; la UI gestiona compra y comparativa."""
    return ""


def dispara_comparar_precios(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if "mejor precio" in tn:
        return True
    if "mas barato" in tn:
        return True
    if re.search(r"\bcomparar\b", tn):
        return True
    return False


def dispara_optimizar_compra_inteligente(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if "optimizar compra" in tn:
        return True
    if "compra inteligente" in tn:
        return True
    return False


def mensaje_comparar_precios_supermercados(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    ultimo = (memoria.get("ultimo_plan") or "").strip()
    trozo = ultimo[:14000] if len(ultimo) > 14000 else ultimo
    vacio = not trozo
    urls_bloque = f"\n{texto_urls_base_supermercados()}\n" if ACUERDO_COMERCIAL_ENLACES else ""
    return f"""COMPARATIVA DE PRECIOS ENTRE SUPERMERCADOS (España)

Perfil familiar:
{perfil_a_texto(perfil)}

Lista o plan de referencia (último plan guardado):
---
{trozo if trozo else "(no hay plan guardado: construye una lista de compra semanal familiar razonable y compárala)"}
---

{texto_factores_precio_supermercados()}
{urls_bloque}
FORMATO (modo comparar — solo totales):
1) NO desglose producto a producto entre supermercados.
2) RESUMEN DE TOTALES: una línea por supermercado con TOTAL ESTIMADO (usa factores). Incluye Mercadona, Lidl, Aldi, Carrefour, El Corte Inglés, Dia, Amazon Fresh. Marca el más barato con ⭐ (o «⭐ MÁS BARATO» al final de esa línea).
3) NO incluyas listas detalladas por tienda ni enlaces a supermercados{" salvo que se indique lo contrario" if ACUERDO_COMERCIAL_ENLACES else ""}.
NO suplementos ni enlaces de afiliado.
{"4) Si no hay plan previo, indica al inicio que la lista se ha inferido." if vacio else ""}

Responde en español, secciones claras."""


def mensaje_optimizar_compra_multisuper(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    ultimo = (memoria.get("ultimo_plan") or "").strip()
    trozo = ultimo[:14000] if len(ultimo) > 14000 else ultimo
    vacio = not trozo
    return f"""COMPRA INTELIGENTE MULTI-SUPERMERCADO

Perfil familiar:
{perfil_a_texto(perfil)}

Lista de compra / último plan:
---
{trozo if trozo else "(no hay plan guardado: propón una lista semanal familiar y optimízala entre tiendas)"}
---

{texto_factores_precio_supermercados()}

TAREA:
1) Reparte la compra indicando en qué supermercado conviene comprar cada producto o grupo para minimizar el coste total (incluye Aldi en el abanico de opciones).
2) Propón una ruta o itinerario de compra (orden de visitas / prioridad) razonable para una familia.
3) Totales estimados por tienda y total del plan multi-super; usa los factores de precio anteriores de forma coherente.
4) Precios orientativos; indica supuestos.
{"5) Indica al inicio que la lista se ha inferido por no haber plan guardado." if vacio else ""}

NO suplementos ni enlaces de afiliado.
{"NO enlaces ni URLs a webs de supermercados." if not ACUERDO_COMERCIAL_ENLACES else ""}

Español, secciones con títulos en MAYÚSCULAS donde ayude."""


def generar_respuesta_modo_supermercado(
    client: OpenAI,
    contenido_usuario: str,
    max_tokens: int = 6144,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {"role": "user", "content": contenido_usuario},
    ]
    return completar(client, messages, max_tokens=max_tokens)


def dispara_seguimiento_semanal(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    if "seguimiento" in tn:
        return True
    if "como fue la semana" in tn:
        return True
    if "nueva semana" in tn:
        return True
    return False


def bloque_prompt_seguimiento_semanal(como_fue: str, repetir_evitar: str) -> str:
    return f"""SEGUIMIENTO SEMANAL — Respuestas del usuario sobre la semana anterior:
- Cómo fue la semana (plan, adherencia, sensaciones): {como_fue.strip()}
- Qué recetas quiere repetir y cuáles evitar u omitir: {repetir_evitar.strip()}

A partir de esto, genera un plan NUEVO para la semana que entra. Varía de forma clara respecto al último plan en memoria: no repitas los mismos platos principales ni copiar la misma lista de compra. Si el usuario pidió repetir algún plato concreto, inclúyelo de nuevo en el nuevo menú; si pidió evitar algo, no lo propongas."""


def guardar_feedback_seguimiento(
    memoria: dict[str, Any],
    como_fue: str,
    repetir_evitar: str,
) -> None:
    entrada: dict[str, Any] = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "como_fue_la_semana": como_fue.strip(),
        "repetir_o_evitar_recetas": repetir_evitar.strip(),
    }
    memoria.setdefault("seguimiento_semanal", []).append(entrada)
    guardar_memoria(memoria)


def generar_plan_semanal_respuesta(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    bloque_seguimiento: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {"role": "user", "content": mensaje_plan_semanal(perfil, memoria, bloque_seguimiento)},
    ]
    texto = completar(client, messages, max_tokens=8192)
    return texto, messages


def mensaje_plan_semanal(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    bloque_seguimiento: str | None = None,
) -> str:
    extra = contexto_memoria_para_prompt(memoria)
    seg = f"\n{bloque_seguimiento}\n" if bloque_seguimiento else ""
    return f"""Con el siguiente perfil familiar, genera un PLAN SEMANAL completo (solo menús; NO incluyas lista de la compra).

Perfil:
{perfil_a_texto(perfil)}
{extra}
{seg}
OBLIGATORIO — LOS SIETE DÍAS COMPLETOS:
- Incluye obligatoriamente, en este orden y con sección claramente titulada para cada uno: LUNES, MARTES, MIÉRCOLES, JUEVES, VIERNES, SÁBADO y DOMINGO.
- No omitas ningún día. No agrupes varios días en un solo bloque. No digas «el resto de la semana igual» ni des solo un ejemplo de uno o dos días.
- Cada uno de los siete días debe tener su desayuno, comida y cena (o el esquema de comidas que encaje con la familia), con platos concretos.

Requisitos del plan:
1. Incluye recetas caseras que se puedan hacer en MENOS DE 20 MINUTOS (indica tiempo estimado) repartidas a lo largo de la semana.
2. En varias comidas, sugiere productos PREPARADOS del supermercado (ensaladas, hummus, pollo asado, etc.) mezclados con algo mínimo en casa si hace falta.
3. Sé concreto con nombres de platos y ideas prácticas en todos los días.

NO incluyas LISTA DE LA COMPRA ni precios de cesta (se generará aparte si el usuario lo pide).
NO frase motivacional al final (la añade el programa)."""


def mensaje_lista_compra_para_super(
    perfil: dict[str, str],
    plan_texto: str,
) -> str:
    ids = ids_supermercados_detectados(perfil.get("supermercado", ""))
    cid = ids[0] if len(ids) >= 1 else "mercadona"
    nombre, _url = SUPER_TIENDA_URL[cid]
    factor = FACTOR_PRECIO_VS_MERCADONA.get(cid, 1.0)
    pres = presupuesto_semanal_euros(perfil)
    pres_bloque = ""
    if pres is not None:
        pres_bloque = (
            f"\nPresupuesto semanal del usuario: {pres:.2f}€. "
            "Los precios línea a línea y el TOTAL deben ser realistas; si el conjunto superaría claramente ese tope, "
            "prioriza productos o cantidades más económicos manteniendo el plan cubierto.\n"
        )
    enlace_o_no = (
        f"Última línea exacta: 🛒 Comprar en {nombre} → {_url}\n"
        if ACUERDO_COMERCIAL_ENLACES
        else "NO incluyas enlaces, URLs ni líneas «Comprar en …» (la app no muestra tiendas hasta acuerdo comercial).\n"
    )
    return f"""The user wants shopping list prices AS IF they bought EVERYTHING at ONE chain only.

The user's habitual supermarket is: {nombre} (internal price factor vs Mercadona reference: ×{factor:.2f}).
City / context from profile: use {perfil.get("ciudad", "") or perfil.get("ubicacion", "") or "España"} only as locale context, NOT to mix other chains' prices.

Con este PLAN SEMANAL, genera ÚNICAMENTE la LISTA DE LA COMPRA para cubrir esa semana.

PLAN:
---
{plan_texto.strip()}
---

OBLIGATORIO — UNA SOLA CADENA EN LOS PRECIOS:
- TODOS los importes (cada línea y el TOTAL ESTIMADO) deben ser precios orientativos como si la compra entera fuera en **{nombre}** únicamente.
- Para estimar: usa precio referencia tipo Mercadona por ítem coherente con el producto, luego aplica ×{factor:.2f} para reflejar el nivel de precio de {nombre}.
- NO mezcles precios de otras cadenas en la misma lista. NO pongas alternativas «en otra tienda».
{pres_bloque}
Estructura:
- FRESCO: líneas • producto (cantidad) → XX.XX€
- PREPARADO: líneas • producto (cantidad) → XX.XX€
- TOTAL ESTIMADO: XX.XX€
{enlace_o_no}
Perfil: {perfil_a_texto(perfil)}
NO suplementos. NO frase motivacional. NO preguntas."""


def generar_lista_compra_respuesta(
    client: OpenAI,
    perfil: dict[str, str],
    plan_texto: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_LISTA_COMPRA},
        {"role": "user", "content": mensaje_lista_compra_para_super(perfil, plan_texto)},
    ]
    return completar(client, messages, temperature=0.55, max_tokens=4096)


def mensaje_totales_solo_lineas(memoria: dict[str, Any]) -> str:
    ref = (memoria.get("ultimo_plan") or "").strip()
    return f"""Basándote en la lista de la compra / plan guardado, estima el TOTAL de la misma cesta en cada cadena (mismos productos y cantidades).
---
{ref[:14000]}
---
{texto_factores_precio_supermercados()}

Salida: SOLO estas 7 líneas, sin título ni párrafos ni texto extra, en este orden:
🏪 Mercadona → XX.XX€
🏪 Lidl → XX.XX€
🏪 Aldi → XX.XX€
🏪 Carrefour → XX.XX€
🏪 El Corte Inglés → XX.XX€
🏪 Dia → XX.XX€
🏪 Amazon Fresh → XX.XX€

Añade un espacio y «⭐ MÁS BARATO» solo al final de la línea del total más bajo (tras el importe)."""


def generar_totales_comparativa(client: OpenAI, memoria: dict[str, Any]) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_SOLO_TOTALES},
        {"role": "user", "content": mensaje_totales_solo_lineas(memoria)},
    ]
    return completar(client, messages, temperature=0.35, max_tokens=512)


def mensaje_lista_detallada_en_tienda(cid: str, memoria: dict[str, Any]) -> str:
    nombre, url = SUPER_TIENDA_URL[cid]
    factor = FACTOR_PRECIO_VS_MERCADONA.get(cid, 1.0)
    ref = (memoria.get("ultimo_plan") or "").strip()
    cierre = (
        f"línea final 🛒 Comprar en {nombre} → {url}"
        if ACUERDO_COMERCIAL_ENLACES
        else "sin enlaces ni URLs de tienda."
    )
    return f"""A partir de este plan/lista, genera la lista de compra COMPLETA para comprar todo en {nombre}.

Referencia:
---
{ref[:14000]}
---

Usa precios coherentes (factor ×{factor:.2f} vs referencia Mercadona por ítem).
Formato: • producto (cantidad) → XX.XX€ ; TOTAL ESTIMADO ; {cierre}
NO suplementos. NO frase motivacional."""


def generar_lista_para_tienda(
    client: OpenAI,
    memoria: dict[str, Any],
    cid: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_LISTA_COMPRA},
        {"role": "user", "content": mensaje_lista_detallada_en_tienda(cid, memoria)},
    ]
    return completar(client, messages, temperature=0.55, max_tokens=4096)


def es_respuesta_si_lista(texto: str) -> bool:
    t = texto.strip().lower()
    return t in ("sí", "si", "s", "yes", "ok", "vale", "claro")


def es_respuesta_no_lista(texto: str) -> bool:
    t = texto.strip().lower()
    return t in ("no", "n")


def cid_supermercado_habitual_perfil(perfil: dict[str, str]) -> str:
    """Cadena principal del perfil (misma lógica que la lista de compra inicial)."""
    ids = ids_supermercados_detectados(perfil.get("supermercado", ""))
    return ids[0] if len(ids) >= 1 else "mercadona"


def imprimir_pedido_listo_tienda_habitual_sin_repetir_lista(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    cid: str,
) -> str:
    """
    Tras confirmar la compra, la lista ya se mostró. Si el usuario elige su super habitual,
    no volver a generar ni imprimir el carrito: solo cierre con enlace y total.
    """
    nombre, url = SUPER_TIENDA_URL[cid]
    lista_actual = (memoria.get("lista_compra_actual") or "").strip()
    tot = total_lista_a_float(lista_actual)
    tot_s = f"{tot:.2f}" if tot is not None else "—"
    bloque = (
        f"✅ Perfecto. Tu pedido en {nombre} está listo.\n"
        f"🛒 Ir a {nombre} → {url}\n"
        f"💰 Total: {tot_s}€\n\n"
        "¿Necesitas algo más?"
    )
    print("\n" + bloque + "\n")
    return bloque


def detectar_id_supermercado_en_texto(texto: str) -> str | None:
    tn = texto_sin_acentos(texto.lower().strip())
    if not tn:
        return None
    if "mercadona" in tn:
        return "mercadona"
    if "lidl" in tn:
        return "lidl"
    if "aldi" in tn:
        return "aldi"
    if "carrefour" in tn:
        return "carrefour"
    if "corte" in tn and "ingles" in tn:
        return "eci"
    if "eci" in tn.replace(" ", ""):
        return "eci"
    if re.search(r"\bdia\b", tn):
        return "dia"
    if "consum" in tn:
        return "consum"
    if "amazon" in tn:
        return "amazon"
    return None


def url_super_principal_o_default(perfil: dict[str, str]) -> str:
    ids = ids_supermercados_detectados(perfil.get("supermercado", ""))
    cid = ids[0] if ids else "mercadona"
    return SUPER_TIENDA_URL[cid][1]


def mensaje_chat_libre(perfil: dict[str, str], texto_usuario: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system_zia_completo() + "\n\nPerfil actual:\n" + perfil_a_texto(perfil)},
        {"role": "user", "content": texto_usuario},
    ]


def completar(
    client: OpenAI,
    messages: list[dict[str, Any]],
    temperature: float = 0.65,
    max_tokens: int = 4096,
) -> str:
    respuesta = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = respuesta.choices[0].message.content
    return content or ""


def ejecutar_onboarding() -> dict[str, str]:
    perfil: dict[str, str] = {}
    print("\n=== ZIA — Tu nutricionista personal ===\n")
    print("ZIA: ¡Hola! Soy ZIA, tu nutricionista personal. Voy a hacerte unas preguntas rápidas para personalizar tu experiencia.\n")
    print("ZIA: ¿El plan es para ti solo o para toda tu familia?")
    print("     1️⃣  Para mí solo")
    print("     2️⃣  Para mi familia\n")

    while True:
        tipo = input("Tú: ").strip().lower()
        if tipo in ("salir",):
            raise SystemExit(0)
        if tipo in ("1", "1️⃣", "solo", "mi solo", "para mi", "para mí", "individual", "yo"):
            perfil["tipo_plan"] = "individual"
            preguntas = ONBOARDING_QUESTIONS_INDIVIDUAL
            print("\nZIA: Perfecto, vamos a crear tu plan personalizado.\n")
            break
        if tipo in ("2", "2️⃣", "familia", "familiar", "todos", "para mi familia", "para toda la familia"):
            perfil["tipo_plan"] = "familiar"
            preguntas = ONBOARDING_QUESTIONS_FAMILIAR
            print("\nZIA: Perfecto, vamos a crear el plan para toda la familia.\n")
            break
        print("ZIA: Escribe 1 para plan individual o 2 para plan familiar.\n")

    for campo, pregunta in preguntas:
        print(f"ZIA: {pregunta}")
        respuesta = input("Tú: ").strip()
        if respuesta.lower() == "salir":
            raise SystemExit(0)
        while not respuesta:
            print("ZIA: Necesito una respuesta (o escribe «salir»).")
            respuesta = input("Tú: ").strip()
            if respuesta.lower() == "salir":
                raise SystemExit(0)

        # Validar objetivo individual — solo uno
        if campo == "objetivo":
            mapa = {
                "1": "Perder grasa", "1️⃣": "Perder grasa",
                "2": "Ganar músculo", "2️⃣": "Ganar músculo",
                "3": "Mantenimiento", "3️⃣": "Mantenimiento",
                "4": "Comer más sano", "4️⃣": "Comer más sano",
                "5": "Más energía", "5️⃣": "Más energía",
            }
            if respuesta in mapa:
                respuesta = mapa[respuesta]
            else:
                # Detectar si pone dos objetivos
                rl = respuesta.lower()
                if any(x in rl for x in (" y ", ",", " también")):
                    print("\nZIA: Entiendo que quieres las dos cosas, pero para darte el mejor plan necesito que elijas tu objetivo PRINCIPAL ahora.\nPuedes ajustarlo más adelante.\n")
                    print("ZIA: ¿Cuál es tu objetivo principal?\n1️⃣ Perder grasa\n2️⃣ Ganar músculo\n3️⃣ Mantenimiento\n4️⃣ Comer más sano\n5️⃣ Más energía\n")
                    while True:
                        respuesta = input("Tú: ").strip()
                        if respuesta in mapa:
                            respuesta = mapa[respuesta]
                            break
                        # Intentar detectar palabra clave
                        rl2 = respuesta.lower()
                        if "grasa" in rl2 or "peso" in rl2:
                            respuesta = "Perder grasa"; break
                        if "musc" in rl2:
                            respuesta = "Ganar músculo"; break
                        if "manten" in rl2:
                            respuesta = "Mantenimiento"; break
                        if "sano" in rl2 or "salud" in rl2:
                            respuesta = "Comer más sano"; break
                        if "energ" in rl2:
                            respuesta = "Más energía"; break
                        print("ZIA: Escribe el número (1-5) o el nombre del objetivo.\n")

        # Validar tiempo cocina
        if campo == "tiempo_cocina":
            mapa_t = {
                "1": "menos de 20 minutos", "1️⃣": "menos de 20 minutos",
                "2": "entre 20 y 40 minutos", "2️⃣": "entre 20 y 40 minutos",
                "3": "tengo tiempo, me gusta cocinar", "3️⃣": "tengo tiempo, me gusta cocinar",
            }
            if respuesta in mapa_t:
                respuesta = mapa_t[respuesta]

        perfil[campo] = respuesta

    # Compatibilidad: asegurar campos comunes
    if "num_personas" not in perfil:
        perfil["num_personas"] = "1"

    return perfil


def system_chat_con_memoria(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    base = system_zia_completo() + "\n\nPerfil:\n" + perfil_a_texto(perfil)
    ctx = contexto_memoria_para_prompt(memoria)
    if ctx:
        base += ctx
    base += (
        "\n\nConversación: mantén un hilo natural; evita sonar a formulario. "
        "No repitas opciones tipo menú si el usuario no las ha pedido. "
        "Orienta hacia comer bien y tener claro qué comprar."
    )
    ult_res = memoria.get("ultima_reserva_restaurante") or {}
    if ult_res.get("reserva_enviada_por_zia"):
        base += (
            "\n\nReserva de restaurante: ya consta una reserva gestionada por ZIA en memoria. "
            "Si el usuario pregunta por su reserva, resume los datos guardados; no digas que no puedes reservar "
            "ni insistas en que llame él ni en enlaces externos como única vía."
        )
    return base


def dispara_nevera_inteligente(texto: str) -> bool:
    tl = texto.lower()
    if "nevera" in tl:
        return True
    if re.search(r"(?i)qu[eé]\s+cocino\s+con", texto):
        return True
    # «tengo» solo al inicio del mensaje para no confundir con «tengo una duda», etc.
    return bool(re.match(r"(?i)\s*tengo\b", texto.strip()))


def ingredientes_tras_trigger(texto: str) -> str:
    t = texto
    t = re.sub(r"(?i)qu[eé]\s+cocino\s+con", " ", t)
    for p in ("nevera inteligente", "nevera"):
        t = re.sub(re.escape(p), " ", t, flags=re.IGNORECASE)
    t = re.sub(r"(?i)\btengo\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,;:-")
    return t


def tiene_lista_ingredientes_explicita(texto: str) -> bool:
    resto = ingredientes_tras_trigger(texto)
    if len(resto) < 6:
        return False
    rlow = resto.lower()
    if "," in resto or " y " in rlow:
        return True
    return len(resto.split()) >= 4


def mensaje_nevera_inteligente(perfil: dict[str, str], memoria: dict[str, Any], ingredientes: str) -> str:
    extra = contexto_memoria_para_prompt(memoria)
    return f"""NEVERA INTELIGENTE — El usuario tiene SOLO estos ingredientes en casa (más lo básico que asumas en cualquier cocina: sal, aceite, pimienta si encaja):

{ingredientes.strip()}

Perfil familiar (respeta alergias/restricciones y número de comensales):
{perfil_a_texto(perfil)}
{extra}

Instrucciones:
1. Propón EXACTAMENTE 3 recetas distintas, cada una preparable en MENOS DE 20 minutos (indica tiempo estimado por receta).
2. Cada receta debe usar ÚNICAMENTE ingredientes de la lista del usuario; no añadas productos nuevos salvo condimentos básicos (sal, aceite, especias) si son imprescindibles. Si con lo listado es imposible un plato razonable, dilo en una frase y da la mejor opción posible con lo disponible.
3. Sé concreto con pasos breves.

Después, una sección titulada COMPLEMENTOS ECONÓMICOS: sugiere 1 o 2 ingredientes baratos que podrían comprar para ampliar platos (nombre del producto y precio orientativo aproximado en euros en España).
NO suplementos en polvo ni tiendas de suplementos."""


def generar_nevera_inteligente(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    ingredientes: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {
            "role": "user",
            "content": mensaje_nevera_inteligente(perfil, memoria, ingredientes),
        },
    ]
    return completar(client, messages, temperature=0.65, max_tokens=3072)


def dispara_modo_deporte(texto: str) -> bool:
    tn = texto_sin_acentos(texto.lower())
    for kw in ("deporte", "gym", "musculo", "proteina"):
        if kw in tn:
            return True
    return False


def extraer_peso_kg_desde_texto(texto: str) -> float | None:
    m = re.search(r"\b(\d{2,3})\s*(?:kg|kilos?)\b", texto, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        if 35 <= v <= 250:
            return v
    nums = [int(x) for x in re.findall(r"\b(\d{2,3})\b", texto)]
    for n in nums:
        if 40 <= n <= 140:
            return float(n)
    return None


def extraer_dias_entreno_desde_texto(texto: str) -> int:
    m = re.search(
        r"(\d{1,2})\s*(?:d[ií]as?|d\/sem|veces|por\s*semana|entrenos?|sesiones?)",
        texto,
        re.IGNORECASE,
    )
    if m:
        return max(1, min(int(m.group(1)), 7))
    m2 = re.search(r"\b([1-7])\b", texto)
    if m2:
        return int(m2.group(1))
    return 3


def clasificar_objetivo_deporte(texto: str) -> str:
    tn = texto_sin_acentos(texto.lower())
    if any(
        x in tn
        for x in (
            "perder grasa",
            "perder peso",
            "deficit",
            "adelgaz",
            "cut",
        )
    ):
        return "perder_grasa"
    if any(
        x in tn
        for x in (
            "ganar musculo",
            "masa muscular",
            "hipertrofia",
            "volumen",
            "bulk",
        )
    ):
        return "ganar_musculo"
    if "grasa" in tn and ("perder" in tn or "quemar" in tn):
        return "perder_grasa"
    if "musculo" in tn or "muscular" in tn:
        return "ganar_musculo"
    return "rendimiento"


def calcular_macros_diarios_deporte(
    peso_kg: float,
    objetivo_clave: str,
    dias_entreno: int,
) -> dict[str, float]:
    dias_entreno = max(1, min(int(dias_entreno), 7))
    factor_act = 1.32 + 0.04 * dias_entreno
    tdee_aprox = peso_kg * 24 * factor_act

    if objetivo_clave == "ganar_musculo":
        kcal = tdee_aprox + 250
        prot_x_kg = 2.0
        fat_x_kg = 0.9
    elif objetivo_clave == "perder_grasa":
        kcal = max(tdee_aprox - 450, peso_kg * 22)
        prot_x_kg = 2.0
        fat_x_kg = 0.8
    else:
        kcal = tdee_aprox
        prot_x_kg = 1.7
        fat_x_kg = 0.85

    p_g = round(peso_kg * prot_x_kg)
    f_g = round(max(peso_kg * fat_x_kg, kcal * 0.22 / 9))
    resto_kcal = kcal - p_g * 4 - f_g * 9
    c_g = max(round(resto_kcal / 4), 100)

    return {
        "kcal_aprox": round(kcal),
        "proteinas_g": float(p_g),
        "grasas_g": float(f_g),
        "carbohidratos_g": float(c_g),
    }


def mensaje_nutricion_deporte(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    deporte: str,
    frecuencia: str,
    objetivo_respuesta: str,
    peso_kg: float,
    macros: dict[str, float],
    peso_inferido: bool,
) -> str:
    extra = contexto_memoria_para_prompt(memoria)
    pi = (
        " (peso estimado por defecto 70 kg; el usuario no indicó peso claramente)"
        if peso_inferido
        else ""
    )
    m = macros
    return f"""MODO NUTRICIÓN DEPORTE — Genera un plan de alimentación orientado al rendimiento y la recuperación.

Perfil familiar:
{perfil_a_texto(perfil)}
{extra}

Datos de entrenamiento del usuario:
- Deporte o actividad: {deporte.strip()}
- Frecuencia / volumen: {frecuencia.strip()}
- Objetivo y datos aportados: {objetivo_respuesta.strip()}{pi}
- Peso usado para el cálculo: {peso_kg:.1f} kg

MACROS DIARIOS ORIENTATIVOS (ya calculados; respétalos como referencia y explícalos al usuario al inicio):
- Calorías aproximadas: {m["kcal_aprox"]:.0f} kcal/día
- Proteínas: {m["proteinas_g"]:.0f} g/día
- Carbohidratos: {m["carbohidratos_g"]:.0f} g/día
- Grasas: {m["grasas_g"]:.0f} g/día

Instrucciones del plan:
1. Presenta primero un bloque titulado OBJETIVOS DIARIOS DE MACRONUTRIENTES con los valores anteriores (orientativos).
2. Genera un plan de comidas para VARIOS DÍAS (mínimo 3 días de ejemplo, máximo 7) adaptado al deporte y horarios típicos. Incluye siempre:
   - Comida PRE-ENTRENO (con timing orientativo: 1–3 h antes)
   - Comida POST-ENTRENO o recuperación (en las primeras 1–2 h después)
3. Prioriza alimentos ricos en proteína y combina con carbohidratos según el objetivo.
4. Incluye productos del supermercado ricos en proteína cuando encaje: yogur griego, huevos, pollo, atún, queso fresco batido o requesón, legumbres en conserva, etc. No menciones suplementos salvo que el usuario lo haya pedido explícitamente (objetivo o mensaje). Si pide suplementos, cada uno debe llevar Cuándo, Dosis, línea «• 🛒 Comprar →» con URL Amazon que incluya &tag=zia-nutricion-21, y al final del bloque la línea «• 💊 MyProtein → https://www.myprotein.es/?affil=zia» (como en el system).
5. Respeta restricciones del perfil familiar. Recetas o ideas rápidas (menos de 20 min cuando sea posible).

Al final, una lista corta COMPRA PROTEICA SUGERIDA con precios orientativos en euros si el perfil indica presupuesto o supermercado."""


def guardar_perfil_deporte_memoria(
    memoria: dict[str, Any],
    deporte: str,
    frecuencia: str,
    objetivo_texto: str,
    objetivo_clave: str,
    peso_kg: float,
    macros: dict[str, float],
) -> None:
    memoria["perfil_deporte"] = {
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        "deporte": deporte.strip(),
        "frecuencia_entrenos": frecuencia.strip(),
        "objetivo_texto": objetivo_texto.strip(),
        "objetivo_clave": objetivo_clave,
        "peso_kg_usado": peso_kg,
        "macros_diarios_aprox": {k: round(v, 1) for k, v in macros.items()},
    }
    guardar_memoria(memoria)


def generar_respuesta_nutricion_deporte(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    deporte: str,
    frecuencia: str,
    objetivo_respuesta: str,
    peso_kg: float,
    macros: dict[str, float],
    peso_inferido: bool,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {
            "role": "user",
            "content": mensaje_nutricion_deporte(
                perfil,
                memoria,
                deporte,
                frecuencia,
                objetivo_respuesta,
                peso_kg,
                macros,
                peso_inferido,
            ),
        },
    ]
    return completar(client, messages, temperature=0.65, max_tokens=6144)


MODOS_DIETA: dict[str, dict[str, str]] = {
    "keto": {
        "nombre": "Dieta cetogénica (keto)",
        "reglas": (
            "Muy baja en carbohidratos (típicamente 20–50 g netos/día según contexto), grasas como principal energía, "
            "proteína moderada según peso. Evita cereales, legumbres en exceso, azúcares, fruta alta en azúcar; "
            "prioriza carnes, pescados, huevos, aceites buenos, aguacate, verduras bajas en carbohidrato, frutos secos."
        ),
    },
    "mediterranea": {
        "nombre": "Dieta mediterránea",
        "reglas": (
            "Aceite de oliva virgen extra como grasa principal, verdura y fruta abundantes, legumbres, cereales integrales, "
            "frutos secos, pescado varias veces por semana, carne menos frecuente; lácteos fermentados; hierbas y especias. "
            "Limita carnes procesadas, bollería y ultraprocesados."
        ),
    },
    "ayuno_16_8": {
        "nombre": "Ayuno intermitente 16:8",
        "reglas": (
            "Ventana de alimentación de 8 horas y ayuno de 16 horas (ej.: comer de 12:00 a 20:00; ajusta a la vida real). "
            "Distribuye el mismo día calórico y nutrientes dentro de esa ventana; hidratación fuera del ayuno con agua, infusiones sin azúcar."
        ),
    },
    "vegetariana": {
        "nombre": "Dieta vegetariana",
        "reglas": (
            "Sin carne ni pescado; sí incluye huevos y lácteos si encajan con el usuario. Proteínas: huevos, lácteos, legumbres, "
            "tofu, tempeh, frutos secos, semillas, cereales integrales; combina proteínas vegetales para aminoácidos esenciales."
        ),
    },
    "vegana": {
        "nombre": "Dieta vegana",
        "reglas": (
            "Sin ningún producto de origen animal (ni carne, pescado, huevos, lácteos, miel). Proteínas: legumbres, tofu, tempeh, "
            "seitan, bebidas vegetales enriquecidas, frutos secos, semillas; vigila B12 y omega-3 (alimentos enriquecidos o suplementación orientativa)."
        ),
    },
    "volumen": {
        "nombre": "Dieta de volumen (bulking)",
        "reglas": (
            "Superávit calórico moderado, proteína alta (aprox. 1,6–2,2 g/kg/día según contexto), carbohidratos suficientes para entrenar, "
            "grasas saludables. Comidas frecuentes si encaja; prioriza alimentos densos en nutrientes y minimiza ultraprocesados vacíos."
        ),
    },
    "definicion": {
        "nombre": "Dieta de definición (cutting)",
        "reglas": (
            "Déficit calórico controlado, proteína alta para preservar músculo, grasas y carbohidratos ajustados según saciedad y energía. "
            "Prioriza volumen de verdura, proteínas magras, y evita calorías líquidas vacías."
        ),
    },
}


def detectar_modo_dieta(texto: str) -> str | None:
    tn = texto_sin_acentos(texto.lower())
    if "vegetariana" in tn:
        return "vegetariana"
    if "vegana" in tn:
        return "vegana"
    if "mediterranea" in tn:
        return "mediterranea"
    if "definicion" in tn:
        return "definicion"
    if re.search(r"\bketo\b", tn) or "cetogenica" in tn:
        return "keto"
    if "ayuno" in tn:
        return "ayuno_16_8"
    if re.search(r"\bvolumen\b", tn):
        return "volumen"
    return None


def guardar_preferencia_dieta(memoria: dict[str, Any], modo_clave: str) -> None:
    info = MODOS_DIETA[modo_clave]
    memoria["preferencia_dieta"] = {
        "modo": modo_clave,
        "nombre": info["nombre"],
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
    }
    guardar_memoria(memoria)


def mensaje_plan_dieta_modo(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    modo_clave: str,
) -> str:
    extra = contexto_memoria_para_prompt(memoria)
    info = MODOS_DIETA[modo_clave]
    return f"""MODO DIETA ESPECÍFICA: {info["nombre"]}

Perfil familiar:
{perfil_a_texto(perfil)}
{extra}

REGLAS DE ESTE MODO (aplícalas con rigor en todas las comidas):
{info["reglas"]}

TAREA — Genera una respuesta completa en este orden:

1) CÓMO FUNCIONA ESTA DIETA
   Breve explicación en español (4–8 frases) para una familia: principios, qué se prioriza y qué se limita, sin alarmismo.

2) PLAN DE 7 DÍAS COMPLETO
   Obligatorio: LUNES, MARTES, MIÉRCOLES, JUEVES, VIERNES, SÁBADO y DOMINGO en ese orden, cada día con título claro.
   Para cada día incluye desayuno, comida y cena (o el esquema que encaje con el perfil) respetando ESTRICTAMENTE el modo de dieta indicado.
   Si el modo es ayuno 16:8, indica la ventana horaria de comida y ajusta todas las ingestas a esa ventana.
   Recetas o ideas prácticas; cuando sea posible, preparación en menos de 30 minutos.

3) LISTA DE LA COMPRA ADAPTADA A ESTA DIETA
   - Sección FRESCO: productos frescos acordes al modo (precios orientativos en euros, 2 decimales).
   - Sección PREPARADO: productos de supermercado adecuados al modo (precios orientativos en euros).
   - TOTAL ESTIMADO en euros.
   Ningún alimento incompatible con el modo de dieta en la lista.

Si el perfil tiene restricciones (alergias, etc.), respétalas incluso si implica ajustar el típico patrón del modo (indícalo en una frase)."""


def generar_plan_dieta_especial(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    modo_clave: str,
) -> tuple[str, list[dict[str, Any]]]:
    guardar_preferencia_dieta(memoria, modo_clave)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {"role": "user", "content": mensaje_plan_dieta_modo(perfil, memoria, modo_clave)},
    ]
    texto = completar(client, messages, temperature=0.65, max_tokens=8192)
    return texto, messages


def mime_tipo_imagen(sufijo: str) -> str:
    s = sufijo.lower()
    if s in (".jpg", ".jpeg"):
        return "image/jpeg"
    if s == ".png":
        return "image/png"
    return "image/jpeg"


def extraer_ruta_imagen_desde_texto(texto: str) -> str | None:
    t = texto.strip()
    for sep in ('"', "'"):
        if len(t) >= 2 and t[0] == sep and t[-1] == sep:
            t = t[1:-1]
            break
    candidatos = [t]
    candidatos.extend(re.split(r"\s+", texto.strip()))
    for cand in candidatos:
        c = cand.strip().strip('"').strip("'")
        if not c:
            continue
        p = Path(c).expanduser()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return str(p)
    return None


def dispara_reconocimiento_foto(texto: str) -> bool:
    if extraer_ruta_imagen_desde_texto(texto):
        return True
    tn = texto.lower()
    if re.search(r"\bfoto\b", tn):
        return True
    if re.search(r"\banaliza\b", tn):
        return True
    return False


def es_foto_nevera(texto: str) -> bool:
    """Modo nevera/frigo: requiere «foto» o «analiza» para no chocar con el modo nevera inteligente (solo «nevera»)."""
    tn = texto_sin_acentos(texto.lower())
    if not (re.search(r"\bfoto\b", tn) or re.search(r"\banaliza\b", tn)):
        return False
    return any(
        k in tn
        for k in (
            "nevera",
            "frigo",
            "refrigerador",
            "frigorifico",
            "congelador",
        )
    )


def personas_desde_perfil(perfil: dict[str, str]) -> int:
    raw = str(perfil.get("num_personas", "2"))
    m = re.search(r"(\d+)", raw)
    if m:
        n = int(m.group(1))
        return max(1, min(n, 20))
    return 2


def texto_prompt_vision_plato(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    n = personas_desde_perfil(perfil)
    extra = contexto_memoria_para_prompt(memoria)
    enlace_aviso = "" if ACUERDO_COMERCIAL_ENLACES else " NO enlaces ni URLs de supermercados."
    return (
        prefijo_instruccion_vision_usuario()
        + f"""Identifica el plato en la imagen. Responde en ESPAÑOL con secciones en MAYÚSCULAS:

PLATO: nombre claro del plato
RECETA: ingredientes con pesos exactos (g o ml) para {n} persona(s) según el perfil; pasos breves.
LISTA DE LA COMPRA: • producto (cantidad) → XX.XX€ por línea; línea final TOTAL ESTIMADO: XX.XX€
Precios orientativos en euros (España).{enlace_aviso}
Al final, una línea breve con UNA pregunta natural (por ejemplo si quiere variar algo o qué suele comprar).

Perfil familiar:
{perfil_a_texto(perfil)}
{extra if extra else ""}
NO menciones suplementos."""
    )


def texto_prompt_vision_nevera(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    n = personas_desde_perfil(perfil)
    extra = contexto_memoria_para_prompt(memoria)
    return (
        prefijo_instruccion_vision_usuario()
        + f"""Identify all food products visible in this image and suggest 3 quick recipes under 20 minutes using these ingredients

Responde en ESPAÑOL. Estructura:
1) PRODUCTOS VISIBLES: enumera lo que reconoces en la imagen.
2) TRES RECETAS RÁPIDAS: exactamente 3 ideas distintas, menos de 20 minutos cada una, usando prioridad los ingredientes visibles; indica porciones para {n} persona(s).
3) CESTA COMPLEMENTARIA: solo ingredientes clave que falten para completar esas recetas, con cantidades a comprar, precio estimado en euros por ítem (supermercado del perfil si consta) y TOTAL ESTIMADO.

Perfil familiar:
{perfil_a_texto(perfil)}
{extra if extra else ""}
NO menciones suplementos."""
    )


def analizar_imagen_receta_vision(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    image_path: str,
    modo: str = "plato",
) -> str:
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    image_path = str(path.resolve())
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    mime = mime_tipo_imagen(path.suffix)
    data_url = f"data:{mime};base64,{image_data}"

    if modo == "nevera":
        texto_usuario = texto_prompt_vision_nevera(perfil, memoria)
    else:
        texto_usuario = texto_prompt_vision_plato(perfil, memoria)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_para_vision()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": texto_usuario},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    respuesta = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.65,
        max_tokens=4096,
    )
    content = respuesta.choices[0].message.content
    return content or ""


def texto_prompt_vision_nevera_solo_productos(perfil: dict[str, str], memoria: dict[str, Any]) -> str:
    _ = perfil, memoria
    return (
        prefijo_instruccion_vision_usuario()
        + """Analiza la imagen (nevera, frigorífico o despensa). Lista los alimentos que reconozcas.
Responde en ESPAÑOL con este formato:
PRODUCTOS VISIBLES:
• producto (cantidad aproximada si se ve)
(una línea con • por producto)
No incluyas recetas ni precios en este paso. No digas que no puedes ver la imagen."""
    )


def analizar_imagen_nevera_solo_productos(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    image_path: str,
) -> str:
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo: {path}")
    image_path = str(path.resolve())
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")
    mime = mime_tipo_imagen(path.suffix)
    data_url = f"data:{mime};base64,{image_data}"
    texto_usuario = texto_prompt_vision_nevera_solo_productos(perfil, memoria)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_para_vision()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": texto_usuario},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    respuesta = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.65,
        max_tokens=2048,
    )
    content = respuesta.choices[0].message.content
    return content or ""


def mensaje_receta_lista_desde_nevera_foto(
    perfil: dict[str, str],
    memoria: dict[str, Any],
    ingredientes_reconocidos: str,
    respuesta_usuario: str,
) -> str:
    extra = contexto_memoria_para_prompt(memoria)
    enlace_aviso = "" if ACUERDO_COMERCIAL_ENLACES else "\nNO incluyas URLs ni enlaces a supermercados."
    return f"""FOTO NEVERA — Productos detectados en la imagen:
{ingredientes_reconocidos.strip()}

El usuario responde a qué le apetece / si tiene poco tiempo o más calma para cocinar (o nombra un plato):
{respuesta_usuario.strip()}

Perfil familiar:
{perfil_a_texto(perfil)}
{extra}

Genera en ESPAÑOL:
1) RECETA: propón un plato coherente con lo anterior (rápido si pidió rapidez; más elaborado si dijo que tiene tiempo). Usa en lo posible lo que ya tiene; indica qué falta comprar.
2) LISTA DE COMPRA: solo lo que falta, con • producto (cantidad) → precio en €, y TOTAL ESTIMADO: X.XX€
Cierra con UNA pregunta breve y natural.
{enlace_aviso}
NO suplementos."""


def generar_receta_lista_nevera_foto(
    client: OpenAI,
    perfil: dict[str, str],
    memoria: dict[str, Any],
    ingredientes_reconocidos: str,
    respuesta_usuario: str,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_zia_completo()},
        {
            "role": "user",
            "content": mensaje_receta_lista_desde_nevera_foto(
                perfil, memoria, ingredientes_reconocidos, respuesta_usuario
            ),
        },
    ]
    return completar(client, messages, temperature=0.65, max_tokens=4096)


def clasificar_nevera_o_plato_desde_texto(texto: str) -> str | None:
    """Devuelve 'nevera', 'plato' o None si no queda claro."""
    tl = texto_sin_acentos(texto.lower().strip())
    if tl in ("1", "1️⃣"):
        return "nevera"
    if tl in ("2", "2️⃣"):
        return "plato"
    if "nevera" in tl or "frigo" in tl or "refrigerador" in tl or "congelador" in tl:
        return "nevera"
    if "plato" in tl or tl in ("comida", "un plato"):
        return "plato"
    return None


def main() -> None:
    if API_KEY == "PON_CLAVE_AQUI" or not API_KEY.strip():
        print(
            "ZIA: Edita API_KEY en main.py y pon tu clave de OpenAI antes de continuar.\n"
        )
        return

    memoria = cargar_memoria()
    client = crear_cliente()

    if MEMORIA_PATH.is_file() and perfil_tiene_datos(memoria.get("perfil", {})):
        if ofrecer_perfil_guardado(memoria) == "nuevo":
            reset_memoria_tras_nuevo(memoria)
            perfil = ejecutar_onboarding()
            memoria["perfil"] = perfil
            guardar_memoria(memoria)
        else:
            perfil = {k: str(v) for k, v in memoria["perfil"].items()}
    else:
        perfil = ejecutar_onboarding()
        memoria["perfil"] = perfil
        guardar_memoria(memoria)

    print("\nZIA: Perfecto. ¿Qué quieres hacer hoy?")
    print("1️⃣  Quiero mi plan de dieta personalizado")
    print("2️⃣  Solo necesito la lista de la compra familiar\n")

    while True:
        opcion_inicio = input("Tú: ").strip()
        if opcion_inicio in ("1", "1️⃣", "dieta", "plan", "quiero mi plan"):
            modo_inicio = "dieta"
            break
        elif opcion_inicio in ("2", "2️⃣", "lista", "compra", "lista de la compra"):
            modo_inicio = "lista"
            break
        else:
            print("ZIA: Escribe 1 para el plan de dieta o 2 para la lista de la compra.\n")

    if modo_inicio == "lista":
        print("\nZIA: Generando tu lista de la compra familiar…\n")
        plan_ref = (memoria.get("ultimo_plan") or "").strip()
        if not plan_ref:
            plan_ref = mensaje_plan_semanal(perfil, memoria)
        print_confirmacion_super_y_presupuesto_antes_lista(perfil)
        lista_txt = generar_lista_compra_respuesta(client, perfil, plan_ref)
        print(lista_txt)
        memoria["lista_compra_actual"] = lista_txt
        guardar_memoria(memoria)
        aprobacion_lista_ctx = encolar_aprobacion_post_lista(lista_txt, perfil, omitir_vista_carrito=True)
    else:
        print("\nZIA: Perfecto. Generando tu plan semanal de 7 días…\n")

    if modo_inicio == "dieta":
        plan_menu, messages = generar_plan_semanal_respuesta(client, perfil, memoria, None)
        print(plan_menu)
        print()
        memoria["plan_semanal_actual"] = plan_menu
        memoria["ultimo_plan"] = plan_menu
        añadir_lista_al_historial(memoria, plan_menu)
        guardar_memoria(memoria)
        historial: list[dict[str, Any]] = messages + [
            {"role": "assistant", "content": plan_menu},
        ]
        print("ZIA: ¿Quieres que prepare tu lista de la compra? Escribe sí o no\n")
    else:
        historial: list[dict[str, Any]] = [
            {"role": "system", "content": system_zia_completo()},
        ]

    nevera_esperando_ingredientes = False
    foto_esperando_ruta = False
    foto_modo_nevera = False
    seguimiento_estado = 0
    seguimiento_como_fue = ""
    deporte_estado = 0
    deporte_deporte = ""
    deporte_frecuencia = ""
    esperando_si_lista = True
    foto_esperando_tipo_nevera_o_plato = False
    nevera_foto_esperando_plato = False
    nevera_foto_ingredientes_texto = ""
    carrito_fase: str | None = None
    aprobacion_lista_ctx: dict[str, Any] | None = None
    falta_ing_ctx: dict[str, Any] | None = None
    esperando_mini_lista_faltantes = False
    reserva_restaurante_estado: str | None = None
    reserva_restaurante_ctx: dict[str, Any] = {}

    while True:
        texto = input("Tú: ").strip()
        if not texto:
            continue
        if texto.lower() == "salir":
            print("ZIA: ¡Hasta pronto!")
            break

        if nevera_foto_esperando_plato:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                nevera_foto_esperando_plato = False
                nevera_foto_ingredientes_texto = ""
                print("ZIA: Vale.\n")
                continue
            if len(texto.strip()) < 2:
                print(
                    "ZIA: ¿Te apetece algo rápido o tienes más tiempo? ¿O ya se te ocurre un plato concreto?\n"
                )
                continue
            print("\nZIA: Dame un segundo que monto receta y lista…\n")
            try:
                resp_cocina = generar_receta_lista_nevera_foto(
                    client,
                    perfil,
                    memoria,
                    nevera_foto_ingredientes_texto,
                    texto.strip(),
                )
                print(resp_cocina.rstrip())
                print()
                historial.extend(
                    [
                        {
                            "role": "user",
                            "content": f"[Foto nevera → plato elegido] {texto.strip()}",
                        },
                        {"role": "assistant", "content": resp_cocina},
                    ]
                )
                memoria["lista_compra_actual"] = resp_cocina
                guardar_memoria(memoria)
                if total_lista_a_float(resp_cocina) is not None:
                    aprobacion_lista_ctx = encolar_aprobacion_post_lista(
                        resp_cocina, perfil, omitir_vista_carrito=True, origen="nevera"
                    )
            except Exception as e:
                print(f"ZIA: No pude generar la receta: {e}\n")
            nevera_foto_esperando_plato = False
            nevera_foto_ingredientes_texto = ""
            continue

        if falta_ing_ctx is not None:
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                falta_ing_ctx = None
                print("ZIA: Vale.\n")
                continue
            ctx_f = falta_ing_ctx
            if es_opcion_falta_sustituir(texto):
                falta_ing_ctx = None
                try:
                    sust = generar_sustituto_falta_ingrediente(
                        client,
                        perfil,
                        memoria,
                        str(ctx_f.get("ingredient", "")),
                        str(ctx_f.get("meal", "")),
                    )
                    print(sust.rstrip())
                    print()
                    historial.extend(
                        [
                            {"role": "user", "content": "[Falta ingrediente → sustituir]"},
                            {"role": "assistant", "content": sust},
                        ]
                    )
                except Exception as e:
                    print(f"ZIA: No pude sugerir la alternativa: {e}\n")
                continue
            if es_opcion_falta_comprar(texto):
                falta_ing_ctx = None
                ing = str(ctx_f.get("ingredient", "producto"))
                qty = str(ctx_f.get("quantity", "1 ud"))
                pr = str(ctx_f.get("price", "0"))
                ps = formatear_precio_euros(pr)
                linea = f"{ing} ({qty}) → {ps}€"
                memoria.setdefault("mini_lista_faltantes", []).append(
                    {
                        "ingredient": ing,
                        "quantity": qty,
                        "price": ps,
                        "line": linea,
                    }
                )
                guardar_memoria(memoria)
                esperando_mini_lista_faltantes = True
                bloque_pedido = texto_mini_pedido_faltantes(memoria, linea)
                print("\n" + bloque_pedido + "\n")
                historial.extend(
                    [
                        {"role": "user", "content": "[Falta ingrediente → comprar]"},
                        {"role": "assistant", "content": bloque_pedido},
                    ]
                )
                continue
            print("\nZIA: Elige 1 (sustituir) o 2 (comprar), o escribe cancelar.\n")
            continue

        if esperando_mini_lista_faltantes:
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                memoria["mini_lista_faltantes"] = []
                guardar_memoria(memoria)
                esperando_mini_lista_faltantes = False
                print("ZIA: Vale, vacío la mini lista.\n")
                continue
            if intencion_no_mas_ingredientes_mini(texto):
                items_m = memoria.get("mini_lista_faltantes") or []
                if items_m:
                    ult = items_m[-1]
                    ult_line = (
                        str(ult.get("line", ""))
                        if isinstance(ult, dict)
                        else str(ult)
                    )
                    resumen_pedido = texto_mini_pedido_faltantes(memoria, ult_line)
                    memoria["lista_compra_actual"] = resumen_pedido
                    plan_base_m = (memoria.get("plan_semanal_actual") or "").strip()
                    memoria["ultimo_plan"] = (
                        (plan_base_m + "\n\n" + resumen_pedido).strip()
                        if plan_base_m
                        else resumen_pedido
                    )
                    añadir_lista_al_historial(memoria, memoria["ultimo_plan"])
                memoria["mini_lista_faltantes"] = []
                guardar_memoria(memoria)
                esperando_mini_lista_faltantes = False
                cierre = (
                    "¡Perfecto! Tu pedido está listo. "
                    "¿Necesitas algo más para esta semana?"
                )
                print(f"\n{cierre}\n")
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": cierre},
                    ]
                )
                continue
            if detectar_falta_ingrediente(texto):
                try:
                    ctx_n = ejecutar_inferencia_y_ctx_falta_ingrediente(
                        client, perfil, memoria, texto
                    )
                    falta_ing_ctx = ctx_n
                    assistant_txt = texto_bloque_falta_ingrediente_exacto(
                        ctx_n["ingredient"],
                        ctx_n["meal"],
                        ctx_n["quantity"],
                        ctx_n["price"],
                    )
                    historial.extend(
                        [
                            {"role": "user", "content": texto},
                            {"role": "assistant", "content": assistant_txt},
                        ]
                    )
                except Exception as e:
                    print(f"ZIA: No pude leer el faltante: {e}\n")
                continue
            print(
                "\nZIA: Si ya no falta nada, di «lo dejamos así» o «nada más». "
                "Si te falta otro producto, descríbelo.\n"
            )
            continue

        if reserva_restaurante_estado == "elegiendo_restaurante":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale, dejamos la reserva para otro momento.\n")
                continue
            ctx_e = reserva_restaurante_ctx
            bloque_sug = str(ctx_e.get("sugerencias_bloque") or "")
            try:
                es_corr, nueva_c = extraer_correccion_cocina_reserva(
                    client,
                    texto,
                    str(ctx_e.get("preferencia_cocina", "") or ""),
                )
            except Exception:
                es_corr, nueva_c = False, ""
            if es_corr:
                pref_fin = (nueva_c or ctx_e.get("preferencia_cocina") or "").strip()
                if not pref_fin:
                    print(
                        "\nZIA: Dime qué cocina buscas (por ejemplo paella, sushi…) "
                        "y te paso opciones adecuadas.\n"
                    )
                    continue
                ctx_e["preferencia_cocina"] = pref_fin
                reserva_restaurante_ctx = ctx_e
                try:
                    bloque_corr = generar_tres_sugerencias_restaurantes(
                        client,
                        perfil,
                        ctx_e,
                        disculpa_correccion=(
                            "Perdona, antes no acerté con la cocina. "
                            "Aquí van solo opciones que encajan con lo que buscas:"
                        ),
                    )
                    ctx_e["sugerencias_bloque"] = bloque_corr
                    reserva_restaurante_ctx = ctx_e
                    print("\n" + bloque_corr.rstrip() + "\n")
                    historial.extend(
                        [
                            {"role": "user", "content": texto},
                            {"role": "assistant", "content": bloque_corr},
                        ]
                    )
                except Exception as e:
                    print(f"ZIA: No pude corregir las sugerencias: {e}\n")
                continue
            try:
                nombre_e, mas = interpretar_eleccion_restaurante(
                    client, texto, bloque_sug
                )
            except Exception as e:
                print(f"ZIA: No pude leer tu elección: {e}\n")
                continue
            if mas:
                try:
                    bloque_nuevo = generar_tres_sugerencias_restaurantes(
                        client, perfil, ctx_e
                    )
                    ctx_e["sugerencias_bloque"] = bloque_nuevo
                    reserva_restaurante_ctx = ctx_e
                    print("\n" + bloque_nuevo.rstrip() + "\n")
                    historial.extend(
                        [
                            {"role": "user", "content": texto},
                            {"role": "assistant", "content": bloque_nuevo},
                        ]
                    )
                except Exception as e:
                    print(f"ZIA: No pude buscar más opciones: {e}\n")
                continue
            if not nombre_e:
                msg = (
                    "ZIA: Dime cuál de las opciones prefieres (el nombre del restaurante) "
                    "o si quieres que busque más opciones.\n"
                )
                print("\n" + msg)
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": msg.replace("ZIA: ", "").strip()},
                    ]
                )
                continue
            ctx_e["restaurante_elegido"] = nombre_e
            reserva_restaurante_ctx = ctx_e
            contact_q = (
                "ZIA: ¿A nombre de quién hago la reserva y qué teléfono dejo?\n"
            )
            print("\n" + contact_q)
            historial.extend(
                [
                    {"role": "user", "content": texto},
                    {"role": "assistant", "content": contact_q.replace("ZIA: ", "").strip()},
                ]
            )
            reserva_restaurante_estado = "esperando_contacto"
            continue

        if reserva_restaurante_estado == "esperando_contacto":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale, dejamos la reserva para otro momento.\n")
                continue
            try:
                reserva_restaurante_ctx = fusionar_contacto_reserva_restaurante(
                    client, perfil, texto, reserva_restaurante_ctx
                )
            except Exception as e:
                print(f"ZIA: No pude anotar eso: {e}\n")
                continue
            ctx_c = reserva_restaurante_ctx
            nom = str(ctx_c.get("nombre_reserva", "") or "").strip()
            tel = str(ctx_c.get("telefono", "") or "").strip()
            if nom and tel:
                res_q = (
                    "ZIA: ¿Hay alguna alergia o preferencia que deba mencionar al reservar? "
                    "Si no, di «ninguna».\n"
                )
                print("\n" + res_q)
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": res_q.replace("ZIA: ", "").strip()},
                    ]
                )
                reserva_restaurante_estado = "recolectando_restricciones"
                continue
            print(
                "\nZIA: Necesito un nombre para la reserva y un teléfono de contacto.\n"
            )
            historial.extend(
                [
                    {"role": "user", "content": texto},
                    {
                        "role": "assistant",
                        "content": "Necesito un nombre para la reserva y un teléfono de contacto.",
                    },
                ]
            )
            continue

        if reserva_restaurante_estado == "recolectando_restricciones":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale, dejamos la reserva para otro momento.\n")
                continue
            try:
                reserva_restaurante_ctx = fusionar_restricciones_reserva_restaurante(
                    client, texto, reserva_restaurante_ctx
                )
            except Exception as e:
                print(f"ZIA: No pude anotar eso: {e}\n")
                continue
            ctx_rr = reserva_restaurante_ctx
            restr = str(ctx_rr.get("restricciones", "") or "").strip()
            if intencion_ninguna_restriccion(texto) and not restr:
                ctx_rr["restricciones"] = "Ninguna"
                restr = "Ninguna"
                reserva_restaurante_ctx = ctx_rr
            if restr:
                try:
                    bloque_res = generar_bloque_reserva_final_un_restaurante(
                        perfil, ctx_rr
                    )
                    print("\n" + bloque_res.rstrip() + "\n")
                    guardar_reserva_restaurante_en_memoria(memoria, ctx_rr, bloque_res)
                    historial.extend(
                        [
                            {"role": "user", "content": texto},
                            {"role": "assistant", "content": bloque_res},
                        ]
                    )
                    ctx_rr["mostrada_nudges"] = "0"
                    ctx_rr["recordatorio_una_hora"] = ""
                    reserva_restaurante_ctx = ctx_rr
                    reserva_restaurante_estado = "mostrada"
                except Exception as e:
                    print(f"ZIA: No pude cerrar la reserva: {e}\n")
                continue
            msg_r = (
                "ZIA: ¿Hay alguna alergia o preferencia? Si no, di «ninguna».\n"
            )
            print("\n" + msg_r)
            historial.extend(
                [
                    {"role": "user", "content": texto},
                    {"role": "assistant", "content": msg_r.replace("ZIA: ", "").strip()},
                ]
            )
            continue

        if reserva_restaurante_estado == "recolectando":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale, dejamos la reserva para otro momento.\n")
                continue
            try:
                reserva_restaurante_ctx = fusionar_reserva_restaurante_ctx(
                    client, perfil, texto, reserva_restaurante_ctx, etapa="cita"
                )
            except Exception as e:
                print(f"ZIA: No pude anotar eso: {e}\n")
                continue
            ctx_r, est_new, out_txt = procesar_reserva_tras_fusion_cita(
                client, perfil, reserva_restaurante_ctx
            )
            reserva_restaurante_ctx = ctx_r
            if est_new:
                reserva_restaurante_estado = est_new
            print("\n" + out_txt.rstrip() + "\n")
            a_hist = out_txt.replace("ZIA: ", "").strip()
            historial.extend(
                [
                    {"role": "user", "content": texto},
                    {"role": "assistant", "content": a_hist},
                ]
            )
            continue

        if reserva_restaurante_estado == "recordatorio":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale.\n")
                continue
            if intencion_afirmativo_recordatorio_reserva(texto):
                guardar_recordatorio_reserva_restaurante(memoria, reserva_restaurante_ctx)
                h = 1 if str(reserva_restaurante_ctx.get("recordatorio_una_hora", "") or "").strip() == "1" else 2
                msg_ok = (
                    f"He guardado el aviso: te recordaré la reserva {h} hora{'s' if h != 1 else ''} antes. "
                    "¿Necesitas algo más?"
                )
                print(f"\nZIA: {msg_ok}\n")
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": msg_ok},
                    ]
                )
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                continue
            if intencion_negativo_recordatorio_reserva(texto):
                msg_no = "Sin problema, no programo aviso. ¿Algo más?"
                print(f"\nZIA: {msg_no}\n")
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": msg_no},
                    ]
                )
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                continue
            h_pre = 1 if str(reserva_restaurante_ctx.get("recordatorio_una_hora", "") or "").strip() == "1" else 2
            print(
                f"\nZIA: Dime sí o no si quieres el recordatorio {h_pre} hora{'s' if h_pre != 1 else ''} antes.\n"
            )
            continue

        if reserva_restaurante_estado == "mostrada":
            tl = texto.strip().lower()
            if tl in ("cancelar", "cancela"):
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                print("ZIA: Vale.\n")
                continue
            if intencion_usuario_quiere_que_zia_guie_thefork(texto):
                bloque_zia = bloque_reserva_enviada_por_zia(perfil, reserva_restaurante_ctx)
                print("\n" + bloque_zia.rstrip() + "\n")
                guardar_reserva_zia_enviada_y_recordatorio(
                    memoria, reserva_restaurante_ctx, bloque_zia
                )
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": bloque_zia},
                    ]
                )
                reserva_restaurante_estado = None
                reserva_restaurante_ctx = {}
                continue
            if intencion_reserva_usuario_llama(texto):
                bloque_acc = texto_bloque_reservar_ahora_y_recordatorio(memoria, perfil)
                print("\n" + bloque_acc + "\n")
                reserva_restaurante_ctx["recordatorio_una_hora"] = ""
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": bloque_acc},
                    ]
                )
                reserva_restaurante_estado = "recordatorio"
                continue
            n_raw = str(reserva_restaurante_ctx.get("mostrada_nudges", "") or "0").strip()
            try:
                n_int = int(n_raw) if n_raw else 0
            except ValueError:
                n_int = 0
            if n_int == 0:
                reserva_restaurante_ctx["mostrada_nudges"] = "1"
                clar = (
                    "ZIA: Si quieres que yo gestione la reserva, di «hazlo tú» o «que la hagas tú». "
                    "Si prefieres llamar tú al restaurante, di «llamo yo».\n"
                )
                print("\n" + clar)
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": clar.replace("ZIA: ", "").strip()},
                    ]
                )
                continue
            print("\nZIA: Cuando quieras. ¿Necesitas algo más?\n")
            reserva_restaurante_estado = None
            reserva_restaurante_ctx = {}
            continue

        if aprobacion_lista_ctx is not None:
            modo_ap = aprobacion_lista_ctx["modo"]
            lista_ap = aprobacion_lista_ctx["lista_txt"]
            pres_ap = aprobacion_lista_ctx["presupuesto"]
            tot_ap = aprobacion_lista_ctx["total"]
            nombre_t_ap = aprobacion_lista_ctx.get("nombre_tienda")

            if modo_ap == "confirmar":
                if es_respuesta_si_lista(texto):
                    registrar_evento_aprobacion_gastos(
                        memoria, "confirmada", tot_ap, pres_ap, "compra confirmada"
                    )
                    origen_ap = aprobacion_lista_ctx.get("origen", "semanal")
                    aprobacion_lista_ctx = None
                    if origen_ap == "tienda_carrito":
                        print("\nZIA: ¡Listo para comprar! 🛒\n")
                        print("ZIA: ¿Añadimos algo más o lo dejamos aquí?\n")
                    else:
                        print_pregunta_donde_comprar(perfil)
                        carrito_fase = "pregunta_donde_comprar"
                elif es_respuesta_no_lista(texto):
                    registrar_evento_aprobacion_gastos(
                        memoria, "rechazada", tot_ap, pres_ap, "usuario no confirmó la compra"
                    )
                    aprobacion_lista_ctx = None
                    print(
                        "\nZIA: Vale, no cierro esta compra. ¿Quieres que rehaga la lista o seguimos con otra cosa?\n"
                    )
                else:
                    print("\nZIA: Responde sí o no.\n")
                continue

            if modo_ap == "sobre_presupuesto":
                tl_ap = texto.strip().lower()
                if tl_ap in ("1", "1️⃣") or "aprobar" in tl_ap:
                    registrar_evento_aprobacion_gastos(
                        memoria,
                        "aprobada_sobregiro",
                        tot_ap,
                        pres_ap,
                        "aprobado gasto por encima del presupuesto",
                    )
                    origen_ap = aprobacion_lista_ctx.get("origen", "semanal")
                    imprimir_vista_carrito_tras_lista(
                        lista_ap, perfil, nombre_tienda=nombre_t_ap
                    )
                    aprobacion_lista_ctx = None
                    if origen_ap == "tienda_carrito":
                        print("\nZIA: ¡Listo para comprar! 🛒\n")
                        print("ZIA: ¿Añadimos algo más o lo dejamos aquí?\n")
                    else:
                        print_pregunta_donde_comprar(perfil)
                        carrito_fase = "pregunta_donde_comprar"
                elif tl_ap in ("2", "2️⃣") or "ajust" in tl_ap:
                    if pres_ap is None:
                        aprobacion_lista_ctx = None
                        print("ZIA: No tengo tu presupuesto en el perfil para ajustar.\n")
                        continue
                    print("\nZIA: Ajustando la lista a tu presupuesto…\n")
                    try:
                        lista_nueva = generar_lista_ajustada_presupuesto(
                            client, perfil, lista_ap, pres_ap
                        )
                        memoria["lista_compra_actual"] = lista_nueva
                        plan_base_ap = (memoria.get("plan_semanal_actual") or "").strip()
                        memoria["ultimo_plan"] = (
                            (plan_base_ap + "\n\n" + lista_nueva).strip()
                            if plan_base_ap
                            else lista_nueva
                        )
                        guardar_memoria(memoria)
                        tot_n = total_lista_a_float(lista_nueva)
                        registrar_evento_aprobacion_gastos(
                            memoria,
                            "lista_ajustada_presupuesto",
                            tot_n,
                            pres_ap,
                            "lista ajustada al presupuesto",
                        )
                        imprimir_vista_carrito_tras_lista(
                            lista_nueva, perfil, nombre_tienda=nombre_t_ap
                        )
                        print("ZIA: ¿Confirmas esta compra? (sí/no)\n")
                        aprobacion_lista_ctx = {
                            "modo": "confirmar",
                            "lista_txt": lista_nueva,
                            "total": tot_n,
                            "presupuesto": pres_ap,
                            "nombre_tienda": nombre_t_ap,
                            "origen": aprobacion_lista_ctx.get("origen", "semanal"),
                        }
                    except Exception as e:
                        print(f"ZIA: No pude ajustar la lista: {e}\n")
                        aprobacion_lista_ctx = None
                else:
                    print(
                        "\nZIA: Responde 1 para aprobar el gasto extra o 2 para que ajuste la lista.\n"
                    )
                continue

        if carrito_fase == "pregunta_donde_comprar" and aprobacion_lista_ctx is None:
            tn = texto_sin_acentos(texto.lower())
            if respuesta_es_demasiado_ambigua(texto):
                print(
                    "\nZIA: Di «comparar precios», «cercanía», «entrega», o el nombre de una cadena, "
                    "o «me quedo en mi super».\n"
                )
                continue
            if intencion_comparar_cercania(texto):
                print("\nZIA: " + texto_orientacion_cercania_supermercados(perfil).strip() + "\n")
                historial.extend(
                    [
                        {"role": "user", "content": f"[Carrito: cercanía] {texto}"},
                        {
                            "role": "assistant",
                            "content": texto_orientacion_cercania_supermercados(perfil).strip(),
                        },
                    ]
                )
                continue
            if intencion_comparar_entrega_online(texto):
                msg_e = texto_orientacion_entrega_supermercados().strip()
                print(f"\nZIA: {msg_e}\n")
                historial.extend(
                    [
                        {"role": "user", "content": f"[Carrito: entrega] {texto}"},
                        {"role": "assistant", "content": msg_e},
                    ]
                )
                continue
            if intencion_comparar_otras_tiendas(texto):
                print("\nZIA: Vale, miro cuánto saldría la misma cesta en varias tiendas…\n")
                tot_txt = generar_totales_comparativa(client, memoria)
                print(tot_txt)
                print()
                print(
                    "ZIA: Dime en qué cadena quieres ver el carrito detallado con precios "
                    "(por ejemplo Mercadona, Lidl o Aldi).\n"
                )
                carrito_fase = "elegir_cadena_tras_comparativa"
                historial.extend(
                    [
                        {"role": "user", "content": f"[Carrito: comparar] {texto}"},
                        {"role": "assistant", "content": tot_txt},
                    ]
                )
                continue
            if intencion_quedarse_super_habitual(texto):
                print("\nZIA: ¡Perfecto! 🛒 ¿Necesitas algo más para esta semana?\n")
                carrito_fase = None
                continue
            cid_direct = detectar_id_supermercado_en_texto(texto)
            if cid_direct and len(tn.split()) <= 10:
                nombre_c = SUPER_TIENDA_URL[cid_direct][0]
                cid_habitual = cid_supermercado_habitual_perfil(perfil)
                if cid_direct == cid_habitual:
                    bloque_c = imprimir_pedido_listo_tienda_habitual_sin_repetir_lista(
                        perfil, memoria, cid_direct
                    )
                    historial.extend(
                        [
                            {"role": "user", "content": texto},
                            {"role": "assistant", "content": bloque_c},
                        ]
                    )
                    carrito_fase = None
                    continue
                print(f"\nZIA: Te lo preparo en {nombre_c}…\n")
                lista_t = generar_lista_para_tienda(client, memoria, cid_direct)
                plan_base = (memoria.get("plan_semanal_actual") or "").strip()
                memoria["ultimo_plan"] = (plan_base + "\n\n" + lista_t).strip() if plan_base else lista_t
                memoria["lista_compra_actual"] = lista_t
                guardar_memoria(memoria)
                historial.extend(
                    [
                        {"role": "user", "content": f"[Carrito: tienda {nombre_c}] {texto}"},
                        {"role": "assistant", "content": lista_t},
                    ]
                )
                carrito_fase = None
                aprobacion_lista_ctx = encolar_aprobacion_post_lista(
                    lista_t, perfil, nombre_c, origen="tienda_carrito"
                )
                continue
            print(
                "\nZIA: No lo tengo claro. Puedes decir «comparar precios», «cercanía», «entrega», "
                "una cadena concreta, o «me quedo en mi super».\n"
            )
            continue

        if carrito_fase == "elegir_cadena_tras_comparativa" and aprobacion_lista_ctx is None:
            cid_pick = detectar_id_supermercado_en_texto(texto)
            if not cid_pick:
                print("\nZIA: Dime el nombre de una cadena (Mercadona, Lidl, Aldi, Carrefour…).\n")
                continue
            nombre_c = SUPER_TIENDA_URL[cid_pick][0]
            cid_habitual = cid_supermercado_habitual_perfil(perfil)
            if cid_pick == cid_habitual:
                bloque_p = imprimir_pedido_listo_tienda_habitual_sin_repetir_lista(
                    perfil, memoria, cid_pick
                )
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": bloque_p},
                    ]
                )
                carrito_fase = None
                continue
            print(f"\nZIA: Preparando tu carrito en {nombre_c}…\n")
            lista_t = generar_lista_para_tienda(client, memoria, cid_pick)
            plan_base = (memoria.get("plan_semanal_actual") or "").strip()
            memoria["ultimo_plan"] = (plan_base + "\n\n" + lista_t).strip() if plan_base else lista_t
            memoria["lista_compra_actual"] = lista_t
            guardar_memoria(memoria)
            historial.extend(
                [
                    {"role": "user", "content": f"[Carrito: detalle {nombre_c}] {texto}"},
                    {"role": "assistant", "content": lista_t},
                ]
            )
            carrito_fase = None
            aprobacion_lista_ctx = encolar_aprobacion_post_lista(
                lista_t, perfil, nombre_c, origen="tienda_carrito"
            )
            continue

        if esperando_si_lista and aprobacion_lista_ctx is None:
            if es_respuesta_si_lista(texto):
                plan_ref = (memoria.get("plan_semanal_actual") or memoria.get("ultimo_plan") or "").strip()
                print_confirmacion_super_y_presupuesto_antes_lista(perfil)
                print("ZIA: Preparando tu lista…\n")
                lista_txt = generar_lista_compra_respuesta(client, perfil, plan_ref)
                memoria["lista_compra_actual"] = lista_txt
                memoria["ultimo_plan"] = (plan_ref + "\n\n" + lista_txt).strip()
                guardar_memoria(memoria)
                añadir_lista_al_historial(memoria, memoria["ultimo_plan"])
                aprobacion_lista_ctx = encolar_aprobacion_post_lista(lista_txt, perfil)
                historial.extend(
                    [
                        {"role": "user", "content": "sí — lista de la compra"},
                        {"role": "assistant", "content": lista_txt},
                    ]
                )
                esperando_si_lista = False
            elif es_respuesta_no_lista(texto):
                print(
                    "\nZIA: De acuerdo. ¿Te tiro una idea para cenar esta semana o prefieres hablar de la compra?\n"
                )
                esperando_si_lista = False
            else:
                print("\nZIA: Escribe sí o no.\n")
            continue

        if foto_esperando_tipo_nevera_o_plato:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                foto_esperando_tipo_nevera_o_plato = False
                print("ZIA: Cancelado.\n")
                continue
            cual = clasificar_nevera_o_plato_desde_texto(texto)
            if cual is None:
                print("\nZIA: ¿Es de la nevera o de un plato?\n")
                continue
            foto_modo_nevera = cual == "nevera"
            foto_esperando_tipo_nevera_o_plato = False
            foto_esperando_ruta = True
            print(
                "\nZIA: Envíame la ruta completa del archivo de imagen "
                "(.jpg, .jpeg o .png), por ejemplo: /Users/tu/carpeta/foto.jpg\n"
            )
            continue

        if seguimiento_estado == 1:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                seguimiento_estado = 0
                seguimiento_como_fue = ""
                print("ZIA: Seguimiento cancelado.\n")
                continue
            if len(texto.strip()) < 2:
                print("ZIA: Cuéntame un poco más sobre cómo fue la semana.\n")
                continue
            seguimiento_como_fue = texto.strip()
            print(
                "\nZIA: ¿Hay platos o recetas que quieras REPETIR esta semana, y alguno que prefieras "
                "EVITAR o que no vuelva a salir en el menú? Si no hay preferencias, escribe «ninguno» o «nada».\n"
            )
            seguimiento_estado = 2
            continue

        if seguimiento_estado == 2:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                seguimiento_estado = 0
                seguimiento_como_fue = ""
                print("ZIA: Seguimiento cancelado.\n")
                continue
            repetir_evitar = texto.strip()
            bloque = bloque_prompt_seguimiento_semanal(seguimiento_como_fue, repetir_evitar)
            guardar_feedback_seguimiento(memoria, seguimiento_como_fue, repetir_evitar)
            print("\nZIA: Gracias por el feedback. Generando tu plan para la nueva semana…\n")
            plan_nuevo, msgs_plan = generar_plan_semanal_respuesta(client, perfil, memoria, bloque)
            print(plan_nuevo)
            print()
            memoria["plan_semanal_actual"] = plan_nuevo
            memoria["ultimo_plan"] = plan_nuevo
            añadir_lista_al_historial(memoria, plan_nuevo)
            guardar_memoria(memoria)
            resumen_usuario = (
                f"[Seguimiento semanal] Semana pasada: {seguimiento_como_fue[:200]}"
                + ("…" if len(seguimiento_como_fue) > 200 else "")
                + f" | Repetir/evitar: {repetir_evitar[:200]}"
                + ("…" if len(repetir_evitar) > 200 else "")
            )
            historial.extend(
                [
                    {"role": "user", "content": resumen_usuario},
                    {"role": "assistant", "content": plan_nuevo},
                ]
            )
            seguimiento_estado = 0
            seguimiento_como_fue = ""
            print("ZIA: ¿Quieres que prepare tu lista de la compra? Escribe sí o no\n")
            esperando_si_lista = True
            continue

        if deporte_estado == 1:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                deporte_estado = 0
                deporte_deporte = ""
                deporte_frecuencia = ""
                print("ZIA: Modo nutrición deporte cancelado.\n")
                continue
            if len(texto.strip()) < 2:
                print("ZIA: Cuéntame qué deporte o actividad haces.\n")
                continue
            deporte_deporte = texto.strip()
            print(
                "\nZIA: ¿Cuántos días a la semana entrenas y cuánto dura más o menos cada sesión? "
                "(ej.: 4 días, 1 hora)\n"
            )
            deporte_estado = 2
            continue

        if deporte_estado == 2:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                deporte_estado = 0
                deporte_deporte = ""
                deporte_frecuencia = ""
                print("ZIA: Modo nutrición deporte cancelado.\n")
                continue
            if len(texto.strip()) < 2:
                print("ZIA: Indica al menos la frecuencia semanal aproximada.\n")
                continue
            deporte_frecuencia = texto.strip()
            print(
                "\nZIA: ¿Tu objetivo principal: ganar masa muscular, perder grasa o mejorar rendimiento? "
                "En el mismo mensaje indica tu peso corporal en kg (necesario para calcular proteínas y calorías).\n"
            )
            deporte_estado = 3
            continue

        if deporte_estado == 3:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                deporte_estado = 0
                deporte_deporte = ""
                deporte_frecuencia = ""
                print("ZIA: Modo nutrición deporte cancelado.\n")
                continue
            objetivo_respuesta = texto.strip()
            if len(objetivo_respuesta) < 3:
                print("ZIA: Necesito tu objetivo y un peso en kg (ej.: «ganar músculo, 75 kg»).\n")
                continue
            peso_raw = extraer_peso_kg_desde_texto(objetivo_respuesta)
            peso_inferido = peso_raw is None
            peso_kg = float(peso_raw) if peso_raw is not None else 70.0
            dias_ent = extraer_dias_entreno_desde_texto(deporte_frecuencia)
            obj_clave = clasificar_objetivo_deporte(objetivo_respuesta)
            macros = calcular_macros_diarios_deporte(peso_kg, obj_clave, dias_ent)
            guardar_perfil_deporte_memoria(
                memoria,
                deporte_deporte,
                deporte_frecuencia,
                objetivo_respuesta,
                obj_clave,
                peso_kg,
                macros,
            )
            print(
                "\nZIA: Objetivos diarios aproximados: "
                f"{macros['proteinas_g']:.0f} g proteína, "
                f"{macros['carbohidratos_g']:.0f} g carbohidratos, "
                f"{macros['grasas_g']:.0f} g grasas, "
                f"~{macros['kcal_aprox']:.0f} kcal. Generando tu plan…\n"
            )
            resp_deporte = generar_respuesta_nutricion_deporte(
                client,
                perfil,
                memoria,
                deporte_deporte,
                deporte_frecuencia,
                objetivo_respuesta,
                peso_kg,
                macros,
                peso_inferido,
            )
            print(resp_deporte.rstrip())
            print()
            historial.extend(
                [
                    {
                        "role": "user",
                        "content": (
                            f"[Nutrición deporte] {deporte_deporte} | {deporte_frecuencia} | {objetivo_respuesta}"
                        ),
                    },
                    {"role": "assistant", "content": resp_deporte},
                ]
            )
            deporte_estado = 0
            deporte_deporte = ""
            deporte_frecuencia = ""
            continue

        if foto_esperando_ruta:
            tl = texto.lower()
            if tl in ("cancelar", "cancela"):
                foto_esperando_ruta = False
                foto_modo_nevera = False
                print("ZIA: Modo foto cancelado.\n")
                continue
            ruta_try = extraer_ruta_imagen_desde_texto(texto) or texto.strip().strip('"').strip("'")
            pimg = Path(ruta_try).expanduser()
            if pimg.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                print("ZIA: Necesito una ruta a un archivo .jpg, .jpeg o .png\n")
                continue
            if not pimg.is_file():
                print(f"ZIA: No encuentro el archivo: {pimg}\n")
                continue
            try:
                if foto_modo_nevera:
                    print("\nZIA: Mirando la foto…\n")
                    ing_txt = analizar_imagen_nevera_solo_productos(
                        client, perfil, memoria, str(pimg)
                    )
                    res = resumen_ingredientes_voz(ing_txt)
                    print(ing_txt)
                    print()
                    print(
                        f"ZIA: Veo que tienes {res}. "
                        "¿Qué te apetece más, algo rápido o tienes tiempo para cocinar?\n"
                    )
                    nevera_foto_ingredientes_texto = ing_txt
                    nevera_foto_esperando_plato = True
                else:
                    print(
                        "\nZIA: Qué buena pinta tiene esto 😍 Te paso la receta completa y lo que necesitas comprar.\n"
                    )
                    resp_foto = analizar_imagen_receta_vision(
                        client, perfil, memoria, str(pimg), modo="plato"
                    )
                    print(resp_foto.rstrip())
                    print()
                    historial.extend(
                        [
                            {"role": "user", "content": f"[Foto plato] {pimg}"},
                            {"role": "assistant", "content": resp_foto},
                        ]
                    )
            except OSError as e:
                print(f"ZIA: No pude leer la imagen: {e}\n")
            except Exception as e:
                print(f"ZIA: Error al analizar la imagen: {e}\n")
            foto_esperando_ruta = False
            foto_modo_nevera = False
            continue

        if (
            dispara_reconocimiento_foto(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and aprobacion_lista_ctx is None
            and reserva_restaurante_estado is None
        ):
            ruta_msg = extraer_ruta_imagen_desde_texto(texto)
            if ruta_msg:
                pmsg = Path(ruta_msg).expanduser()
                if not pmsg.is_file():
                    print(f"ZIA: No encuentro el archivo: {pmsg}\n")
                    continue
                es_nev = es_foto_nevera(texto)
                try:
                    if es_nev:
                        print("\nZIA: Mirando la foto…\n")
                        ing_txt = analizar_imagen_nevera_solo_productos(
                            client, perfil, memoria, str(pmsg)
                        )
                        res = resumen_ingredientes_voz(ing_txt)
                        print(ing_txt)
                        print()
                        print(
                            f"ZIA: Veo que tienes {res}. "
                            "¿Qué te apetece más, algo rápido o tienes tiempo para cocinar?\n"
                        )
                        nevera_foto_ingredientes_texto = ing_txt
                        nevera_foto_esperando_plato = True
                    else:
                        print(
                            "\nZIA: Qué buena pinta tiene esto 😍 Te paso la receta completa y lo que necesitas comprar.\n"
                        )
                        resp_foto = analizar_imagen_receta_vision(
                            client, perfil, memoria, str(pmsg), modo="plato"
                        )
                        print(resp_foto.rstrip())
                        print()
                        historial.extend(
                            [
                                {"role": "user", "content": f"[Foto plato] {texto}"},
                                {"role": "assistant", "content": resp_foto},
                            ]
                        )
                except OSError as e:
                    print(f"ZIA: No pude leer la imagen: {e}\n")
                except Exception as e:
                    print(f"ZIA: Error al analizar la imagen: {e}\n")
                continue
            if es_foto_nevera(texto):
                foto_modo_nevera = True
                foto_esperando_ruta = True
                print(
                    "\nZIA: FOTO NEVERA. Envíame la ruta del archivo (.jpg, .jpeg o .png).\n"
                )
            else:
                foto_esperando_tipo_nevera_o_plato = True
                print("\nZIA: ¿Es de la nevera o de un plato?\n")
            continue

        if (
            dispara_modo_deporte(texto)
            and seguimiento_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_tipo_nevera_o_plato
            and aprobacion_lista_ctx is None
            and reserva_restaurante_estado is None
        ):
            deporte_estado = 1
            deporte_deporte = ""
            deporte_frecuencia = ""
            print(
                "\nZIA: Modo NUTRICIÓN DEPORTE. ¿Qué deporte o actividad física practicas "
                "(gimnasio, running, natación, fútbol, etc.)?\n"
            )
            continue

        modo_dieta = detectar_modo_dieta(texto)
        if (
            modo_dieta
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_tipo_nevera_o_plato
            and aprobacion_lista_ctx is None
            and reserva_restaurante_estado is None
        ):
            nombre_modo = MODOS_DIETA[modo_dieta]["nombre"]
            print(f"\nZIA: Activando {nombre_modo}. Generando explicación, plan de 7 días y lista de la compra…\n")
            plan_dieta, _msgs_dieta = generar_plan_dieta_especial(client, perfil, memoria, modo_dieta)
            print(plan_dieta.rstrip())
            print()
            memoria["ultimo_plan"] = plan_dieta
            añadir_lista_al_historial(memoria, plan_dieta)
            guardar_memoria(memoria)
            historial.extend(
                [
                    {"role": "user", "content": f"[Dieta: {modo_dieta}] {texto}"},
                    {"role": "assistant", "content": plan_dieta},
                ]
            )
            aprobacion_lista_ctx = encolar_aprobacion_post_lista(
                plan_dieta, perfil, omitir_vista_carrito=True, origen="dieta"
            )
            continue

        if (
            dispara_optimizar_compra_inteligente(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not foto_esperando_ruta
            and not nevera_foto_esperando_plato
            and not foto_esperando_tipo_nevera_o_plato
            and aprobacion_lista_ctx is None
        ):
            print("\nZIA: Compra inteligente multi-supermercado: reparto por tienda y ruta sugerida…\n")
            contenido_opt = mensaje_optimizar_compra_multisuper(perfil, memoria)
            resp_opt = generar_respuesta_modo_supermercado(client, contenido_opt)
            print(resp_opt.rstrip())
            print()
            historial.extend(
                [
                    {"role": "user", "content": f"[Compra inteligente] {texto}"},
                    {"role": "assistant", "content": resp_opt},
                ]
            )
            continue

        if (
            dispara_comparar_precios(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not foto_esperando_ruta
            and not nevera_foto_esperando_plato
            and not foto_esperando_tipo_nevera_o_plato
            and aprobacion_lista_ctx is None
        ):
            print("\nZIA: Calculando totales por supermercado…\n")
            tot_txt = generar_totales_comparativa(client, memoria)
            print(tot_txt)
            print()
            print(
                "ZIA: Si quieres el carrito con precios en una cadena concreta, dime cuál "
                "(por ejemplo Mercadona o Lidl).\n"
            )
            historial.extend(
                [
                    {"role": "user", "content": f"[Comparar precios] {texto}"},
                    {"role": "assistant", "content": tot_txt},
                ]
            )
            continue

        if dispara_seguimiento_semanal(texto):
            if not (memoria.get("ultimo_plan") or "").strip():
                print(
                    "\nZIA: Aún no tengo un plan semanal anterior guardado; igualmente puedes contarme "
                    "cómo fue la semana y qué te gustaría para la que viene.\n"
                )
            print(
                "\nZIA: Vamos a preparar tu nueva semana. ¿Cómo te fue la semana pasada respecto al plan? "
                "¿Lo seguiste más o menos? ¿Cómo te has sentido (energía, tiempo, hambre)? Cuéntame lo que quieras.\n"
            )
            seguimiento_estado = 1
            continue

        if (
            detectar_falta_ingrediente(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_ruta
            and not foto_esperando_tipo_nevera_o_plato
            and not esperando_si_lista
            and carrito_fase is None
            and aprobacion_lista_ctx is None
            and falta_ing_ctx is None
            and not esperando_mini_lista_faltantes
            and reserva_restaurante_estado is None
        ):
            try:
                ctx_n = ejecutar_inferencia_y_ctx_falta_ingrediente(
                    client, perfil, memoria, texto
                )
                falta_ing_ctx = ctx_n
                assistant_txt = texto_bloque_falta_ingrediente_exacto(
                    ctx_n["ingredient"],
                    ctx_n["meal"],
                    ctx_n["quantity"],
                    ctx_n["price"],
                )
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": assistant_txt},
                    ]
                )
            except Exception as e:
                print(f"ZIA: No pude preparar el detalle ahora: {e}\n")
            continue

        if (
            detectar_consulta_mi_reserva_guardada(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_ruta
            and not foto_esperando_tipo_nevera_o_plato
            and not esperando_si_lista
            and carrito_fase is None
            and aprobacion_lista_ctx is None
            and falta_ing_ctx is None
            and not esperando_mini_lista_faltantes
            and reserva_restaurante_estado is None
        ):
            ult_r = memoria.get("ultima_reserva_restaurante") or {}
            res_txt = str(ult_r.get("texto_resumen", "") or "").strip()
            if res_txt:
                print(f"\n{res_txt}\n")
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": res_txt},
                    ]
                )
            else:
                msg_nr = (
                    "Aún no tengo una reserva guardada para mostrarte. "
                    "Cuando cierres una reserva conmigo, la tendrás aquí."
                )
                print(f"\nZIA: {msg_nr}\n")
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": msg_nr},
                    ]
                )
            continue

        if (
            detectar_intencion_reserva_restaurante(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_ruta
            and not foto_esperando_tipo_nevera_o_plato
            and not esperando_si_lista
            and carrito_fase is None
            and aprobacion_lista_ctx is None
            and falta_ing_ctx is None
            and not esperando_mini_lista_faltantes
            and reserva_restaurante_estado is None
        ):
            reserva_restaurante_estado = "recolectando"
            reserva_restaurante_ctx = reserva_restaurante_ctx_vacio()
            try:
                reserva_restaurante_ctx = fusionar_reserva_restaurante_ctx(
                    client, perfil, texto, reserva_restaurante_ctx, etapa="cita"
                )
            except Exception:
                pass
            ctx0, est_new, out_txt = procesar_reserva_tras_fusion_cita(
                client, perfil, reserva_restaurante_ctx
            )
            reserva_restaurante_ctx = ctx0
            if est_new:
                reserva_restaurante_estado = est_new
            print("\n" + out_txt.rstrip() + "\n")
            a_hist = out_txt.replace("ZIA: ", "").strip()
            historial.extend(
                [
                    {"role": "user", "content": texto},
                    {"role": "assistant", "content": a_hist},
                ]
            )
            continue

        if nevera_esperando_ingredientes:
            tl = texto.lower()
            if tl in ("cancelar", "cancela", "salir nevera", "no"):
                nevera_esperando_ingredientes = False
                print("ZIA: Vale, salimos del modo nevera inteligente.\n")
                continue
            ingredientes_usuario = texto.strip()
            if len(ingredientes_usuario) < 3:
                print("ZIA: Necesito al menos un par de ingredientes o productos. Lista lo que tengas.\n")
                continue
            respuesta_nevera = generar_nevera_inteligente(client, perfil, memoria, ingredientes_usuario)
            imprimir_zia_conversacion(respuesta_nevera)
            historial.append({"role": "user", "content": f"[Nevera inteligente] Ingredientes: {ingredientes_usuario}"})
            historial.append({"role": "assistant", "content": respuesta_nevera})
            nevera_esperando_ingredientes = False
            continue

        if (
            dispara_bloqueo_cocina_sin_ideas(texto)
            and seguimiento_estado == 0
            and deporte_estado == 0
            and not nevera_esperando_ingredientes
            and not nevera_foto_esperando_plato
            and not foto_esperando_ruta
            and not foto_esperando_tipo_nevera_o_plato
            and not esperando_si_lista
            and carrito_fase is None
            and aprobacion_lista_ctx is None
            and falta_ing_ctx is None
            and not esperando_mini_lista_faltantes
            and reserva_restaurante_estado is None
        ):
            try:
                r_bloqueo = generar_respuesta_bloqueo_cocina(client, perfil, texto)
                imprimir_zia_conversacion(r_bloqueo)
                historial.extend(
                    [
                        {"role": "user", "content": texto},
                        {"role": "assistant", "content": r_bloqueo},
                    ]
                )
            except Exception as e:
                print(f"ZIA: Uf, no pude responder bien: {e}\n")
            continue

        if dispara_nevera_inteligente(texto) and not nevera_foto_esperando_plato:
            if tiene_lista_ingredientes_explicita(texto):
                ing = ingredientes_tras_trigger(texto)
                if len(ing) >= 6:
                    respuesta_nevera = generar_nevera_inteligente(client, perfil, memoria, ing)
                    imprimir_zia_conversacion(respuesta_nevera)
                    historial.append({"role": "user", "content": texto})
                    historial.append({"role": "assistant", "content": respuesta_nevera})
                    continue
            nevera_esperando_ingredientes = True
            print(
                "\nZIA: Modo NEVERA INTELIGENTE. Escribe todo lo que tengas en casa "
                "(ingredientes, latas, huevos, verdura…), separado por comas o en una lista.\n"
            )
            continue

        if registrar_feedback_recetas(texto, memoria):
            guardar_memoria(memoria)
            print("ZIA: Anotado. Lo tendré en cuenta para el próximo plan.\n")
            continue

        historial.append({"role": "user", "content": texto})
        tail = historial[2:]
        if len(tail) > 24:
            tail = tail[-24:]
        messages_chat = [
            {"role": "system", "content": system_chat_con_memoria(perfil, memoria)},
            *tail,
        ]
        respuesta = completar(client, messages_chat, max_tokens=2048)
        imprimir_zia_conversacion(respuesta)
        historial.append({"role": "assistant", "content": respuesta})


if __name__ == "__main__":
    main()

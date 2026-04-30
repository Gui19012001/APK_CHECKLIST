# APK CHECKLIST DE QUALIDADE - MANGA / PNM / MOLA
# Mantém a lógica do Streamlit: busca apontamentos do dia por linha, remove os já inspecionados,
# mostra pendentes em ordem e salva o checklist no Supabase.

import os
import json
import time
import threading
import datetime
from pathlib import Path
from datetime import timezone, timedelta

import requests

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Line
from kivy.graphics.texture import Texture
from kivy.core.text import Label as CoreLabel
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup

try:
    from kivy.uix.camera import Camera
except Exception:
    Camera = None

BG_APP = (1, 1, 1, 1)
CARD_BG = (1, 1, 1, 1)
CARD_BORDER = (0.83, 0.88, 0.95, 1)
HEADER_TOP = (0.03, 0.14, 0.34, 1)
HEADER_BOTTOM = (0.10, 0.31, 0.61, 1)
FIELD_TOP = (0.05, 0.20, 0.44, 1)
FIELD_BOTTOM = (0.09, 0.30, 0.58, 1)
FIELD_BORDER = (0.18, 0.41, 0.74, 1)
BUTTON_TOP = (0.03, 0.14, 0.34, 1)
BUTTON_BOTTOM = (0.10, 0.31, 0.61, 1)
BUTTON_SECONDARY_TOP = (0.29, 0.35, 0.48, 1)
BUTTON_SECONDARY_BOTTOM = (0.21, 0.26, 0.38, 1)
TEXT_DARK = (0.09, 0.14, 0.22, 1)
TEXT_MUTED = (0.40, 0.47, 0.58, 1)
TEXT_LIGHT = (1, 1, 1, 1)
SUCCESS = (0.20, 0.64, 0.33, 1)
WARNING = (0.88, 0.58, 0.00, 1)
ERROR = (0.82, 0.22, 0.22, 1)
Window.clearcolor = BG_APP


def _rgba255(rgba):
    return bytes(max(0, min(255, int(round(c * 255)))) for c in rgba)


def make_vertical_gradient_texture(top_rgba, bottom_rgba):
    texture = Texture.create(size=(1, 2), colorfmt="rgba")
    buf = _rgba255(bottom_rgba) + _rgba255(top_rgba)
    texture.blit_buffer(buf, colorfmt="rgba", bufferfmt="ubyte")
    texture.wrap = "clamp_to_edge"
    texture.mag_filter = "linear"
    texture.min_filter = "linear"
    return texture


def load_simple_env(path):
    path = Path(path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent
load_simple_env(BASE_DIR / "teste.env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_ANON_KEY", "")).strip()
INACTIVITY_TIMEOUT_SEC = 30 * 60
# No Android, a câmera traseira normalmente é o índice 0.
# Se em algum tablet específico inverter, altere no teste.env: CAMERA_TRASEIRA_INDEX=1
try:
    CAMERA_TRASEIRA_INDEX = int(os.getenv("CAMERA_TRASEIRA_INDEX", "0"))
except Exception:
    CAMERA_TRASEIRA_INDEX = 0

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = timezone(timedelta(hours=-3))


def normalizar_texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _normaliza_codigo(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _agora_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fmt_data_local(valor):
    if not valor:
        return "-"
    try:
        dt = datetime.datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(TZ).strftime("%d/%m %H:%M")
    except Exception:
        return str(valor)


def _inicio_fim_hoje_utc():
    agora_local = datetime.datetime.now(TZ)
    inicio_local = datetime.datetime(agora_local.year, agora_local.month, agora_local.day, 0, 0, 0, tzinfo=TZ)
    fim_local = inicio_local + datetime.timedelta(days=1)
    return inicio_local.astimezone(datetime.timezone.utc).isoformat(), fim_local.astimezone(datetime.timezone.utc).isoformat()


def status_emoji_para_texto(emoji):
    return {"✅": "Conforme", "❌": "Não Conforme", "🟡": "N/A"}.get(emoji, "")


def garantir_permissao_camera_android():
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.CAMERA, Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
    except Exception:
        pass


def _app_config_file():
    try:
        app = App.get_running_app()
        base = Path(getattr(app, "user_data_dir", str(BASE_DIR)))
    except Exception:
        base = BASE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / "checklist_config.json"


def carregar_config_local():
    cfg = {}
    try:
        path = _app_config_file()
        if path.exists():
            cfg = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    return cfg


def salvar_config_local(cfg):
    try:
        path = _app_config_file()
        path.write_text(json.dumps(cfg or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def pasta_base_documentos():
    app = App.get_running_app()
    pasta = _normaliza_codigo(getattr(app, "pasta_padrao", ""))
    if pasta:
        try:
            base = Path(pasta).expanduser()
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:
            pass
    base = Path(getattr(app, "user_data_dir", str(BASE_DIR))) / "checklists_fotos"
    base.mkdir(parents=True, exist_ok=True)
    return base


def pasta_fotos_local(item_apontamento):
    base = pasta_base_documentos()
    tipo = _normaliza_codigo(item_apontamento.get("tipo_producao")).upper() or "NA"
    serie = _normaliza_codigo(item_apontamento.get("numero_serie")) or "NA"
    pasta = base / tipo / serie
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def nome_foto_local(item_apontamento):
    op = _normaliza_codigo(item_apontamento.get("op")) or "NA"
    serie = _normaliza_codigo(item_apontamento.get("numero_serie")) or "NA"
    ts = datetime.datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    return f"{serie}__OP{op}__vista_superior__{ts}.png"

def nome_pdf_local(item_apontamento):
    op = _normaliza_codigo(item_apontamento.get("op")) or "NA"
    serie = _normaliza_codigo(item_apontamento.get("numero_serie")) or "NA"
    ts = datetime.datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    return f"checklist_{serie}__OP{op}__{ts}.pdf"


def resposta_para_texto(valor):
    return {
        "✅": "Conforme",
        "❌": "Não Conforme",
        "🟡": "N/A",
        "Conforme": "Conforme",
        "Não Conforme": "Não Conforme",
        "N/A": "N/A",
    }.get(valor, _normaliza_codigo(valor) or "-")


def gerar_pdf_checklist_local(item_apontamento, respostas, complementos, usuario, foto_path=""):
    """
    Gera um PDF local na mesma pasta da foto:
    checklists_fotos / TIPO / NUMERO_SERIE / checklist_SERIE__OP...pdf

    Requer no APK: reportlab e pillow no requirements do buildozer.spec.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    except Exception as e:
        raise RuntimeError(
            "Biblioteca reportlab não encontrada. Instale com: pip install reportlab pillow "
            "e adicione reportlab,pillow no requirements do buildozer.spec. "
            f"Erro original: {e}"
        )

    numero_serie = _normaliza_codigo(item_apontamento.get("numero_serie")) or "-"
    op = _normaliza_codigo(item_apontamento.get("op")) or "-"
    tipo = _normaliza_codigo(item_apontamento.get("tipo_producao")).upper() or "-"
    data_apontamento = _fmt_data_local(item_apontamento.get("data_hora"))
    data_inspecao = datetime.datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

    pasta = pasta_fotos_local(item_apontamento)
    pdf_path = pasta / nome_pdf_local(item_apontamento)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TituloChecklist",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0B2D5C"),
        spaceAfter=10,
    )
    normal_style = ParagraphStyle(
        "NormalChecklist",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
    )
    question_style = ParagraphStyle(
        "PerguntaChecklist",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10,
    )

    story = []
    story.append(Paragraph(f"Checklist de Qualidade - {tipo}", title_style))

    resumo_data = [
        ["Série", numero_serie, "OP", op],
        ["Tipo", tipo, "Inspetor", _normaliza_codigo(usuario) or "Operador_Logado"],
        ["Data inspeção", data_inspecao, "Data apontamento", data_apontamento],
    ]
    resumo = Table(resumo_data, colWidths=[2.8 * cm, 5.0 * cm, 3.2 * cm, 6.2 * cm])
    resumo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6FA")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(resumo)
    story.append(Spacer(1, 0.35 * cm))

    perguntas = perguntas_por_tipo(tipo)
    tabela = [["#", "Item inspecionado", "Resposta", "Complemento"]]
    for idx, pergunta in enumerate(perguntas, start=1):
        comp = normalizar_texto(complementos.get(idx, "")) or "-"
        tabela.append([
            str(idx),
            Paragraph(pergunta, question_style),
            resposta_para_texto(respostas.get(idx)),
            Paragraph(comp, normal_style),
        ])

    table = Table(tabela, colWidths=[0.8 * cm, 10.5 * cm, 3.0 * cm, 3.3 * cm], repeatRows=1)
    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B2D5C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])
    for row_idx in range(1, len(tabela)):
        resposta = tabela[row_idx][2]
        if resposta == "Conforme":
            table_style.add("TEXTCOLOR", (2, row_idx), (2, row_idx), colors.HexColor("#1F9D55"))
        elif resposta == "Não Conforme":
            table_style.add("TEXTCOLOR", (2, row_idx), (2, row_idx), colors.HexColor("#D93025"))
        elif resposta == "N/A":
            table_style.add("TEXTCOLOR", (2, row_idx), (2, row_idx), colors.HexColor("#B7791F"))
    table.setStyle(table_style)

    story.append(table)
    story.append(Spacer(1, 0.35 * cm))

    if foto_path and Path(foto_path).exists():
        story.append(Paragraph("Foto anexada - vista superior", styles["Heading3"]))
        try:
            img = Image(foto_path)
            max_w = 16.5 * cm
            max_h = 10.0 * cm
            ratio = min(max_w / img.imageWidth, max_h / img.imageHeight)
            img.drawWidth = img.imageWidth * ratio
            img.drawHeight = img.imageHeight * ratio
            story.append(img)
        except Exception as e:
            story.append(Paragraph(f"Não foi possível inserir a foto no PDF: {e}", normal_style))
    else:
        story.append(Paragraph("Foto: nenhuma foto local anexada no momento do salvamento.", normal_style))

    story.append(Spacer(1, 0.25 * cm))
    story.append(Paragraph(f"Arquivo gerado localmente em: {pdf_path}", normal_style))

    doc.build(story)
    return str(pdf_path)


def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def supabase_url(table_name: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table_name}"


def supabase_get(table_name: str, params: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY não encontrados no teste.env")
    resp = requests.get(supabase_url(table_name), headers=supabase_headers(), params=params, timeout=25)
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"GET {table_name} -> HTTP {resp.status_code} | {resp.text}")
    return resp.json()


def supabase_post(table_name: str, payload):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY não encontrados no teste.env")
    headers = supabase_headers().copy()
    headers["Prefer"] = "return=representation"
    resp = requests.post(
        supabase_url(table_name),
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=25,
    )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"POST {table_name} -> HTTP {resp.status_code} | {resp.text}")
    try:
        return resp.json()
    except Exception:
        return []


PERGUNTAS_MANGA_PNM_BASE = [
    "Etiqueta do produto – As informações estão corretas / legíveis conforme modelo e gravação do eixo?",
    "Placa do Inmetro está correta / fixada e legível? Número corresponde à viga? Gravação do número de série da viga está legível e pintada?",
    "Etiqueta do ABS está conforme? Com número de série compatível ao da viga? Teste do ABS está aprovado?",
    "Rodagem – tipo correto? Especifique o modelo",
    "Graxeiras e Anéis elásticos estão em perfeito estado?",
    "Sistema de atuação correto? Springs ou cuícas em perfeitas condições? Especifique o modelo:",
    "Catraca do freio correta? Especifique modelo",
    "Tampa do cubo correta, livre de avarias e pintura nos critérios? As tampas dos cubos dos ambos os lados são iguais?",
    "Pintura do eixo livre de oxidação, isento de escorrimento, pontos sem tinta e camada conforme padrão?",
    "Os cordões de solda do eixo estão conformes?",
    "As caixas estão corretas? Escreva qual o modelo:",
    "As porcas da bolsa dos suspensores estão devidamente assentadas e apertadas?",
    "Etiqueta pede suspensor?",
    "Etiqueta pede Sem Suporte da Bolsa (S/AP)?",
    "Etiqueta pede Mão Francesa?",
]
ITEM_KEYS_MANGA_PNM = {
    1: "ETIQUETA", 2: "PLACA_IMETRO_E_NUMERO_SERIE", 3: "TESTE_ABS", 4: "RODAGEM",
    5: "GRAXEIRAS", 6: "SISTEMA_ATUACAO", 7: "CATRACA_FREIO", 8: "TAMPA_CUBO",
    9: "PINTURA_EIXO", 10: "SOLDA", 11: "CAIXAS", 12: "PORCAS",
    13: "FALTA_SUSPENSOR", 14: "FALTA_SPT_BOLSA", 15: "FALTA_MAO_FRANCESA", 16: "GRAU_DIVERGENTE",
}
OPCOES_MODELOS_MANGA_PNM = {
    4: ["", "Single", "Aço", "Alumínio", "N/A"],
    6: ["", "Spring", "Cuíca", "N/A"],
    7: ["", "Automático", "Manual", "N/A"],
    10: ["", "Conforme", "Respingo", "Falta de cordão", "Porosidade", "Falta de Fusão"],
}
ITENS_TEXTO_MANGA_PNM = {11, 15, 16}
ITENS_SIM_NAO_MANGA_PNM = {12, 13, 14}

PERGUNTAS_MOLA = [
    "Etiqueta do produto – As informações estão corretas / legíveis conforme modelo e gravação do eixo?",
    "Placa do Inmetro está correta / fixada e legível? Número corresponde à viga?",
    "A cor (Letra) do número de série é compatível com a etiqueta? Informe cor:",
    "Os grampos estão conforme a estrutura? Informe dimensão:",
    "Qual o feixe de mola utilizado?",
    "A medida do entre centro dos feixes está correta?",
    "Qual o comprimento do braço fixo utilizado?",
    "Qual o comprimento do braço móvel utilizado?",
    "Os parafusos dos braços estão apertados?",
    "Porcas das bases laterais do Rack estão corretamente apertadas?",
    "Tampa do cubo, pintura e graxeiras estão conforme?",
]
ITEM_KEYS_MOLA = {
    1: "ETIQUETA", 2: "PLACA_INMETRO", 3: "COR_DA_VIGA", 4: "GRAMPO", 5: "FEIXE_DE_MOLA",
    6: "ENTRE_CENTRO", 7: "BRACO_FIXO", 8: "BRACO_MOVEL", 9: "PARAFUSO_DOS_BRACOS",
    10: "RAQUEAMENTO", 11: "COMPONENTES",
}
ITENS_OBS_OBRIGATORIA_MOLA = {3, 4, 5, 6, 7, 8}

def is_mola(tipo_producao):
    return _normaliza_codigo(tipo_producao).upper() == "MOLA"

def linha_atual_app():
    try:
        app = App.get_running_app()
        return _normaliza_codigo(getattr(app, "linha", "MANGA_PNM")).upper() or "MANGA_PNM"
    except Exception:
        return "MANGA_PNM"


def perguntas_por_tipo(tipo_producao):
    tipo = _normaliza_codigo(tipo_producao).upper()
    if tipo == "MOLA":
        return list(PERGUNTAS_MOLA)
    perguntas = list(PERGUNTAS_MANGA_PNM_BASE)
    if tipo == "MANGA":
        perguntas.append("Grau do Manga conforme etiqueta do produto? Escreva qual o Grau:")
    return perguntas

def item_keys_por_tipo(tipo_producao):
    return ITEM_KEYS_MOLA if is_mola(tipo_producao) else ITEM_KEYS_MANGA_PNM

def tabela_checklist_por_tipo(tipo_producao):
    return "checklists_mola_detalhes" if is_mola(tipo_producao) else "checklists_manga_pnm_detalhes"

def carregar_apontamentos_hoje(limit=500, linha=None):
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    inicio_utc, fim_utc = _inicio_fim_hoje_utc()
    if linha == "MOLA":
        params = [("select", "id,numero_serie,op,usuario,data_hora"), ("data_hora", f"gte.{inicio_utc}"), ("data_hora", f"lt.{fim_utc}"), ("order", "data_hora.asc"), ("limit", str(limit))]
        dados = supabase_get("apontamentos_mola", params)
        for row in dados or []:
            row["tipo_producao"] = "MOLA"
        return dados
    params = [("select", "id,numero_serie,op,tipo_producao,usuario,data_hora"), ("data_hora", f"gte.{inicio_utc}"), ("data_hora", f"lt.{fim_utc}"), ("order", "data_hora.asc"), ("limit", str(limit))]
    return supabase_get("apontamentos_manga_pnm", params)

def carregar_checklists_existentes(linha=None, limit=5000):
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    inicio_utc, fim_utc = _inicio_fim_hoje_utc()
    if linha == "MOLA":
        return supabase_get(
            "checklists_mola_detalhes",
            [
                ("select", "numero_serie,data_hora"),
                ("data_hora", f"gte.{inicio_utc}"),
                ("data_hora", f"lt.{fim_utc}"),
                ("order", "data_hora.desc"),
                ("limit", str(limit)),
            ],
        )
    return supabase_get(
        "checklists_manga_pnm_detalhes",
        [
            ("select", "numero_serie,tipo_producao,data_hora"),
            ("data_hora", f"gte.{inicio_utc}"),
            ("data_hora", f"lt.{fim_utc}"),
            ("order", "data_hora.desc"),
            ("limit", str(limit)),
        ],
    )

def carregar_pendentes_inspecao(linha=None):
    """Mantido para compatibilidade: retorna somente itens ainda não inspecionados."""
    return [item for item in carregar_itens_inspecao_dia(linha=linha) if not item.get("inspecionado")]


def carregar_itens_inspecao_dia(linha=None):
    """
    Retorna TODOS os apontamentos do dia na ordem de produção.
    - Itens ainda sem checklist: inspecionado=False e mostram botão Inspecionar.
    - Itens já salvos no Supabase: inspecionado=True e mostram OK, sem botão.
    """
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    apontamentos = carregar_apontamentos_hoje(linha=linha)
    checklists = carregar_checklists_existentes(linha=linha)

    feitos = set()
    for row in checklists or []:
        serie = _normaliza_codigo(row.get("numero_serie"))
        tipo = "MOLA" if linha == "MOLA" else _normaliza_codigo(row.get("tipo_producao")).upper()
        if serie:
            feitos.add((serie, tipo))

    itens = []
    vistos = set()
    for row in apontamentos or []:
        serie = _normaliza_codigo(row.get("numero_serie"))
        tipo = "MOLA" if linha == "MOLA" else _normaliza_codigo(row.get("tipo_producao")).upper()
        if not serie:
            continue
        chave = (serie, tipo)
        if chave in vistos:
            continue
        vistos.add(chave)
        itens.append({
            "id": row.get("id"),
            "numero_serie": serie,
            "op": _normaliza_codigo(row.get("op")),
            "tipo_producao": tipo,
            "usuario": _normaliza_codigo(row.get("usuario")),
            "data_hora": row.get("data_hora"),
            "data_fmt": _fmt_data_local(row.get("data_hora")),
            "inspecionado": chave in feitos,
        })
    return itens


def salvar_checklist_supabase(item_apontamento, respostas, complementos, usuario):
    numero_serie = _normaliza_codigo(item_apontamento.get("numero_serie"))
    tipo_producao = _normaliza_codigo(item_apontamento.get("tipo_producao")).upper()
    op = _normaliza_codigo(item_apontamento.get("op"))
    usuario = _normaliza_codigo(usuario) or "Operador_Logado"
    perguntas = perguntas_por_tipo(tipo_producao)
    keys = item_keys_por_tipo(tipo_producao)
    registros = []
    for idx, _pergunta in enumerate(perguntas, start=1):
        emoji = respostas.get(idx)
        item_final = keys.get(idx, f"ITEM_{idx}")
        comp = normalizar_texto(complementos.get(idx, ""))
        if is_mola(tipo_producao):
            registros.append({"numero_serie": numero_serie, "op": op, "usuario": usuario, "data_hora": _agora_utc_iso(), "item": item_final, "status": status_emoji_para_texto(emoji), "observacao": comp or None})
        else:
            if comp:
                item_final = f"{item_final} - {comp}"
            registros.append({"numero_serie": numero_serie, "tipo_producao": tipo_producao, "item": item_final, "status": status_emoji_para_texto(emoji), "usuario": usuario, "data_hora": _agora_utc_iso()})
    return supabase_post(tabela_checklist_por_tipo(tipo_producao), registros)

def complemento_config(tipo_producao, idx):
    tipo = _normaliza_codigo(tipo_producao).upper()
    if tipo == "MOLA":
        if idx in ITENS_OBS_OBRIGATORIA_MOLA:
            return "texto", None, "Obrigatório: informe valor / tipo / dimensão."
        return "", None, ""
    if idx in OPCOES_MODELOS_MANGA_PNM:
        return "spinner", OPCOES_MODELOS_MANGA_PNM[idx], "Selecione o modelo quando aplicável."
    if idx in ITENS_SIM_NAO_MANGA_PNM:
        return "spinner", ["", "Sim", "Não"], "Selecione Sim ou Não quando aplicável."
    if idx in ITENS_TEXTO_MANGA_PNM:
        return "texto", None, "Digite o complemento quando aplicável."
    return "", None, ""



class BaseScreen(Screen):
    def on_touch_down(self, touch):
        app = App.get_running_app()
        if app and hasattr(app, "register_activity"):
            app.register_activity()
        return super().on_touch_down(touch)


class Card(BoxLayout):
    def __init__(self, bg=CARD_BG, border=CARD_BORDER, radius=18, **kwargs):
        super().__init__(**kwargs)
        self._bg = bg
        self._border = border
        self._radius = radius
        with self.canvas.before:
            self._bg_color = Color(*self._bg)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
            self._border_color = Color(*self._border)
            self._border_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.1)
        self.bind(pos=self._update_card, size=self._update_card)

    def _update_card(self, *_):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class GradientCard(BoxLayout):
    def __init__(self, top_color=HEADER_TOP, bottom_color=HEADER_BOTTOM, border=FIELD_BORDER, radius=18, **kwargs):
        super().__init__(**kwargs)
        self._radius = radius
        self._texture = make_vertical_gradient_texture(top_color, bottom_color)
        with self.canvas.before:
            self._color = Color(1, 1, 1, 1)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4, texture=self._texture)
            self._border_color = Color(*border)
            self._border_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.1)
        self.bind(pos=self._update_rect, size=self._update_rect)

    def _update_rect(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class StyledButton(Button):
    def __init__(self, text="", primary=True, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(48))
        super().__init__(text=text, background_normal="", background_down="", background_color=(0, 0, 0, 0), color=TEXT_LIGHT, **kwargs)
        self._radius = 16
        top, bottom, border = (BUTTON_TOP, BUTTON_BOTTOM, FIELD_BORDER) if primary else (BUTTON_SECONDARY_TOP, BUTTON_SECONDARY_BOTTOM, (0.38, 0.45, 0.56, 1))
        self._texture = make_vertical_gradient_texture(top, bottom)
        with self.canvas.before:
            self._c = Color(1, 1, 1, 1)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4, texture=self._texture)
            self._bc = Color(*border)
            self._line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.1)
        self.bind(pos=self._update_btn, size=self._update_btn)

    def _update_btn(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class StyledInput(TextInput):
    def __init__(self, hint="", navy=False, **kwargs):
        kwargs.setdefault("input_type", "text")
        kwargs.setdefault("keyboard_suggestions", True)
        self._navy = navy
        self._radius = 16
        self._texture = make_vertical_gradient_texture(FIELD_TOP, FIELD_BOTTOM)
        self._display_pad_x = dp(14)
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(46))
        kwargs.setdefault("padding", [dp(14), dp(12), dp(14), dp(12)])
        super().__init__(multiline=False, hint_text=hint, foreground_color=(1, 1, 1, 0), disabled_foreground_color=(1, 1, 1, 0), hint_text_color=(1, 1, 1, 0), cursor_color=(1, 1, 1, 1) if navy else (0, 0, 0, 1), selection_color=(1, 1, 1, 0.25) if navy else (0.2, 0.4, 0.8, 0.35), background_color=(0, 0, 0, 0), background_normal="", background_active="", write_tab=False, **kwargs)
        with self.canvas.before:
            if self._navy:
                self._fill_color = Color(1, 1, 1, 1)
                self._fill_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4, texture=self._texture)
                self._border_color = Color(*FIELD_BORDER)
            else:
                self._fill_color = Color(1, 1, 1, 1)
                self._fill_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
                self._border_color = Color(*CARD_BORDER)
            self._border_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.15)
        with self.canvas.after:
            self._mirror_color = Color(1, 1, 1, 1)
            self._mirror_rect = RoundedRectangle(pos=self.pos, size=(0, 0))
        self.bind(pos=self._update_bg, size=self._update_bg)
        self.bind(text=self._update_mirror_text)
        self.bind(hint_text=self._update_mirror_text)
        Clock.schedule_once(self._update_mirror_text, 0)

    def _update_bg(self, *_):
        self._fill_rect.pos = self.pos
        self._fill_rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)
        self._update_mirror_text()

    def _mirror_value(self):
        txt = self.text or ""
        if txt:
            return txt, ((1, 1, 1, 1) if self._navy else (0, 0, 0, 1))
        return self.hint_text or "", ((1, 1, 1, 0.92) if self._navy else TEXT_MUTED)

    def _update_mirror_text(self, *_):
        txt, color = self._mirror_value()
        self._mirror_color.rgba = color
        if not txt:
            self._mirror_rect.texture = None
            self._mirror_rect.size = (0, 0)
            return
        label = CoreLabel(text=txt, font_size=self.font_size, color=color)
        label.refresh()
        texture = label.texture
        self._mirror_rect.texture = texture
        self._mirror_rect.pos = (self.x + self._display_pad_x, self.center_y - texture.height / 2)
        self._mirror_rect.size = texture.size


class StyledSpinner(Spinner):
    def __init__(self, navy=False, **kwargs):
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), color=TEXT_LIGHT if navy else TEXT_DARK, **kwargs)
        self._radius = 16
        self._texture = make_vertical_gradient_texture(FIELD_TOP, FIELD_BOTTOM)
        with self.canvas.before:
            if navy:
                self._fill_color = Color(1, 1, 1, 1)
                self._fill_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4, texture=self._texture)
                self._border_color = Color(*FIELD_BORDER)
            else:
                self._fill_color = Color(1, 1, 1, 1)
                self._fill_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
                self._border_color = Color(*CARD_BORDER)
            self._border_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.15)
        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *_):
        self._fill_rect.pos = self.pos
        self._fill_rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class StatusBox(BoxLayout):
    text = StringProperty("")
    def __init__(self, **kwargs):
        texto_inicial = kwargs.pop("text", "")
        super().__init__(orientation="vertical", **kwargs)
        self.text = texto_inicial
        self._radius = 16
        with self.canvas.before:
            self._fill = Color(1, 1, 1, 1)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
            self._bc = Color(*CARD_BORDER)
            self._line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.15)
        self.lbl = Label(text=self.text, color=TEXT_DARK, halign="left", valign="middle", padding=(dp(10), dp(8)))
        self.lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(self.lbl)
        self.bind(pos=self._update_bg, size=self._update_bg)
        self.bind(text=self._update_text)

    def _update_bg(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)

    def _update_text(self, *_):
        self.lbl.text = self.text or ""


class StatusSelectButton(Button):
    def __init__(self, label, value, normal_color, **kwargs):
        super().__init__(
            text=label,
            background_normal="",
            background_down="",
            background_color=(0, 0, 0, 0),
            color=TEXT_DARK,
            font_size="12sp",
            bold=True,
            size_hint_x=None,
            width=dp(116),
            size_hint_y=None,
            height=dp(44),
            **kwargs
        )
        self.value = value
        self.normal_color = normal_color
        self._radius = 12
        with self.canvas.before:
            self._fill = Color(1, 1, 1, 1)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
            self._bc = Color(*normal_color)
            self._line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._radius), width=1.2)
        self.bind(pos=self._update, size=self._update)

    def set_selected(self, selected):
        if selected:
            self._fill.rgba = self.normal_color
            self._line.width = 2.4
            self.color = (1, 1, 1, 1)
        else:
            self._fill.rgba = (1, 1, 1, 1)
            self._line.width = 1.2
            self.color = TEXT_DARK

    def _update(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class QuestionCard(Card):
    def __init__(self, idx, pergunta, on_change, tipo_producao="MANGA", **kwargs):
        super().__init__(orientation="vertical", padding=dp(12), spacing=dp(8), size_hint_y=None, height=dp(150), bg=CARD_BG, border=CARD_BORDER, **kwargs)
        self.idx = idx
        self.tipo_producao = _normaliza_codigo(tipo_producao).upper()
        self.on_change = on_change
        self.complement_widget = None
        lbl = Label(text=f"{idx}. {pergunta}", color=TEXT_DARK, bold=True, halign="left", valign="top", size_hint_y=None, height=dp(48))
        lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(lbl)
        row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        self.buttons = {"✅": StatusSelectButton("CONFORME", "✅", SUCCESS), "❌": StatusSelectButton("NÃO CONF.", "❌", ERROR), "🟡": StatusSelectButton("N/A", "🟡", WARNING)}
        self.buttons["✅"].bind(on_release=lambda *_: self.select("✅"))
        self.buttons["❌"].bind(on_release=lambda *_: self.select("❌"))
        self.buttons["🟡"].bind(on_release=lambda *_: self.select("🟡"))
        row.add_widget(self.buttons["✅"]); row.add_widget(self.buttons["❌"]); row.add_widget(self.buttons["🟡"]); row.add_widget(Label(size_hint_x=0.04))
        tipo_widget, valores, dica = complemento_config(self.tipo_producao, idx)
        if tipo_widget == "spinner":
            self.complement_widget = StyledSpinner(text="", values=valores, size_hint_y=None, height=dp(42), navy=False)
            row.add_widget(self.complement_widget)
        elif tipo_widget == "texto":
            hint = "Informe valor / tipo / dimensão" if self.tipo_producao == "MOLA" else "Complemento"
            self.complement_widget = StyledInput(hint, size_hint_y=None, height=dp(42), navy=False)
            row.add_widget(self.complement_widget)
        else:
            row.add_widget(Label())
        self.add_widget(row)
        self.add_widget(Label(text=dica, color=TEXT_MUTED, font_size="12sp", halign="left", valign="middle", size_hint_y=None, height=dp(22)))
    def select(self, emoji):
        for key, btn in self.buttons.items():
            btn.set_selected(key == emoji)
        if callable(self.on_change):
            self.on_change(self.idx, emoji)
    def get_complemento(self):
        if not self.complement_widget:
            return ""
        return normalizar_texto(self.complement_widget.text)



class LoginScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        outer = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        self.add_widget(outer)
        outer.add_widget(Label(size_hint_y=0.12))
        wrap = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(350))
        outer.add_widget(wrap)
        wrap.add_widget(Label(size_hint_x=0.14))
        center_col = BoxLayout(orientation="vertical", size_hint_x=0.72, spacing=dp(14))
        wrap.add_widget(center_col)
        wrap.add_widget(Label(size_hint_x=0.14))
        header = GradientCard(orientation="vertical", size_hint_y=None, height=dp(120), padding=dp(18), spacing=dp(4))
        center_col.add_widget(header)
        header.add_widget(Label(text="Checklist de Qualidade", font_size="28sp", color=TEXT_LIGHT))
        header.add_widget(Label(text="MANGA / PNM / MOLA", font_size="16sp", color=(0.86, 0.92, 0.98, 1)))
        form = Card(orientation="vertical", padding=dp(18), spacing=dp(12), size_hint_y=None, height=dp(380))
        center_col.add_widget(form)
        form.add_widget(Label(text="Linha", color=TEXT_DARK, halign="left", size_hint_y=None, height=dp(22)))
        self.linha = StyledSpinner(text="MANGA_PNM", values=["MANGA_PNM", "MOLA"], size_hint_y=None, height=dp(46), navy=True)
        form.add_widget(self.linha)
        form.add_widget(Label(text="Usuário", color=TEXT_DARK, halign="left", size_hint_y=None, height=dp(22)))
        self.usuario = StyledInput("Usuário / Inspetor", input_type="text", keyboard_suggestions=True, navy=True)
        self.usuario.text = "Operador_Logado"
        form.add_widget(self.usuario)

        form.add_widget(Label(text="Pasta padrão para fotos e PDFs", color=TEXT_DARK, halign="left", size_hint_y=None, height=dp(22)))
        pasta_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
        self.pasta_padrao = StyledInput("Ex.: C:\\Checklists ou /storage/emulated/0/Documents/Checklists", input_type="text", keyboard_suggestions=True, navy=True)
        self.pasta_padrao.text = carregar_config_local().get("pasta_padrao", "")
        btn_pasta = StyledButton("Escolher", primary=False, size_hint_x=None, width=dp(110), height=dp(46))
        btn_pasta.bind(on_release=lambda *_: self.escolher_pasta())
        pasta_row.add_widget(self.pasta_padrao)
        pasta_row.add_widget(btn_pasta)
        form.add_widget(pasta_row)

        self.status = Label(text="", size_hint_y=None, height=dp(24), color=WARNING)
        form.add_widget(self.status)
        btn = StyledButton("Entrar", primary=True)
        btn.bind(on_release=lambda *_: self.entrar())
        form.add_widget(btn)
        outer.add_widget(Label())

    def on_pre_enter(self, *args):
        Window.softinput_mode = "below_target"
        Clock.schedule_once(lambda dt: self.force_focus_login(), 0.15)

    def force_focus_login(self):
        self.usuario.focus = True
        self.usuario.select_all()

    def show_status(self, msg):
        self.status.text = msg

    def escolher_pasta(self):
        # Windows/Linux: abre seletor nativo.
        # Android: se o seletor nativo não abrir, digite manualmente o caminho no campo.
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            caminho = filedialog.askdirectory(title="Escolha a pasta padrão dos checklists")
            root.destroy()
            if caminho:
                self.pasta_padrao.text = caminho
                self.status.text = f"Pasta selecionada: {caminho}"
        except Exception as e:
            self.status.text = f"Digite a pasta manualmente. Seletor não abriu: {e}"

    def entrar(self):
        app = App.get_running_app()
        app.usuario = normalizar_texto(self.usuario.text) or "Operador_Logado"
        app.linha = normalizar_texto(self.linha.text).upper() or "MANGA_PNM"
        app.pasta_padrao = normalizar_texto(self.pasta_padrao.text)
        salvar_config_local({"pasta_padrao": app.pasta_padrao})
        app.register_activity()
        self.manager.current = "pendentes"


class PendentesScreen(BaseScreen):
    busy = BooleanProperty(False)
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        self.add_widget(outer)
        topo = GradientCard(orientation="horizontal", size_hint_y=None, height=dp(86), padding=dp(16), spacing=dp(8))
        outer.add_widget(topo)
        left = BoxLayout(orientation="vertical")
        self.lbl_title = Label(text="Pendentes de Inspeção", font_size="24sp", color=TEXT_LIGHT, halign="left", valign="middle")
        self.lbl_title.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.lbl_user = Label(text="", font_size="13sp", color=(0.86, 0.92, 0.98, 1), halign="left", valign="middle")
        self.lbl_user.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        left.add_widget(self.lbl_title)
        left.add_widget(self.lbl_user)
        btn_refresh = StyledButton("Atualizar", primary=False, size_hint_x=None, width=dp(110))
        btn_refresh.bind(on_release=lambda *_: self.refresh())
        btn_sair = StyledButton("Sair", primary=False, size_hint_x=None, width=dp(90))
        btn_sair.bind(on_release=lambda *_: self.logout())
        topo.add_widget(left)
        topo.add_widget(btn_refresh)
        topo.add_widget(btn_sair)
        info = Card(orientation="vertical", padding=dp(12), spacing=dp(6), size_hint_y=None, height=dp(78))
        outer.add_widget(info)
        self.status = StatusBox(text="Carregando pendentes do dia...")
        info.add_widget(self.status)
        scroll = ScrollView(do_scroll_x=False)
        outer.add_widget(scroll)
        self.pendentes_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=[0, 0, 0, dp(12)])
        self.pendentes_box.bind(minimum_height=self.pendentes_box.setter("height"))
        scroll.add_widget(self.pendentes_box)

    def on_pre_enter(self, *args):
        app = App.get_running_app()
        self.lbl_user.text = f"Inspetor: {app.usuario or '-'} | Linha: {app.linha or '-'}"
        self.refresh()

    def logout(self):
        app = App.get_running_app()
        app.usuario = ""
        self.manager.current = "login"

    def set_status(self, texto):
        self.status.text = texto

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.set_status(f"Buscando apontamentos de produção do dia na linha {App.get_running_app().linha}...")
        self.pendentes_box.clear_widgets()
        threading.Thread(target=self._worker_refresh, daemon=True).start()

    def _worker_refresh(self):
        try:
            app = App.get_running_app()
            itens = carregar_itens_inspecao_dia(linha=app.linha)
            Clock.schedule_once(lambda dt: self._render_pendentes(itens), 0)
        except Exception as e:
            erro_msg = str(e)
            Clock.schedule_once(lambda dt, erro_msg=erro_msg: self._render_erro(erro_msg), 0)

    def _render_erro(self, erro):
        self.busy = False
        self.set_status(f"Erro ao carregar apontamentos: {erro}")

    def _render_pendentes(self, itens):
        self.busy = False
        self.pendentes_box.clear_widgets()

        if not itens:
            self.set_status("Nenhum apontamento de produção encontrado hoje para esta linha.")
            return

        pendentes = [x for x in itens if not x.get("inspecionado")]
        feitos = [x for x in itens if x.get("inspecionado")]

        if pendentes:
            self.set_status(f"{len(pendentes)} pendente(s) de inspeção. {len(feitos)} já inspecionado(s) hoje.")
        else:
            self.set_status(f"✅ Todos os {len(itens)} apontamento(s) de hoje já têm checklist salvo.")

        for item in itens:
            inspecionado = bool(item.get("inspecionado"))
            card = Card(orientation="horizontal", padding=dp(12), spacing=dp(8), size_hint_y=None, height=dp(82))
            status_txt = "[color=339955][b]OK - INSPECIONADO[/b][/color]" if inspecionado else "[color=D88C00][b]PENDENTE[/b][/color]"
            texto = (
                f"[b]Série:[/b] {item.get('numero_serie')}   {status_txt}\n"
                f"[b]OP:[/b] {item.get('op') or '-'}   [b]Tipo:[/b] {item.get('tipo_producao') or '-'}   [b]Hora:[/b] {item.get('data_fmt') or '-'}"
            )
            lbl = Label(text=texto, markup=True, color=TEXT_DARK, halign="left", valign="middle", size_hint_x=0.74)
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            card.add_widget(lbl)

            if inspecionado:
                ok = Label(text="OK", color=SUCCESS, bold=True, font_size="20sp", halign="center", valign="middle", size_hint_x=0.26)
                ok.bind(size=lambda inst, val: setattr(inst, "text_size", val))
                card.add_widget(ok)
            else:
                btn = StyledButton("Inspecionar", primary=True, size_hint_x=0.26, height=dp(52))
                btn.bind(on_release=lambda _, x=item: self.abrir_checklist(x))
                card.add_widget(btn)

            self.pendentes_box.add_widget(card)

    def abrir_checklist(self, item):
        app = App.get_running_app()
        app.item_atual = item
        self.manager.current = "checklist"


class ChecklistScreen(BaseScreen):
    busy = BooleanProperty(False)
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.question_cards = {}
        self.respostas = {}
        self.foto_local_path = ""
        self.lbl_foto = None
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        self.add_widget(outer)
        topo = GradientCard(orientation="horizontal", size_hint_y=None, height=dp(94), padding=dp(16), spacing=dp(8))
        outer.add_widget(topo)
        left = BoxLayout(orientation="vertical")
        self.lbl_title = Label(text="Checklist de Qualidade", font_size="22sp", color=TEXT_LIGHT, halign="left", valign="middle")
        self.lbl_title.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.lbl_info = Label(text="", font_size="13sp", color=(0.86, 0.92, 0.98, 1), halign="left", valign="middle")
        self.lbl_info.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        left.add_widget(self.lbl_title)
        left.add_widget(self.lbl_info)
        btn_voltar = StyledButton("Voltar", primary=False, size_hint_x=None, width=dp(100))
        btn_voltar.bind(on_release=lambda *_: self.voltar())
        topo.add_widget(left)
        topo.add_widget(btn_voltar)
        legenda = Card(orientation="vertical", padding=dp(10), spacing=dp(4), size_hint_y=None, height=dp(72))
        outer.add_widget(legenda)
        self.status = StatusBox(text="CONFORME = aprovado | NÃO CONF. = reprovado | N/A = não aplicável")
        legenda.add_widget(self.status)
        scroll = ScrollView(do_scroll_x=False)
        outer.add_widget(scroll)
        self.content = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10), padding=[0, 0, 0, dp(14)])
        self.content.bind(minimum_height=self.content.setter("height"))
        scroll.add_widget(self.content)

    def on_pre_enter(self, *args):
        Window.softinput_mode = "below_target"
        self.montar_checklist()

    def set_status(self, texto):
        self.status.text = texto

    def voltar(self):
        if not self.busy:
            self.manager.current = "pendentes"

    def montar_checklist(self):
        app = App.get_running_app()
        item = app.item_atual or {}
        numero_serie = _normaliza_codigo(item.get("numero_serie"))
        op = _normaliza_codigo(item.get("op"))
        tipo = _normaliza_codigo(item.get("tipo_producao")).upper()
        self.lbl_info.text = f"Série: {numero_serie} | OP: {op or '-'} | Tipo: {tipo or '-'}"
        self.content.clear_widgets()
        self.question_cards = {}
        self.respostas = {}
        perguntas = perguntas_por_tipo(tipo)
        for idx, pergunta in enumerate(perguntas, start=1):
            qcard = QuestionCard(idx, pergunta, on_change=self._on_resposta_change, tipo_producao=tipo)
            self.question_cards[idx] = qcard
            self.content.add_widget(qcard)

        self.foto_local_path = ""
        foto_card = Card(orientation="vertical", padding=dp(12), spacing=dp(8), size_hint_y=None, height=dp(134), bg=CARD_BG, border=CARD_BORDER)
        foto_card.add_widget(Label(text="Foto do checklist - vista superior do produto", color=TEXT_DARK, bold=True, halign="left", valign="middle", size_hint_y=None, height=dp(28)))
        foto_row = BoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=dp(52))
        btn_foto = StyledButton("Abrir câmera / Tirar foto", primary=True, size_hint_x=0.42)
        btn_foto.bind(on_release=lambda *_: self.abrir_camera())
        self.lbl_foto = Label(text="Nenhuma foto salva ainda. A foto será gravada em uma pasta local pelo número de série.", color=TEXT_MUTED, halign="left", valign="middle", size_hint_x=0.58)
        self.lbl_foto.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        foto_row.add_widget(btn_foto)
        foto_row.add_widget(self.lbl_foto)
        foto_card.add_widget(foto_row)
        foto_card.add_widget(Label(text=f"Pasta padrão: {pasta_base_documentos()} / TIPO / NÚMERO_SÉRIE", color=TEXT_MUTED, font_size="12sp", halign="left", valign="middle", size_hint_y=None, height=dp(24)))
        self.content.add_widget(foto_card)

        botoes = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(10))
        self.btn_salvar = StyledButton("💾 Salvar Checklist", primary=True)
        self.btn_salvar.bind(on_release=lambda *_: self.salvar())
        btn_cancelar = StyledButton("Cancelar", primary=False)
        btn_cancelar.bind(on_release=lambda *_: self.voltar())
        botoes.add_widget(self.btn_salvar)
        botoes.add_widget(btn_cancelar)
        self.content.add_widget(botoes)
        self.set_status("CONFORME = aprovado | NÃO CONF. = reprovado | N/A = não aplicável")

    def _on_resposta_change(self, idx, emoji):
        self.respostas[idx] = emoji

    def abrir_camera(self):
        if Camera is None:
            self.set_status("Erro: câmera não disponível neste ambiente. No APK Android, confira a permissão CAMERA no buildozer.spec.")
            return

        garantir_permissao_camera_android()
        app = App.get_running_app()
        item = app.item_atual or {}
        layout = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        camera = Camera(index=CAMERA_TRASEIRA_INDEX, play=True, resolution=(1280, 720))
        layout.add_widget(camera)
        botoes = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        btn_capturar = StyledButton("Salvar foto", primary=True)
        btn_fechar = StyledButton("Fechar", primary=False)
        botoes.add_widget(btn_capturar)
        botoes.add_widget(btn_fechar)
        layout.add_widget(botoes)
        popup = Popup(title="Foto - vista superior", content=layout, size_hint=(0.94, 0.94))

        def capturar(*_):
            try:
                pasta = pasta_fotos_local(item)
                arquivo = pasta / nome_foto_local(item)
                camera.export_to_png(str(arquivo))
                camera.play = False
                self.foto_local_path = str(arquivo)
                if self.lbl_foto:
                    self.lbl_foto.text = f"Foto salva localmente:\n{arquivo}"
                self.set_status(f"Foto salva no tablet: {arquivo.name}")
                popup.dismiss()
            except Exception as e:
                self.set_status(f"Erro ao salvar foto local: {e}")

        def fechar(*_):
            try:
                camera.play = False
            except Exception:
                pass
            popup.dismiss()

        btn_capturar.bind(on_release=capturar)
        btn_fechar.bind(on_release=fechar)
        popup.bind(on_dismiss=lambda *_: setattr(camera, "play", False))
        popup.open()

    def salvar(self):
        if self.busy:
            return
        app = App.get_running_app()
        item = app.item_atual or {}
        tipo = _normaliza_codigo(item.get("tipo_producao")).upper()
        perguntas = perguntas_por_tipo(tipo)
        faltantes = [idx for idx in range(1, len(perguntas) + 1) if not self.respostas.get(idx)]
        if faltantes:
            self.set_status(f"⚠️ Responda todos os itens. Falta(m): {', '.join(str(x) for x in faltantes)}")
            return
        complementos = {idx: qcard.get_complemento() for idx, qcard in self.question_cards.items()}
        if tipo == "MOLA":
            faltam_obs = [idx for idx in ITENS_OBS_OBRIGATORIA_MOLA if not normalizar_texto(complementos.get(idx, ""))]
            if faltam_obs:
                keys = item_keys_por_tipo(tipo)
                nomes = ", ".join(keys.get(i, str(i)) for i in faltam_obs)
                self.set_status(f"⚠️ Preencha as observações obrigatórias da MOLA: {nomes}")
                return
        self.busy = True
        self.btn_salvar.disabled = True
        self.set_status("Salvando checklist, gerando PDF e sincronizando no Supabase...")
        foto_path = self.foto_local_path
        threading.Thread(target=self._worker_salvar, args=(item, dict(self.respostas), complementos, app.usuario, foto_path), daemon=True).start()

    def _worker_salvar(self, item, respostas, complementos, usuario, foto_path):
        try:
            pdf_path = gerar_pdf_checklist_local(item, respostas, complementos, usuario, foto_path)
            salvar_checklist_supabase(item, respostas, complementos, usuario)
            Clock.schedule_once(lambda dt: self._salvo_ok(foto_path, pdf_path), 0)
        except Exception as e:
            erro_msg = str(e)
            Clock.schedule_once(lambda dt, erro_msg=erro_msg: self._salvo_erro(erro_msg), 0)

    def _salvo_ok(self, foto_path="", pdf_path=""):
        self.busy = False
        self.btn_salvar.disabled = False
        if foto_path:
            msg_foto = f"Foto local: {foto_path}"
        else:
            msg_foto = "Foto local: nenhuma foto anexada."
        if pdf_path:
            msg_pdf = f"PDF local: {pdf_path}"
        else:
            msg_pdf = "PDF local: não gerado."
        self.set_status(f"Checklist salvo com sucesso.\n{msg_foto}\n{msg_pdf}")
        Clock.schedule_once(lambda dt: self._voltar_e_atualizar(), 1.8)

    def _salvo_erro(self, erro):
        self.busy = False
        self.btn_salvar.disabled = False
        self.set_status(f"❌ Erro ao salvar checklist: {erro}")

    def _voltar_e_atualizar(self):
        try:
            App.get_running_app().item_atual = {}
        except Exception:
            pass
        self.manager.current = "pendentes"
        Clock.schedule_once(lambda dt: self.manager.get_screen("pendentes").refresh(), 0.2)


class ChecklistQualidadeApp(App):
    usuario = StringProperty("")
    linha = StringProperty("MANGA_PNM")
    pasta_padrao = StringProperty("")
    item_atual = {}
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_activity = time.monotonic()
        self.sm = None

    def build(self):
        self.title = "Checklist de Qualidade"
        self.pasta_padrao = carregar_config_local().get("pasta_padrao", "")
        Window.softinput_mode = "below_target"
        Window.bind(on_key_down=self._on_window_key_down)
        self.sm = ScreenManager()
        self.sm.add_widget(LoginScreen(name="login"))
        self.sm.add_widget(PendentesScreen(name="pendentes"))
        self.sm.add_widget(ChecklistScreen(name="checklist"))
        Clock.schedule_interval(self._check_inactivity, 1)
        return self.sm

    def _on_window_key_down(self, *args):
        self.register_activity()
        return False

    def register_activity(self):
        self.last_activity = time.monotonic()

    def _check_inactivity(self, dt):
        if not self.sm or self.sm.current == "login":
            return
        if time.monotonic() - self.last_activity >= INACTIVITY_TIMEOUT_SEC:
            self.force_logout("Sessão expirada após 30 minutos de inatividade.")

    def force_logout(self, msg="Sessão encerrada."):
        self.usuario = ""
        self.register_activity()
        if self.sm:
            login = self.sm.get_screen("login")
            login.show_status(msg)
            self.sm.current = "login"
            Clock.schedule_once(lambda dt: login.force_focus_login(), 0.10)


if __name__ == "__main__":
    ChecklistQualidadeApp().run()

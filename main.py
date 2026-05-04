# APK CHECKLIST DE REVISÃO - MANGA / PNM / MOLA / EIXO
# Mantém a lógica do Streamlit: busca apontamentos do dia por linha, remove os já revisados,
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
from kivy.utils import platform
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
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scatter import Scatter

try:
    from kivy.uix.camera import Camera
except Exception:
    Camera = None

try:
    # Plyer abre a câmera nativa do Android, mantendo melhor qualidade do sensor.
    from plyer import camera as native_camera
except Exception:
    native_camera = None

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
    """
    Janela operacional da produção: 06:00 até 02:00 do dia seguinte.

    Exemplos:
    - 2026-05-04 14:00 -> 2026-05-04 06:00 até 2026-05-05 02:00
    - 2026-05-05 01:30 -> 2026-05-04 06:00 até 2026-05-05 02:00
    - 2026-05-05 03:00 -> 2026-05-05 06:00 até 2026-05-06 02:00
      (janela ainda futura, então normalmente a lista fica vazia até iniciar o turno)
    """
    agora_local = datetime.datetime.now(TZ)

    if agora_local.time() < datetime.time(2, 0):
        data_base = agora_local.date() - datetime.timedelta(days=1)
    else:
        data_base = agora_local.date()

    inicio_local = datetime.datetime.combine(data_base, datetime.time(6, 0)).replace(tzinfo=TZ)
    fim_local = datetime.datetime.combine(data_base + datetime.timedelta(days=1), datetime.time(2, 0)).replace(tzinfo=TZ)

    return (
        inicio_local.astimezone(datetime.timezone.utc).isoformat(),
        fim_local.astimezone(datetime.timezone.utc).isoformat(),
    )

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

    # Android: usa uma pasta pública padrão para facilitar acesso e permitir câmera nativa.
    # Caso o Android bloqueie o acesso público por política de armazenamento, cai para user_data_dir.
    if platform == "android":
        try:
            from android.storage import primary_external_storage_path
            base = Path(primary_external_storage_path()) / "Documents" / "Checklists"
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
    return f"{serie}__OP{op}__vista_superior__{ts}.jpg"

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


def _limpar_texto_pdf(txt):
    """Evita caracteres que algumas fontes do FPDF não aceitam bem."""
    txt = str(txt or "")
    return (
        txt.replace("✅", "Conforme")
        .replace("❌", "Nao Conforme")
        .replace("🟡", "N/A")
        .replace("–", "-")
        .replace("—", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("ç", "c")
        .replace("Ç", "C")
        .replace("ã", "a")
        .replace("Ã", "A")
        .replace("õ", "o")
        .replace("Õ", "O")
        .replace("á", "a")
        .replace("Á", "A")
        .replace("à", "a")
        .replace("À", "A")
        .replace("â", "a")
        .replace("Â", "A")
        .replace("é", "e")
        .replace("É", "E")
        .replace("ê", "e")
        .replace("Ê", "E")
        .replace("í", "i")
        .replace("Í", "I")
        .replace("ó", "o")
        .replace("Ó", "O")
        .replace("ô", "o")
        .replace("Ô", "O")
        .replace("ú", "u")
        .replace("Ú", "U")
    )


def _pdf_cell_text(pdf, w, h, txt, border=1, align="L", fill=False):
    txt = _limpar_texto_pdf(txt)
    try:
        pdf.cell(w, h, txt, border=border, align=align, fill=fill)
    except Exception:
        pdf.cell(w, h, txt.encode("latin-1", "ignore").decode("latin-1"), border=border, align=align, fill=fill)


def _pdf_multicell_row(pdf, cols, widths, line_h=5):
    """
    Escreve uma linha com multi_cell mantendo bordas alinhadas.
    cols = textos; widths = larguras em mm.
    """
    x0 = pdf.get_x()
    y0 = pdf.get_y()

    # calcula altura aproximada pela maior quantidade de linhas após quebra simples
    line_counts = []
    for txt, w in zip(cols, widths):
        txt = _limpar_texto_pdf(txt)
        max_chars = max(8, int(w / 2.2))
        linhas = 1
        for parte in txt.split("\n"):
            linhas += max(0, (len(parte) - 1) // max_chars)
        line_counts.append(max(1, linhas))
    row_h = max(line_h * max(line_counts), 8)

    for txt, w in zip(cols, widths):
        x = pdf.get_x()
        y = pdf.get_y()
        pdf.rect(x, y, w, row_h)
        pdf.multi_cell(w, line_h, _limpar_texto_pdf(txt), border=0)
        pdf.set_xy(x + w, y)

    pdf.set_xy(x0, y0 + row_h)


def _safe_import_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        return Image, ImageDraw, ImageFont, ImageOps
    except Exception as e:
        raise RuntimeError(
            "Biblioteca Pillow não encontrada. Adicione pillow no requirements do buildozer.spec. "
            f"Erro original: {e}"
        )


def _font_default(size=18, bold=False):
    """
    Usa fonte padrão do Pillow para evitar dependência de fontes externas no Android.
    Mantém o PDF funcional sem reportlab/fpdf2/fontTools.
    """
    Image, ImageDraw, ImageFont, ImageOps = _safe_import_pillow()
    candidatos = []
    if os.name == "nt":
        candidatos += [
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        ]
    candidatos += [
        "/system/fonts/Roboto-Bold.ttf" if bold else "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/DroidSans-Bold.ttf" if bold else "/system/fonts/DroidSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for caminho in candidatos:
        try:
            if caminho and Path(caminho).exists():
                return ImageFont.truetype(caminho, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_text_box(draw, xy, text, font, fill=(17, 24, 39), max_width=600, line_spacing=4):
    """Desenha texto com quebra automática. Retorna a próxima posição Y."""
    x, y = xy
    text = _limpar_texto_pdf(text)
    words = text.split()
    lines = []
    atual = ""
    for word in words:
        teste = (atual + " " + word).strip()
        try:
            largura = draw.textbbox((0, 0), teste, font=font)[2]
        except Exception:
            largura = len(teste) * 8
        if largura <= max_width or not atual:
            atual = teste
        else:
            lines.append(atual)
            atual = word
    if atual:
        lines.append(atual)
    if not lines:
        lines = [""]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        try:
            h = draw.textbbox((x, y), line, font=font)[3] - draw.textbbox((x, y), line, font=font)[1]
        except Exception:
            h = 16
        y += h + line_spacing
    return y


def _medir_texto_quebrado(draw, text, font, max_width, line_spacing=4):
    text = _limpar_texto_pdf(text)
    words = text.split()
    lines = []
    atual = ""
    for word in words:
        teste = (atual + " " + word).strip()
        try:
            largura = draw.textbbox((0, 0), teste, font=font)[2]
        except Exception:
            largura = len(teste) * 8
        if largura <= max_width or not atual:
            atual = teste
        else:
            lines.append(atual)
            atual = word
    if atual:
        lines.append(atual)
    if not lines:
        lines = [""]
    altura = 0
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            h = bbox[3] - bbox[1]
        except Exception:
            h = 16
        altura += h + line_spacing
    return max(altura, 22), lines


def gerar_pdf_checklist_local(item_apontamento, respostas, complementos, usuario, foto_path=""):
    """
    Gera um PDF local na mesma pasta da foto usando somente Pillow.

    Motivo: evita ReportLab e FPDF2 no Android, removendo erros de compilação/runtime
    como fontTools ausente. No buildozer.spec, basta manter pillow.
    """
    Image, ImageDraw, ImageFont, ImageOps = _safe_import_pillow()

    numero_serie = _normaliza_codigo(item_apontamento.get("numero_serie")) or "-"
    op = _normaliza_codigo(item_apontamento.get("op")) or "-"
    tipo = _normaliza_codigo(item_apontamento.get("tipo_producao")).upper() or "-"
    data_apontamento = _fmt_data_local(item_apontamento.get("data_hora"))
    data_inspecao = datetime.datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

    pasta = pasta_fotos_local(item_apontamento)
    pdf_path = pasta / nome_pdf_local(item_apontamento)

    W, H = 1240, 1754  # A4 aproximado em 150 DPI
    margem = 70
    header_blue = (11, 45, 92)
    border = (203, 213, 225)
    light = (243, 246, 250)
    text_color = (17, 24, 39)
    green = (31, 157, 85)
    red = (217, 48, 37)
    amber = (183, 121, 31)

    font_title = _font_default(34, bold=True)
    font_head = _font_default(20, bold=True)
    font_normal = _font_default(18, bold=False)
    font_small = _font_default(15, bold=False)
    font_bold = _font_default(18, bold=True)

    pages = []

    def nova_pagina(com_cabecalho=False):
        img = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(img)
        y = margem
        if com_cabecalho:
            draw.rounded_rectangle((margem, y, W - margem, y + 82), radius=18, fill=header_blue)
            draw.text((margem + 24, y + 22), _limpar_texto_pdf(f"Checklist de Revisão - {tipo}"), font=font_title, fill="white")
            y += 112
        pages.append(img)
        return img, draw, y

    img, draw, y = nova_pagina(com_cabecalho=True)

    # Resumo
    resumo = [
        ("Serie", numero_serie, "OP", op),
        ("Tipo", tipo, "Revisor", _normaliza_codigo(usuario) or "Operador_Logado"),
        ("Revisao", data_inspecao, "Apontamento", data_apontamento),
    ]
    colx = [margem, margem + 170, margem + 510, margem + 690]
    colw = [170, 340, 180, W - margem - (margem + 690)]
    row_h = 46
    for a, b, c, d in resumo:
        draw.rectangle((margem, y, W - margem, y + row_h), fill=light, outline=border)
        draw.line((colx[1], y, colx[1], y + row_h), fill=border, width=1)
        draw.line((colx[2], y, colx[2], y + row_h), fill=border, width=1)
        draw.line((colx[3], y, colx[3], y + row_h), fill=border, width=1)
        draw.text((colx[0] + 10, y + 12), _limpar_texto_pdf(a), font=font_bold, fill=text_color)
        draw.text((colx[1] + 10, y + 12), _limpar_texto_pdf(b), font=font_normal, fill=text_color)
        draw.text((colx[2] + 10, y + 12), _limpar_texto_pdf(c), font=font_bold, fill=text_color)
        draw.text((colx[3] + 10, y + 12), _limpar_texto_pdf(d), font=font_normal, fill=text_color)
        y += row_h
    y += 30

    # Cabeçalho da tabela
    widths = [55, 650, 190, 205]
    xs = [margem]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)

    def desenhar_cab_tabela(draw, y):
        draw.rectangle((margem, y, W - margem, y + 46), fill=header_blue)
        heads = ["#", "Item revisado", "Resposta", "Complemento"]
        for i, head in enumerate(heads):
            draw.text((xs[i] + 10, y + 13), _limpar_texto_pdf(head), font=font_bold, fill="white")
            if i > 0:
                draw.line((xs[i], y, xs[i], y + 46), fill=(255, 255, 255), width=1)
        return y + 46

    y = desenhar_cab_tabela(draw, y)

    perguntas = perguntas_por_tipo(tipo)
    for idx, pergunta in enumerate(perguntas, start=1):
        resposta = resposta_para_texto(respostas.get(idx))
        comp = normalizar_texto(complementos.get(idx, "")) or "-"

        h1, _ = _medir_texto_quebrado(draw, pergunta, font_small, widths[1] - 20, line_spacing=3)
        h2, _ = _medir_texto_quebrado(draw, comp, font_small, widths[3] - 20, line_spacing=3)
        linha_h = max(52, h1 + 18, h2 + 18)

        if y + linha_h > H - 150:
            img, draw, y = nova_pagina(com_cabecalho=True)
            y = desenhar_cab_tabela(draw, y)

        draw.rectangle((margem, y, W - margem, y + linha_h), fill="white", outline=border)
        for x in xs[1:]:
            draw.line((x, y, x, y + linha_h), fill=border, width=1)

        draw.text((xs[0] + 18, y + 16), str(idx), font=font_normal, fill=text_color)
        _draw_text_box(draw, (xs[1] + 10, y + 10), pergunta, font_small, fill=text_color, max_width=widths[1] - 20, line_spacing=3)

        resp_color = text_color
        if resposta == "Conforme":
            resp_color = green
        elif resposta == "Não Conforme" or resposta == "Nao Conforme":
            resp_color = red
        elif resposta == "N/A":
            resp_color = amber
        draw.text((xs[2] + 10, y + 16), _limpar_texto_pdf(resposta), font=font_bold, fill=resp_color)
        _draw_text_box(draw, (xs[3] + 10, y + 10), comp, font_small, fill=text_color, max_width=widths[3] - 20, line_spacing=3)
        y += linha_h

    y += 34

    # Foto
    if y + 80 > H - margem:
        img, draw, y = nova_pagina(com_cabecalho=True)
    draw.text((margem, y), "Foto - vista superior", font=font_head, fill=text_color)
    y += 38

    if foto_path and Path(foto_path).exists():
        try:
            foto = Image.open(foto_path).convert("RGB")
            foto = ImageOps.exif_transpose(foto)
            max_w = W - 2 * margem
            max_h = H - y - 150
            if max_h < 420:
                img, draw, y = nova_pagina(com_cabecalho=True)
                draw.text((margem, y), "Foto - vista superior", font=font_head, fill=text_color)
                y += 38
                max_h = H - y - 150
            foto.thumbnail((max_w, max_h))
            x_foto = margem + (max_w - foto.width) // 2
            draw.rectangle((x_foto - 4, y - 4, x_foto + foto.width + 4, y + foto.height + 4), outline=border, width=2)
            img.paste(foto, (x_foto, y))
            y += foto.height + 30
        except Exception as e:
            y = _draw_text_box(draw, (margem, y), f"Nao foi possivel inserir a foto no PDF: {e}", font_normal, fill=red, max_width=W - 2 * margem)
    else:
        y = _draw_text_box(draw, (margem, y), "Foto: nenhuma foto local anexada no momento do salvamento.", font_normal, fill=text_color, max_width=W - 2 * margem)

    if y + 80 > H - margem:
        img, draw, y = nova_pagina(com_cabecalho=False)
    _draw_text_box(draw, (margem, y), f"Arquivo gerado localmente em: {pdf_path}", font_small, fill=(75, 85, 99), max_width=W - 2 * margem)

    if not pages:
        raise RuntimeError("Falha ao criar páginas do PDF.")
    pages[0].save(str(pdf_path), "PDF", resolution=100.0, save_all=True, append_images=pages[1:])
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


def _sem_limit_offset(params):
    return [(k, v) for k, v in list(params or []) if k not in {"limit", "offset"}]


def supabase_get_all(table_name: str, params, page_size=1000, max_pages=80):
    """
    Busca paginada no PostgREST/Supabase.

    Motivo: cada checklist salva várias linhas. Quando a busca vinha limitada,
    os primeiros checklists do turno podiam ficar fora do retorno e voltavam
    a aparecer como PENDENTE mesmo já estando salvos.
    """
    todos = []
    base_params = _sem_limit_offset(params)

    for page in range(max_pages):
        offset = page * page_size
        page_params = list(base_params) + [("limit", str(page_size)), ("offset", str(offset))]
        dados = supabase_get(table_name, page_params)
        if not dados:
            break
        todos.extend(dados)
        if len(dados) < page_size:
            break

    return todos

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

PERGUNTAS_EIXO = [
    "Etiqueta do produto – As informações estão corretas / legíveis conforme modelo e gravação do eixo?",
    "Placa do Inmetro está correta / fixada e legível? Número corresponde à viga? Gravação do número de série da viga está legível e pintada?",
    "Etiqueta do ABS está conforme? Com número de série compatível ao da viga? Teste do ABS está aprovado?",
    "Rodagem – tipo correto? Especifique o modelo",
    "Graxeiras e Anéis elásticos estão em perfeito estado?",
    "Sistema de atuação correto? Springs ou cuícas em perfeitas condições? Especifique o modelo:",
    "Catraca do freio correta? Especifique modelo",
    "Tampa do cubo correta, livre de avarias e pintura nos critérios? As tampas dos cubos dos ambos os lados são iguais?",
    "Pintura do eixo livre de oxidação, isento de escorrimento na pintura, pontos sem tinta e camada conforme padrão?",
    "Os cordões de solda do eixo estão conformes?",
]
ITEM_KEYS_EIXO = {
    1: "ETIQUETA",
    2: "PLACA_IMETRO E NÚMERO DE SÉRIE",
    3: "TESTE_ABS",
    4: "RODAGEM_MODELO",
    5: "GRAXEIRAS E ANÉIS ELÁSTICOS",
    6: "SISTEMA_ATUACAO",
    7: "CATRACA_FREIO",
    8: "TAMPA_CUBO",
    9: "PINTURA_EIXO",
    10: "SOLDA",
}
OPCOES_MODELOS_EIXO = {
    4: ["", "Single", "Aço", "Alumínio", "N/A"],
    6: ["", "Spring", "Cuíca", "N/A"],
    7: ["", "Automático", "Manual", "N/A"],
    10: ["", "Conforme", "Respingo", "Falta de cordão", "Porosidade", "Falta de Fusão"],
}
ITENS_OBS_OBRIGATORIA_EIXO = {4, 6, 7, 10}

def is_mola(tipo_producao):
    return _normaliza_codigo(tipo_producao).upper() == "MOLA"

def linha_atual_app():
    try:
        app = App.get_running_app()
        return _normaliza_codigo(getattr(app, "linha", "MANGA_PNM")).upper() or "MANGA_PNM"
    except Exception:
        return "MANGA_PNM"


def is_eixo(tipo_producao):
    tipo = _normaliza_codigo(tipo_producao).upper()
    return tipo in {"EIXO", "EIXOS"}


def perguntas_por_tipo(tipo_producao):
    tipo = _normaliza_codigo(tipo_producao).upper()
    if tipo == "MOLA":
        return list(PERGUNTAS_MOLA)
    if is_eixo(tipo):
        return list(PERGUNTAS_EIXO)
    perguntas = list(PERGUNTAS_MANGA_PNM_BASE)
    if tipo == "MANGA":
        perguntas.append("Grau do Manga conforme etiqueta do produto? Escreva qual o Grau:")
    return perguntas


def item_keys_por_tipo(tipo_producao):
    tipo = _normaliza_codigo(tipo_producao).upper()
    if tipo == "MOLA":
        return ITEM_KEYS_MOLA
    if is_eixo(tipo):
        return ITEM_KEYS_EIXO
    return ITEM_KEYS_MANGA_PNM


def tabela_checklist_por_tipo(tipo_producao):
    tipo = _normaliza_codigo(tipo_producao).upper()
    if tipo == "MOLA":
        return "checklists_mola_detalhes"
    if is_eixo(tipo):
        return "checklists"
    return "checklists_manga_pnm_detalhes"


def carregar_apontamentos_hoje(limit=500, linha=None):
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    inicio_utc, fim_utc = _inicio_fim_hoje_utc()

    if linha == "MOLA":
        params = [
            ("select", "id,numero_serie,op,usuario,data_hora"),
            ("data_hora", f"gte.{inicio_utc}"),
            ("data_hora", f"lt.{fim_utc}"),
            ("order", "data_hora.asc"),
        ]
        dados = supabase_get_all("apontamentos_mola", params, page_size=1000)
        for row in dados or []:
            row["tipo_producao"] = "MOLA"
        return dados

    if linha == "EIXO":
        params = [
            ("select", "id,numero_serie,op,tipo_producao,data_hora"),
            ("data_hora", f"gte.{inicio_utc}"),
            ("data_hora", f"lt.{fim_utc}"),
            ("order", "data_hora.asc"),
        ]
        dados = supabase_get_all("apontamentos", params, page_size=1000)
        filtrados = []
        for row in dados or []:
            tipo_row = _normaliza_codigo(row.get("tipo_producao")).upper()
            if "EIXO" in tipo_row:
                row["tipo_producao"] = "EIXO"
                row.setdefault("usuario", "")
                filtrados.append(row)
        return filtrados

    params = [
        ("select", "id,numero_serie,op,tipo_producao,usuario,data_hora"),
        ("data_hora", f"gte.{inicio_utc}"),
        ("data_hora", f"lt.{fim_utc}"),
        ("order", "data_hora.asc"),
    ]
    return supabase_get_all("apontamentos_manga_pnm", params, page_size=1000)

def carregar_checklists_existentes(linha=None, limit=5000):
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    inicio_utc, fim_utc = _inicio_fim_hoje_utc()

    if linha == "MOLA":
        return supabase_get_all(
            "checklists_mola_detalhes",
            [
                ("select", "numero_serie,data_hora"),
                ("data_hora", f"gte.{inicio_utc}"),
                ("data_hora", f"lt.{fim_utc}"),
                ("order", "data_hora.desc"),
            ],
            page_size=1000,
        )

    if linha == "EIXO":
        return supabase_get_all(
            "checklists",
            [
                ("select", "numero_serie,data_hora,reinspecao"),
                ("data_hora", f"gte.{inicio_utc}"),
                ("data_hora", f"lt.{fim_utc}"),
                ("order", "data_hora.desc"),
            ],
            page_size=1000,
        )

    return supabase_get_all(
        "checklists_manga_pnm_detalhes",
        [
            ("select", "numero_serie,tipo_producao,data_hora"),
            ("data_hora", f"gte.{inicio_utc}"),
            ("data_hora", f"lt.{fim_utc}"),
            ("order", "data_hora.desc"),
        ],
        page_size=1000,
    )

def carregar_pendentes_inspecao(linha=None):
    """Mantido para compatibilidade: retorna somente itens ainda não revisados."""
    return [item for item in carregar_itens_inspecao_dia(linha=linha) if not item.get("inspecionado")]



def carregar_itens_inspecao_dia(linha=None):
    """
    Retorna TODOS os apontamentos da janela operacional 06:00-02:00 na ordem de produção.
    - Itens ainda sem checklist: revisado=False e mostram botão Revisar.
    - Itens já salvos no Supabase: revisado=True e mostram OK, sem botão.
    """
    linha = _normaliza_codigo(linha or linha_atual_app()).upper()
    apontamentos = carregar_apontamentos_hoje(linha=linha)
    checklists = carregar_checklists_existentes(linha=linha)

    feitos = set()
    for row in checklists or []:
        serie = _normaliza_codigo(row.get("numero_serie"))
        if linha == "MOLA":
            tipo = "MOLA"
        elif linha == "EIXO":
            tipo = "EIXO"
        else:
            tipo = _normaliza_codigo(row.get("tipo_producao")).upper()
        if serie:
            feitos.add((serie, tipo))

    itens = []
    vistos = set()
    for row in apontamentos or []:
        serie = _normaliza_codigo(row.get("numero_serie"))
        if linha == "MOLA":
            tipo = "MOLA"
        elif linha == "EIXO":
            tipo = "EIXO"
        else:
            tipo = _normaliza_codigo(row.get("tipo_producao")).upper()
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

    if is_eixo(tipo_producao):
        reprovado = any(status_emoji_para_texto(respostas.get(idx)) == "Não Conforme" for idx in range(1, len(perguntas) + 1))
        for idx, _pergunta in enumerate(perguntas, start=1):
            emoji = respostas.get(idx)
            item_final = keys.get(idx, f"ITEM_{idx}")
            comp = normalizar_texto(complementos.get(idx, ""))
            registros.append({
                "numero_serie": numero_serie,
                "item": item_final,
                "status": status_emoji_para_texto(emoji),
                "observacoes": comp or "",
                "inspetor": usuario,
                "data_hora": _agora_utc_iso(),
                "produto_reprovado": "Sim" if reprovado else "Não",
                "reinspecao": "Não",
            })
        return supabase_post("checklists", registros)

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
    if is_eixo(tipo):
        if idx in OPCOES_MODELOS_EIXO:
            return "spinner", OPCOES_MODELOS_EIXO[idx], "Selecione o modelo quando aplicável."
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
        # Correção importante para Android:
        # A versão anterior escondia o texto real do TextInput e desenhava um
        # "espelho" por cima. Em alguns teclados Android isso fazia os campos
        # de complemento reaproveitarem/duplicarem textos digitados em outros
        # itens. Agora o TextInput usa o texto nativo visível, mantendo o mesmo
        # visual de borda/fundo, mas sem sobreposição de texto.
        kwargs.setdefault("input_type", "text")
        kwargs.setdefault("keyboard_suggestions", False)
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(46))
        kwargs.setdefault("padding", [dp(14), dp(12), dp(14), dp(12)])

        self._navy = navy
        self._radius = 16
        self._texture = make_vertical_gradient_texture(FIELD_TOP, FIELD_BOTTOM)

        super().__init__(
            multiline=False,
            hint_text=hint,
            foreground_color=TEXT_LIGHT if navy else TEXT_DARK,
            disabled_foreground_color=(1, 1, 1, 0.85) if navy else TEXT_MUTED,
            hint_text_color=(0.92, 0.96, 1, 1) if navy else TEXT_MUTED,
            cursor_color=(1, 1, 1, 1) if navy else (0, 0, 0, 1),
            selection_color=(1, 1, 1, 0.25) if navy else (0.2, 0.4, 0.8, 0.35),
            background_color=(0, 0, 0, 0),
            background_normal="",
            background_active="",
            write_tab=False,
            **kwargs,
        )

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

        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *_):
        self._fill_rect.pos = self.pos
        self._fill_rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)


class LoginStyledInput(StyledInput):
    """
    Campo usado somente na tela inicial.
    Mantém o visual azul do app, mas desenha o texto/hint em branco por cima.
    Isso evita o texto azul/baixo contraste no login sem voltar com o bug de duplicação
    dos campos do checklist, porque os campos das perguntas continuam usando StyledInput normal.
    """
    def __init__(self, hint="", **kwargs):
        kwargs.setdefault("navy", True)
        super().__init__(hint, **kwargs)

        # Esconde o texto nativo apenas nesta tela e desenha uma camada branca controlada.
        self.foreground_color = (1, 1, 1, 0)
        self.disabled_foreground_color = (1, 1, 1, 0)
        self.hint_text_color = (1, 1, 1, 0)
        self._login_pad_x = dp(14)

        with self.canvas.after:
            self._login_text_color = Color(1, 1, 1, 1)
            self._login_text_rect = RoundedRectangle(pos=self.pos, size=(0, 0), radius=[0, 0, 0, 0])

        self.bind(text=self._update_login_text)
        self.bind(hint_text=self._update_login_text)
        self.bind(pos=self._update_login_text)
        self.bind(size=self._update_login_text)
        self.bind(focus=self._update_login_text)
        Clock.schedule_once(self._update_login_text, 0)

    def _texto_visivel_login(self):
        if self.text:
            if getattr(self, "password", False):
                return "*" * len(self.text), (1, 1, 1, 1)
            return self.text, (1, 1, 1, 1)
        return self.hint_text or "", (1, 1, 1, 0.92)

    def _update_login_text(self, *_):
        try:
            txt, color = self._texto_visivel_login()
            self._login_text_color.rgba = color
            if not txt:
                self._login_text_rect.texture = None
                self._login_text_rect.size = (0, 0)
                return

            label = CoreLabel(text=txt, font_size=self.font_size, color=color)
            label.refresh()
            texture = label.texture
            self._login_text_rect.texture = texture
            self._login_text_rect.pos = (self.x + self._login_pad_x, self.center_y - texture.height / 2)
            self._login_text_rect.size = texture.size
        except Exception:
            pass


class StyledSpinner(Spinner):
    def __init__(self, navy=False, **kwargs):
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), color=TEXT_LIGHT if navy else TEXT_DARK, **kwargs)
        self.bold = True if navy else False
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



def login_form_label(text):
    lbl = Label(
        text=text,
        color=TEXT_DARK,
        halign="left",
        valign="middle",
        size_hint_y=None,
        height=dp(22),
    )
    lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
    return lbl

class LoginScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        outer = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        self.add_widget(outer)
        outer.add_widget(Label(size_hint_y=None, height=dp(10)))
        wrap = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(690))
        outer.add_widget(wrap)
        wrap.add_widget(Label(size_hint_x=0.10))
        center_col = BoxLayout(orientation="vertical", size_hint_x=0.80, spacing=dp(14))
        wrap.add_widget(center_col)
        wrap.add_widget(Label(size_hint_x=0.10))
        header = GradientCard(orientation="vertical", size_hint_y=None, height=dp(120), padding=dp(18), spacing=dp(4))
        center_col.add_widget(header)
        header.add_widget(Label(text="Checklist de Revisão", font_size="28sp", color=TEXT_LIGHT, bold=True))
        header.add_widget(Label(text="MANGA / PNM / MOLA / EIXO", font_size="16sp", color=(0.92, 0.96, 1, 1), bold=True))
        form = Card(orientation="vertical", padding=dp(18), spacing=dp(12), size_hint_y=None, height=dp(540))
        center_col.add_widget(form)
        cfg_login = carregar_config_local()
        ultima_linha = normalizar_texto(cfg_login.get("ultima_linha", "MANGA_PNM")).upper() or "MANGA_PNM"
        if ultima_linha not in ["MANGA_PNM", "MOLA", "EIXO"]:
            ultima_linha = "MANGA_PNM"
        ultimo_usuario = normalizar_texto(cfg_login.get("ultimo_usuario", "Operador_Logado")) or "Operador_Logado"

        form.add_widget(login_form_label("Linha"))
        self.linha = StyledSpinner(text=ultima_linha, values=["MANGA_PNM", "MOLA", "EIXO"], size_hint_y=None, height=dp(46), navy=True)
        form.add_widget(self.linha)
        form.add_widget(login_form_label("Usuário"))
        self.usuario = LoginStyledInput("Usuário / Revisor", input_type="text", keyboard_suggestions=False)
        self.usuario.text = ultimo_usuario
        form.add_widget(self.usuario)
        form.add_widget(login_form_label("Senha"))
        self.senha = LoginStyledInput("Senha padrão", input_type="number", keyboard_suggestions=False, password=True)
        self.senha.text = ""
        form.add_widget(self.senha)

        form.add_widget(login_form_label("Pasta padrão para fotos e PDFs"))
        pasta_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
        self.pasta_padrao = LoginStyledInput("Ex.: C:\\Checklists ou /storage/emulated/0/Documents/Checklists", input_type="text", keyboard_suggestions=False)
        self.pasta_padrao.text = cfg_login.get("pasta_padrao", "") or str(pasta_base_documentos())
        btn_pasta = StyledButton("Escolher", primary=False, size_hint_x=None, width=dp(110), height=dp(46))
        btn_pasta.bind(on_release=lambda *_: self.escolher_pasta())
        pasta_row.add_widget(self.pasta_padrao)
        pasta_row.add_widget(btn_pasta)
        form.add_widget(pasta_row)

        self.status = Label(text="", size_hint_y=None, height=dp(34), color=WARNING, halign="left", valign="middle")
        self.status.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        form.add_widget(self.status)
        btn = StyledButton("Entrar", primary=True)
        btn.bind(on_release=lambda *_: self.entrar())
        form.add_widget(btn)
        outer.add_widget(Label())

    def on_pre_enter(self, *args):
        Window.softinput_mode = "below_target"
        cfg_login = carregar_config_local()
        ultima_linha = normalizar_texto(cfg_login.get("ultima_linha", self.linha.text or "MANGA_PNM")).upper() or "MANGA_PNM"
        if ultima_linha in ["MANGA_PNM", "MOLA", "EIXO"]:
            self.linha.text = ultima_linha
        ultimo_usuario = normalizar_texto(cfg_login.get("ultimo_usuario", self.usuario.text or "Operador_Logado")) or "Operador_Logado"
        self.usuario.text = ultimo_usuario
        if cfg_login.get("pasta_padrao"):
            self.pasta_padrao.text = cfg_login.get("pasta_padrao")
        self.senha.text = ""
        Clock.schedule_once(lambda dt: self.force_focus_login(), 0.15)

    def force_focus_login(self):
        self.senha.focus = True

    def show_status(self, msg):
        self.status.text = msg

    def escolher_pasta(self):
        """
        No Android não existe tkinter. Por isso o botão define automaticamente
        uma pasta pública em Documents/Checklists. No Windows, mantém o seletor nativo.
        """
        try:
            from android.storage import primary_external_storage_path
            base = primary_external_storage_path()
            caminho = str(Path(base) / "Documents" / "Checklists")
            Path(caminho).mkdir(parents=True, exist_ok=True)
            self.pasta_padrao.text = caminho
            self.status.text = f"Pasta definida automaticamente\n{caminho}"
            return
        except Exception:
            pass

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
                return
        except Exception:
            pass

        caminho = str(Path(getattr(App.get_running_app(), "user_data_dir", str(BASE_DIR))) / "checklists_fotos")
        Path(caminho).mkdir(parents=True, exist_ok=True)
        self.pasta_padrao.text = caminho
        self.status.text = f"Pasta padrão definida no app\n{caminho}"

    def entrar(self):
        senha_digitada = normalizar_texto(self.senha.text)
        if senha_digitada != "123456":
            self.status.text = "Senha inválida. Use a senha padrão 123456."
            self.senha.text = ""
            self.senha.focus = True
            return

        app = App.get_running_app()
        app.usuario = normalizar_texto(self.usuario.text) or "Operador_Logado"
        app.linha = normalizar_texto(self.linha.text).upper() or "MANGA_PNM"
        app.pasta_padrao = normalizar_texto(self.pasta_padrao.text)
        salvar_config_local({
            "pasta_padrao": app.pasta_padrao,
            "ultimo_usuario": app.usuario,
            "ultima_linha": app.linha,
        })
        self.status.text = ""
        self.senha.text = ""
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
        self.lbl_title = Label(text="Pendentes de Revisão", font_size="24sp", color=TEXT_LIGHT, halign="left", valign="middle")
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
        self.lbl_user.text = f"Revisor: {app.usuario or '-'} | Linha: {app.linha or '-'}"
        self.refresh()

    def logout(self):
        app = App.get_running_app()
        cfg = carregar_config_local()
        if app.usuario:
            cfg["ultimo_usuario"] = app.usuario
        if app.linha:
            cfg["ultima_linha"] = app.linha
        if app.pasta_padrao:
            cfg["pasta_padrao"] = app.pasta_padrao
        salvar_config_local(cfg)
        app.usuario = ""
        self.manager.current = "login"

    def set_status(self, texto):
        self.status.text = texto

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.set_status(f"Buscando apontamentos da janela 06:00-02:00 na linha {App.get_running_app().linha}...")
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
            self.set_status("Nenhum apontamento encontrado na janela 06:00-02:00 para esta linha.")
            return

        pendentes = [x for x in itens if not x.get("inspecionado")]
        feitos = [x for x in itens if x.get("inspecionado")]

        if pendentes:
            self.set_status(f"{len(pendentes)} pendente(s) de revisão. {len(feitos)} já revisado(s) na janela.")
        else:
            self.set_status(f"✅ Todos os {len(itens)} apontamento(s) da janela já têm checklist salvo.")

        itens_ordenados = pendentes + feitos

        for item in itens_ordenados:
            revisado = bool(item.get("inspecionado"))
            card = Card(orientation="horizontal", padding=dp(12), spacing=dp(8), size_hint_y=None, height=dp(82))
            status_txt = "[color=339955][b]OK - REVISADO[/b][/color]" if revisado else "[color=D88C00][b]PENDENTE[/b][/color]"
            texto = (
                f"[b]Série:[/b] {item.get('numero_serie')}   {status_txt}\n"
                f"[b]OP:[/b] {item.get('op') or '-'}   [b]Tipo:[/b] {item.get('tipo_producao') or '-'}   [b]Hora:[/b] {item.get('data_fmt') or '-'}"
            )
            lbl = Label(text=texto, markup=True, color=TEXT_DARK, halign="left", valign="middle", size_hint_x=0.74)
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
            card.add_widget(lbl)

            if revisado:
                ok = Label(text="OK", color=SUCCESS, bold=True, font_size="20sp", halign="center", valign="middle", size_hint_x=0.26)
                ok.bind(size=lambda inst, val: setattr(inst, "text_size", val))
                card.add_widget(ok)
            else:
                btn = StyledButton("Revisar", primary=True, size_hint_x=0.26, height=dp(52))
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
        self._foto_pendente_path = ""
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        self.add_widget(outer)
        topo = GradientCard(orientation="horizontal", size_hint_y=None, height=dp(94), padding=dp(16), spacing=dp(8))
        outer.add_widget(topo)
        left = BoxLayout(orientation="vertical")
        self.lbl_title = Label(text="Checklist de Revisão", font_size="22sp", color=TEXT_LIGHT, halign="left", valign="middle")
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
        """
        Abre a câmera NATIVA do Android para evitar perda de qualidade e problemas de preview girado.
        Fallback: se estiver rodando no Windows ou se a câmera nativa falhar, abre a câmera interna do Kivy.
        """
        garantir_permissao_camera_android()
        app = App.get_running_app()
        item = app.item_atual or {}

        try:
            pasta = pasta_fotos_local(item)
            arquivo = pasta / nome_foto_local(item)
            self._foto_pendente_path = str(arquivo)
        except Exception as e:
            self.set_status(f"Erro ao preparar pasta da foto: {e}")
            return

        # 1) Preferência no APK: câmera nativa via Plyer.
        # Para funcionar, manter no buildozer.spec: requirements = ...,plyer,...
        if native_camera is not None:
            try:
                native_camera.take_picture(
                    filename=self._foto_pendente_path,
                    on_complete=self._foto_nativa_concluida,
                )
                self.set_status("Câmera nativa aberta. Tire a foto e confirme no app da câmera.")
                return
            except Exception as e:
                # Continua para tentativa por Intent manual / fallback Kivy.
                self.set_status(f"Câmera nativa via Plyer falhou. Tentando alternativa. Detalhe: {e}")

        # 2) Android sem Plyer: tenta Intent nativa manual.
        if platform == "android":
            try:
                if self._abrir_camera_nativa_android(self._foto_pendente_path):
                    return
            except Exception as e:
                self.set_status(f"Câmera nativa Android falhou. Abrindo câmera interna. Detalhe: {e}")

        # 3) Fallback desktop/seguro: câmera interna Kivy.
        self._abrir_camera_kivy_popup(item)

    def _foto_nativa_concluida(self, filename=None, *args):
        # Callback pode vir fora da thread principal.
        Clock.schedule_once(lambda dt: self._finalizar_foto_nativa(filename), 0)

    def _finalizar_foto_nativa(self, filename=None):
        caminho = _normaliza_codigo(filename) or self._foto_pendente_path
        if caminho.startswith("content://"):
            # Alguns providers retornam URI; nesse caso o arquivo planejado costuma ser o válido.
            caminho = self._foto_pendente_path

        arquivo = Path(caminho)
        if not arquivo.exists() or arquivo.stat().st_size <= 0:
            self.set_status("Foto não foi salva. Abra a câmera novamente e confirme a captura.")
            return

        try:
            self._normalizar_foto_pos_camera(arquivo)
        except Exception:
            # Não bloqueia o fluxo se a normalização falhar.
            pass

        self.foto_local_path = str(arquivo)
        if self.lbl_foto:
            self.lbl_foto.text = f"Foto salva localmente\n{arquivo}"
        self.set_status(f"Foto salva com câmera nativa: {arquivo.name}")

    def _normalizar_foto_pos_camera(self, arquivo):
        """Aplica orientação EXIF da câmera nativa sem reduzir qualidade perceptível."""
        try:
            from PIL import Image, ImageOps
            img = Image.open(str(arquivo))
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Mantém qualidade alta. Para PNG/JPG, Pillow escolhe pelo formato informado.
            img.save(str(arquivo), quality=95)
        except Exception:
            raise

    def _abrir_camera_nativa_android(self, arquivo_path):
        """
        Fallback nativo por Intent. O Plyer é preferível, mas este caminho ajuda em builds sem Plyer.
        """
        try:
            from jnius import autoclass
            from android import activity

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            Uri = autoclass("android.net.Uri")
            File = autoclass("java.io.File")

            try:
                StrictMode = autoclass("android.os.StrictMode")
                StrictMode.disableDeathOnFileUriExposure()
            except Exception:
                pass

            arquivo = File(arquivo_path)
            parent = arquivo.getParentFile()
            if parent is not None and not parent.exists():
                parent.mkdirs()

            uri = Uri.fromFile(arquivo)
            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(MediaStore.EXTRA_OUTPUT, uri)

            self._camera_request_code = 7813
            try:
                activity.unbind(on_activity_result=self._on_camera_activity_result)
            except Exception:
                pass
            activity.bind(on_activity_result=self._on_camera_activity_result)

            PythonActivity.mActivity.startActivityForResult(intent, self._camera_request_code)
            self.set_status("Câmera nativa aberta. Tire a foto e confirme.")
            return True
        except Exception:
            try:
                from android import activity
                activity.unbind(on_activity_result=self._on_camera_activity_result)
            except Exception:
                pass
            return False

    def _on_camera_activity_result(self, request_code, result_code, intent):
        try:
            if int(request_code) != int(getattr(self, "_camera_request_code", -1)):
                return
        except Exception:
            return

        try:
            from android import activity
            activity.unbind(on_activity_result=self._on_camera_activity_result)
        except Exception:
            pass

        # RESULT_OK normalmente é -1, mas se o arquivo existir, aceitamos mesmo assim.
        Clock.schedule_once(lambda dt: self._finalizar_foto_nativa(self._foto_pendente_path), 0)

    def _abrir_camera_kivy_popup(self, item):
        """
        Fallback seguro usando câmera interna do Kivy.
        Agora o preview abre já rotacionado dentro de um container, sem precisar clicar em Girar.

        Ajustes possíveis via teste.env, se algum tablet inverter:
        CAMERA_PREVIEW_ROTATION=270   # 0, 90, 180 ou 270
        FOTO_ROTATION=270             # rotação aplicada no arquivo salvo
        FOTO_FLIP_HORIZONTAL=0
        FOTO_FLIP_VERTICAL=0
        """
        if Camera is None:
            self.set_status("Erro: câmera não disponível neste ambiente. No APK Android, confira a permissão CAMERA no buildozer.spec.")
            return

        try:
            preview_rotation = int(os.getenv("CAMERA_PREVIEW_ROTATION", "270"))
        except Exception:
            preview_rotation = 270

        try:
            foto_rotation = int(os.getenv("FOTO_ROTATION", str(preview_rotation)))
        except Exception:
            foto_rotation = preview_rotation

        layout = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))

        preview_area = FloatLayout(size_hint_y=1)
        camera = Camera(index=CAMERA_TRASEIRA_INDEX, play=True, resolution=(1920, 1080))
        camera.allow_stretch = True
        camera.keep_ratio = True

        # Rotaciona o WIDGET da câmera, não apenas a imagem salva.
        # Isso corrige o preview que estava abrindo deitado no tablet.
        scatter = Scatter(
            do_rotation=False,
            do_translation=False,
            do_scale=False,
            auto_bring_to_front=False,
        )
        scatter.rotation = preview_rotation
        scatter.add_widget(camera)
        preview_area.add_widget(scatter)

        def ajustar_preview(*_):
            w, h = preview_area.size
            if w <= 0 or h <= 0:
                return

            rot = abs(preview_rotation) % 180
            if rot == 90:
                # Quando gira 90/270, troca largura/altura para ocupar o espaço corretamente.
                cam_w, cam_h = h, w
            else:
                cam_w, cam_h = w, h

            camera.size_hint = (None, None)
            camera.pos = (0, 0)
            camera.size = (cam_w, cam_h)

            scatter.size_hint = (None, None)
            scatter.size = (cam_w, cam_h)
            scatter.center = preview_area.center

        preview_area.bind(size=ajustar_preview, pos=ajustar_preview)
        Clock.schedule_once(lambda dt: ajustar_preview(), 0.1)
        Clock.schedule_once(lambda dt: ajustar_preview(), 0.6)

        layout.add_widget(preview_area)

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

                texture = camera.texture
                if texture is None:
                    raise RuntimeError("Câmera ainda não carregou a imagem. Aguarde 1 segundo e tente novamente.")

                size = texture.size
                pixels = texture.pixels

                from PIL import Image
                img = Image.frombytes("RGBA", size, pixels)

                if foto_rotation:
                    img = img.rotate(foto_rotation, expand=True)
                if os.getenv("FOTO_FLIP_VERTICAL", "0") == "1":
                    img = img.transpose(Image.FLIP_TOP_BOTTOM)
                if os.getenv("FOTO_FLIP_HORIZONTAL", "0") == "1":
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if img.mode != "RGB":
                    img = img.convert("RGB")

                img.save(str(arquivo), format="JPEG", quality=95)

                camera.play = False
                self.foto_local_path = str(arquivo)
                if self.lbl_foto:
                    self.lbl_foto.text = f"Foto salva localmente\n{arquivo}"
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
        if is_eixo(tipo):
            faltam_modelo = [idx for idx in ITENS_OBS_OBRIGATORIA_EIXO if not normalizar_texto(complementos.get(idx, ""))]
            if faltam_modelo:
                keys = item_keys_por_tipo(tipo)
                nomes = ", ".join(keys.get(i, str(i)) for i in faltam_modelo)
                self.set_status(f"⚠️ Preencha os modelos obrigatórios do EIXO: {nomes}")
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


class ChecklistRevisaoApp(App):
    usuario = StringProperty("")
    linha = StringProperty("MANGA_PNM")
    pasta_padrao = StringProperty("")
    item_atual = {}
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_activity = time.monotonic()
        self.sm = None

    def build(self):
        self.title = "Checklist de Revisão"
        cfg_login = carregar_config_local()
        self.pasta_padrao = cfg_login.get("pasta_padrao", "")
        self.usuario = normalizar_texto(cfg_login.get("ultimo_usuario", ""))
        self.linha = normalizar_texto(cfg_login.get("ultima_linha", "MANGA_PNM")).upper() or "MANGA_PNM"
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
        cfg = carregar_config_local()
        if self.usuario:
            cfg["ultimo_usuario"] = self.usuario
        if self.linha:
            cfg["ultima_linha"] = self.linha
        if self.pasta_padrao:
            cfg["pasta_padrao"] = self.pasta_padrao
        salvar_config_local(cfg)
        self.usuario = ""
        self.register_activity()
        if self.sm:
            login = self.sm.get_screen("login")
            login.show_status(msg)
            self.sm.current = "login"
            Clock.schedule_once(lambda dt: login.force_focus_login(), 0.10)


if __name__ == "__main__":
    ChecklistRevisaoApp().run()

# series_story.py
"""
Генерация юбилейных карточек для выкладки в Telegram Stories (1080x1920) в формате PNG.
Включает:
- Вертикальный градиентный фон по стадии серии
- Стеклянную карточку (glassmorphism с мягким неоновым свечением)
- Аватарки и ники двух участников
- Символ серии (пламя 🔥) между ними
- Крупный счетчик дней общения
- Статус серии (скрываемый при hide_stage=1)
- Маленький текст внизу: серии в тг: @rad1kal_bot
"""

import io
import logging
import os
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from stages import normalize_stage, stage_name, get_stage_by_days
from series import plural_days

log = logging.getLogger(__name__)

# Цветовые палитры для градиентов по стадиям (Верх, Середина, Низ)
STAGE_PALETTES = {
    "spark": {
        "gradient": [(15, 32, 39), (32, 58, 67), (44, 83, 100)],
        "accent": (46, 204, 113),
        "glow": (39, 174, 96, 75),
    },
    "dialogue": {
        "gradient": [(15, 12, 41), (48, 43, 99), (36, 36, 62)],
        "accent": (0, 198, 255),
        "glow": (0, 114, 255, 75),
    },
    "buddies": {
        "gradient": [(46, 8, 84), (102, 45, 142), (237, 30, 121)],
        "accent": (233, 64, 87),
        "glow": (138, 35, 135, 75),
    },
    "friendship": {
        "gradient": [(40, 10, 2), (180, 50, 10), (230, 126, 34)],
        "accent": (245, 175, 25),
        "glow": (241, 39, 17, 75),
    },
    "best_friends": {
        "gradient": [(50, 30, 0), (210, 105, 30), (247, 151, 30)],
        "accent": (255, 210, 0),
        "glow": (255, 193, 7, 85),
    },
    "soulmates": {
        "gradient": [(26, 16, 60), (74, 0, 224), (142, 45, 226)],
        "accent": (168, 85, 247),
        "glow": (147, 51, 234, 85),
    },
    "love": {
        "gradient": [(60, 0, 15), (180, 20, 45), (235, 51, 73)],
        "accent": (244, 92, 67),
        "glow": (235, 51, 73, 85),
    },
    "inseparable": {
        "gradient": [(55, 0, 45), (200, 30, 120), (236, 0, 140)],
        "accent": (252, 103, 103),
        "glow": (236, 0, 140, 85),
    },
    "legend": {
        "gradient": [(20, 20, 25), (45, 30, 15), (180, 130, 40)],
        "accent": (255, 215, 0),
        "glow": (212, 175, 55, 95),
    },
}

DEFAULT_PALETTE = {
    "gradient": [(20, 25, 45), (40, 50, 90), (70, 40, 110)],
    "accent": (255, 200, 55),
    "glow": (255, 200, 55, 75),
}


def _get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Загружает шрифт с гарантированной поддержкой кириллицы (сначала локальный в fonts/, затем системные)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_font_file = "bold.ttf" if bold else "regular.ttf"
    local_path = os.path.join(base_dir, "fonts", local_font_file)
    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception as e:
            log.debug("Ошибка загрузки локального шрифта %s: %s", local_path, e)

    font_paths = [
        # Windows
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahomabd.ttf" if bold else "C:/Windows/Fonts/tahoma.ttf",
        # Linux (Debian, Ubuntu, Alpine, Docker)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf" if bold else "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _get_emoji_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """Возвращает системный цветной шрифт эмодзи, если доступен."""
    emoji_paths = [
        "C:/Windows/Fonts/seguiemj.ttf",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/truetype/google-noto-color-emoji/NotoColorEmoji.ttf",
    ]
    for p in emoji_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return None


def _draw_emoji(draw: ImageDraw.Draw, xy: Tuple[int, int], emoji_char: str, size: int):
    """Отрисовывает эмодзи с поддержкой цветных шрифтов."""
    f = _get_emoji_font(size)
    if f:
        try:
            draw.text(xy, emoji_char, font=f, anchor="mm", embedded_color=True)
            return
        except Exception:
            pass
    # Фоллбэк: стандартный шрифт с золотистым оттенком
    f_def = _get_font(size, bold=True)
    draw.text(xy, emoji_char, fill=(255, 204, 0), font=f_def, anchor="mm")


def _draw_gradient(draw: ImageDraw.Draw, width: int, height: int, colors: list):
    """Рисует плавный 3-точечный вертикальный градиент."""
    c1, c2, c3 = colors
    h2 = height // 2

    for y in range(height):
        if y <= h2:
            t = y / max(1, h2)
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
        else:
            t = (y - h2) / max(1, height - h2)
            r = int(c2[0] * (1 - t) + c3[0] * t)
            g = int(c2[1] * (1 - t) + c3[1] * t)
            b = int(c2[2] * (1 - t) + c3[2] * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _make_circular_avatar(avatar_img: Image.Image, size: int) -> Image.Image:
    """Обрезает изображение в круг заданного размера с антиалиасингом."""
    avatar_img = avatar_img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size, size), fill=255)

    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(avatar_img, (0, 0), mask=mask)
    return output


def _make_fallback_avatar(name: str, size: int, color1: Tuple[int, int, int], color2: Tuple[int, int, int]) -> Image.Image:
    """Создаёт стильный круглый аватар с градиентным фоном и инициалом пользователя."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(size):
        t = y / max(1, size - 1)
        r = int(color1[0] * (1 - t) + color2[0] * t)
        g = int(color1[1] * (1 - t) + color2[1] * t)
        b = int(color1[2] * (1 - t) + color2[2] * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)

    avatar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    avatar.paste(img, (0, 0), mask=mask)

    draw_av = ImageDraw.Draw(avatar)
    initial = (name.strip()[:1] if name else "?").upper()
    font = _get_font(int(size * 0.45), bold=True)
    draw_av.text((size // 2, size // 2), initial, fill=(255, 255, 255), font=font, anchor="mm")
    return avatar


async def _download_user_avatar(bot, user_id: int, name: str, size: int) -> Image.Image:
    """Скачивает аватар пользователя через Telegram Bot API или возвращает стильный градиентный фоллбэк."""
    if user_id:
        try:
            photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
            if photos and photos.total_count > 0 and photos.photos:
                photo = photos.photos[0][-1]  # максимальное разрешение
                file_obj = await bot.get_file(photo.file_id)
                buf = io.BytesIO()
                await file_obj.download_to_memory(buf)
                buf.seek(0)
                img = Image.open(buf)
                return _make_circular_avatar(img, size)
        except Exception as e:
            log.debug("Не удалось скачать аватар для %s: %s", user_id, e)

    # Палитры для градиентных фоллбэков
    palette_pairs = [
        ((41, 128, 185), (109, 213, 250)),
        ((39, 174, 96), (46, 204, 113)),
        ((142, 68, 173), (235, 77, 75)),
        ((211, 84, 0), (243, 156, 18)),
        ((235, 51, 73), (244, 92, 67)),
        ((16, 185, 129), (5, 150, 105)),
    ]
    idx = abs(hash(name or str(user_id))) % len(palette_pairs)
    c1, c2 = palette_pairs[idx]
    return _make_fallback_avatar(name, size, c1, c2)


async def create_story_card(
    bot,
    s: dict,
    user1_id: int,
    user1_name: str,
    user2_id: int,
    user2_name: str,
) -> io.BytesIO:
    """
    Генерирует готовую графическую открытку 1080x1920 для Telegram Stories в кристальном качестве PNG.
    """
    W, H = 1080, 1920
    streak = s.get("current_streak", 0)
    raw_stage = s.get("stage")
    stage_key = normalize_stage(raw_stage)
    if (not raw_stage or stage_key == "spark") and streak >= 3 and s.get("auto_stage", 1):
        stage_key = get_stage_by_days(streak)
    palette = STAGE_PALETTES.get(stage_key, DEFAULT_PALETTE)
    accent = palette["accent"]

    # 1. Фоновый градиент
    base = Image.new("RGBA", (W, H))
    bg_draw = ImageDraw.Draw(base)
    _draw_gradient(bg_draw, W, H, palette["gradient"])

    # 2. Мягкое неоновое свечение в центре
    glow_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)
    glow_draw.ellipse(
        (W // 2 - 420, H // 2 - 400, W // 2 + 420, H // 2 + 450),
        fill=palette["glow"],
    )
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(130))
    base = Image.alpha_composite(base, glow_img)

    # 3. Стеклянная карточка по центру (Glassmorphism card)
    card_w, card_h = 940, 1280
    card_x = (W - card_w) // 2
    card_y = (H - card_h) // 2 - 25

    card_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card_layer)

    # Полупрозрачная подложка карточки с мягким оттенком текущей стадии
    c_top = palette["gradient"][0]
    card_draw.rounded_rectangle(
        (card_x, card_y, card_x + card_w, card_y + card_h),
        radius=48,
        fill=(c_top[0] // 3 + 12, c_top[1] // 3 + 14, c_top[2] // 3 + 20, 225),
        outline=(*accent, 140),
        width=3,
    )
    base = Image.alpha_composite(base, card_layer)

    draw = ImageDraw.Draw(base)

    # 4. Верхний бейдж
    badge_text = "🌟 СЕРИЯ ОБЩЕНИЯ 🌟"
    font_badge = _get_font(28, bold=True)
    badge_w = 440
    badge_h = 58
    badge_x = (W - badge_w) // 2
    badge_y = card_y + 60

    draw.rounded_rectangle(
        (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
        radius=29,
        fill=(255, 255, 255, 22),
        outline=(*accent, 190),
        width=2,
    )
    draw.text(
        (W // 2, badge_y + badge_h // 2),
        badge_text,
        fill=accent,
        font=font_badge,
        anchor="mm",
    )

    # 5. Аватарки и никнеймы участников
    avatar_size = 210
    avatar_y = card_y + 180
    avatar_radius = avatar_size // 2

    av1_center_x = card_x + 210
    av2_center_x = card_x + card_w - 210

    # Скачиваем аватарки
    av1_img = await _download_user_avatar(bot, user1_id, user1_name, avatar_size)
    av2_img = await _download_user_avatar(bot, user2_id, user2_name, avatar_size)

    # Обводка и вставка аватара 1
    draw.ellipse(
        (av1_center_x - avatar_radius - 6, avatar_y - 6, av1_center_x + avatar_radius + 6, avatar_y + avatar_size + 6),
        fill=(*accent, 220),
    )
    draw.ellipse(
        (av1_center_x - avatar_radius - 2, avatar_y - 2, av1_center_x + avatar_radius + 2, avatar_y + avatar_size + 2),
        fill=(18, 24, 40),
    )
    base.paste(av1_img, (av1_center_x - avatar_radius, avatar_y), av1_img)

    # Обводка и вставка аватара 2
    draw.ellipse(
        (av2_center_x - avatar_radius - 6, avatar_y - 6, av2_center_x + avatar_radius + 6, avatar_y + avatar_size + 6),
        fill=(*accent, 220),
    )
    draw.ellipse(
        (av2_center_x - avatar_radius - 2, avatar_y - 2, av2_center_x + avatar_radius + 2, avatar_y + avatar_size + 2),
        fill=(18, 24, 40),
    )
    base.paste(av2_img, (av2_center_x - avatar_radius, avatar_y), av2_img)

    # Символ между аватарками (Пламя 🔥 в бейдже)
    center_y = avatar_y + avatar_radius
    draw.ellipse(
        (W // 2 - 48, center_y - 48, W // 2 + 48, center_y + 48),
        fill=(255, 255, 255, 28),
        outline=(*accent, 170),
        width=2,
    )
    _draw_emoji(draw, (W // 2, center_y), "🔥", 46)

    # Ники / имена под аватарками
    font_name = _get_font(30, bold=True)
    name1_clean = (user1_name or "Пользователь")[:14]
    name2_clean = (user2_name or "Пользователь")[:14]

    draw.text((av1_center_x, avatar_y + avatar_size + 38), name1_clean, fill=(255, 255, 255), font=font_name, anchor="mm")
    draw.text((av2_center_x, avatar_y + avatar_size + 38), name2_clean, fill=(255, 255, 255), font=font_name, anchor="mm")

    # 6. Крупный счетчик дней общения
    count_y = card_y + 590
    font_num = _get_font(136, bold=True)
    streak_str = str(streak)

    # Тень числа для объема
    draw.text((W // 2 + 2, count_y + 3), streak_str, fill=(0, 0, 0, 140), font=font_num, anchor="mm")
    draw.text((W // 2, count_y), streak_str, fill=(255, 255, 255), font=font_num, anchor="mm")

    font_days_label = _get_font(38, bold=True)
    days_label = plural_days(streak).upper() + " ОБЩЕНИЯ"
    draw.text((W // 2, count_y + 92), days_label, fill=accent, font=font_days_label, anchor="mm")

    # Тонкий разделитель
    div_y = count_y + 165
    draw.line([(card_x + 120, div_y), (card_x + card_w - 120, div_y)], fill=(255, 255, 255, 45), width=2)

    # 7. Статус (скрываемый при hide_stage == 1)
    hide_stage = bool(s.get("hide_stage", 0))
    custom_stage = s.get("custom_stage")

    if not hide_stage:
        display_stage = custom_stage if custom_stage else stage_name(stage_key)
        status_box_y = div_y + 70
        status_box_h = 76
        status_box_w = min(700, card_w - 160)
        status_box_x = (W - status_box_w) // 2

        draw.rounded_rectangle(
            (status_box_x, status_box_y, status_box_x + status_box_w, status_box_y + status_box_h),
            radius=24,
            fill=(255, 255, 255, 22),
            outline=(255, 255, 255, 55),
            width=2,
        )

        font_st = _get_font(32, bold=True)
        status_full = f"Статус: {display_stage}"
        draw.text(
            (W // 2, status_box_y + status_box_h // 2),
            status_full,
            fill=(255, 255, 255),
            font=font_st,
            anchor="mm",
        )
    else:
        # Для скрытого статуса показываем стильную отметку секретности
        secret_y = div_y + 70
        font_sec = _get_font(30, bold=False)
        draw.text(
            (W // 2, secret_y + 35),
            "🔒 Статус скрыт",
            fill=(180, 195, 215),
            font=font_sec,
            anchor="mm",
        )

    # Дополнительная цитата / мотивирующая строка
    quote_y = card_y + card_h - 110
    font_quote = _get_font(26, bold=False)
    draw.text(
        (W // 2, quote_y),
        "Каждый день вместе делает связь крепче ✨",
        fill=(220, 228, 240),
        font=font_quote,
        anchor="mm",
    )

    # 8. Внизу маленький текст:
    # "внизу маленький текст (серии в тг: @rad1kal_bot)"
    footer_text = "серии в тг: @rad1kal_bot"
    font_footer = _get_font(28, bold=True)
    footer_y = H - 85

    draw.text(
        (W // 2, footer_y),
        footer_text,
        fill=(255, 255, 255, 210),
        font=font_footer,
        anchor="mm",
    )

    # Экспорт в высококачественный PNG
    buf = io.BytesIO()
    base.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

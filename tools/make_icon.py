from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bg)
    for y in range(size):
        t = y / (size - 1)
        bdraw.line([(0, y), (size, y)], fill=(int(18 + 10 * t), int(52 + 44 * t), int(120 + 70 * t), 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=56, fill=255)
    img.alpha_composite(Image.composite(bg, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))
    draw = ImageDraw.Draw(img)
    draw.arc([32, 24, 224, 216], start=205, end=335, fill=(255, 255, 255, 34), width=7)
    font_path = Path(r"C:\Windows\Fonts\segoeuib.ttf")
    try:
        font = ImageFont.truetype(str(font_path), 150)
    except Exception:
        font = ImageFont.load_default()
    text = "G"
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1] - 4), text, font=font, fill=(255, 255, 255, 255))
    draw.ellipse([176, 176, 224, 224], fill=(63, 185, 80, 255), outline=(255, 255, 255, 255), width=6)
    draw.ellipse([190, 190, 210, 210], fill=(255, 255, 255, 255))
    img.save(assets / "githubfix.ico", format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(assets / "githubfix.png")
    print(assets / "githubfix.ico")


if __name__ == "__main__":
    main()

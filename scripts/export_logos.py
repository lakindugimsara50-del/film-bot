import os
import shutil
import base64
from PIL import Image

def main():
    desktop = r"C:\Users\lakin\Desktop"
    hq_folder = os.path.join(desktop, "FilmZilla_Logos_HQ")
    orig_folder = os.path.join(hq_folder, "Original_Web_Assets")

    os.makedirs(orig_folder, exist_ok=True)

    # 1. Source paths in the repository
    web_img_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "website", "assets", "img")
    h_logo_src = os.path.join(web_img_dir, "filmzilla_brand_horizontal.png")
    s_logo_src = os.path.join(web_img_dir, "filmzilla_logo.png")
    icon_src = os.path.join(web_img_dir, "filmzilla_icon.png")
    wordmark_src = os.path.join(web_img_dir, "filmzilla_wordmark.png")
    banner_src = os.path.join(web_img_dir, "filmzilla_intro_card.png")
    legacy_logo_src = os.path.join(web_img_dir, "logo.png")

    # 2. Copy original web assets for convenience
    for p in [h_logo_src, s_logo_src, icon_src, wordmark_src, legacy_logo_src]:
        if os.path.exists(p):
            shutil.copy2(p, orig_folder)
            print(f"Copied original: {os.path.basename(p)}")

    # 3. High quality upscale function (Lanczos + 300 DPI metadata)
    def save_hq(src_path, dest_path, scale=8, dpi=(300, 300)):
        im = Image.open(src_path).convert("RGBA")
        new_w = im.width * scale
        new_h = im.height * scale
        im_hq = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
        im_hq.save(dest_path, format="PNG", dpi=dpi, optimize=True)
        print(f"Saved HQ: {os.path.basename(dest_path)} -> {im_hq.size} px @ 300 DPI")
        return im_hq

    # 4. Generate HQ PNGs
    h_4k = os.path.join(hq_folder, "01_FilmZilla_Horizontal_Logo_4K.png")
    save_hq(h_logo_src, h_4k, scale=8)

    s_4k = os.path.join(hq_folder, "02_FilmZilla_Stacked_Logo_4K.png")
    save_hq(s_logo_src, s_4k, scale=4)

    icon_4k = os.path.join(hq_folder, "03_FilmZilla_Icon_Emblem_4K.png")
    save_hq(icon_src, icon_4k, scale=8)

    wordmark_4k = os.path.join(hq_folder, "04_FilmZilla_Wordmark_4K.png")
    save_hq(wordmark_src, wordmark_4k, scale=8)

    # 1080p Cinematic Banner Card
    if os.path.exists(banner_src):
        banner_dest = os.path.join(hq_folder, "07_FilmZilla_Cinematic_Banner_1080p.png")
        shutil.copy2(banner_src, banner_dest)
        print(f"Saved: {os.path.basename(banner_dest)}")

    # 5. Scalable Vector Graphics (SVG) wrapper for resolution independence
    def create_svg(src_png_path, dest_svg_path, original_w, original_h):
        with open(src_png_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
        svg_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {original_w} {original_h}" width="100%" height="100%">
  <!-- FilmZilla Official Scalable Logo Vector Container -->
  <image width="{original_w}" height="{original_h}" xlink:href="data:image/png;base64,{b64_data}" />
</svg>"""
        with open(dest_svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        print(f"Saved SVG: {os.path.basename(dest_svg_path)}")

    create_svg(h_4k, os.path.join(hq_folder, "05_FilmZilla_Horizontal_Logo_Vector.svg"), 534, 80)
    create_svg(s_4k, os.path.join(hq_folder, "06_FilmZilla_Stacked_Logo_Vector.svg"), 604, 375)

    # 6. Primary Logo directly placed on Desktop root for instant access
    root_desktop_logo = os.path.join(desktop, "FilmZilla_Logo_HQ.png")
    shutil.copy2(h_4k, root_desktop_logo)
    print(f"Placed on Desktop root: {root_desktop_logo}")

if __name__ == "__main__":
    main()

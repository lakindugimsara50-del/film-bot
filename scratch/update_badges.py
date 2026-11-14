import io

with open('website/assets/js/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''  // Left badges (Quality or HOT)
  const rawQ = formatQualityLabel(movie.quality);
  const isHot = isTrending || movie.trending || movie.hot;
  const topBadge = isHot 
    ? `<span class="badge-card-hot"><i class="fa-solid fa-fire"></i> HOT</span>` 
    : `<span class="badge-top-left">${escHtml(rawQ)}</span>`;'''

replacement = '''  // Left badges (Always prominent Full HD / 4K / Quality label)
  const rawQ = formatQualityLabel(movie.quality);
  const qualityBadge = `<span class="badge-top-left">${escHtml(rawQ)}</span>`;'''

if target in content:
    content = content.replace(target, replacement, 1)
    content = content.replace('${topBadge}', '${qualityBadge}', 1)
    with open('website/assets/js/app.js', 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print("SUCCESS: app.js updated successfully")
else:
    print("ERROR: target not found!")

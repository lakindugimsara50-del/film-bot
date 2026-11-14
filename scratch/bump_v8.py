files = ['website/index.html', 'website/movie.html', 'website/search.html']

for path in files:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content = content.replace('v=20261113_legend_v7', 'v=20261113_mobile_v8')
    
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print(f"Bumped {path} to v8")

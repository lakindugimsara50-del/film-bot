import glob

files = ['website/index.html', 'website/movie.html', 'website/search.html']

for path in files:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace v6 and v6_v2 with v7
    content = content.replace('v=20261113_legend_v6_v2', 'v=20261113_legend_v7')
    content = content.replace('v=20261113_legend_v6', 'v=20261113_legend_v7')
    
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print(f"Bumped {path}")

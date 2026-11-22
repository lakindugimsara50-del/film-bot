const fs = require('fs');
const path = require('path');

const NEW_VER = '20261122_cinema_v60';
const htmlFiles = ['index.html', 'movie.html', 'search.html', 'teasers.html', 'companion.html'];

htmlFiles.forEach(file => {
  const filePath = path.join(__dirname, '../website', file);
  if (!fs.existsSync(filePath)) return;
  let content = fs.readFileSync(filePath, 'utf8');

  // Replace tokens.css without version or with old version
  content = content.replace(/href=["']assets\/css\/tokens\.css(?:\?[^"']*)?["']/g, `href="assets/css/tokens.css?v=${NEW_VER}"`);

  // Replace any assets/(css|js)/... or data/... with old version or without version
  content = content.replace(/((?:href|src)=["'](?:assets\/(?:css|js)|data)\/[^"'\?]+)(?:\?[^"']*)?(["'])/g, `$1?v=${NEW_VER}$2`);

  fs.writeFileSync(filePath, content, 'utf8');
  console.log(`Updated versions in ${file}`);
});

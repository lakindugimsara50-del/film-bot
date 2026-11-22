const fs = require('fs');

const cssFiles = [
  'website/assets/css/style.css',
  'website/assets/css/watch-party.css',
  'website/assets/css/teasers.css',
  'website/assets/css/netflix-ui.css',
  'website/assets/css/tokens.css'
];

cssFiles.forEach(file => {
  if (!fs.existsSync(file)) return;
  const content = fs.readFileSync(file, 'utf8');
  const lines = content.split('\n');

  lines.forEach((line, i) => {
    // 100vw often causes horizontal scrollbars on mobile
    if (line.includes('100vw') && !line.includes('max-width: 100vw')) {
      console.log(`[${file}:${i+1}] 100vw usage:`, line.trim());
    }
    // Fixed widths >= 360px not wrapped in media query
    const m = line.match(/(?:^|[^-\w])(?:min-)?width:\s*(\d+)px/);
    if (m && parseInt(m[1], 10) >= 360) {
      // Check if inside a mobile media query or desktop
      console.log(`[${file}:${i+1}] large fixed width (${m[1]}px):`, line.trim().slice(0, 80));
    }
  });
});

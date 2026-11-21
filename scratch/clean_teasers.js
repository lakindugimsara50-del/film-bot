const fs = require('fs');

function cleanFile(filePath) {
  let content = fs.readFileSync(filePath, 'utf8');
  content = content.replace(/"assets\/teasers\/sugriva-2026_teaser\.mp4"/g, '""');
  content = content.replace(/"assets\/teasers\/the-florist-2026_teaser\.mp4"/g, '""');
  content = content.replace(/https:\/\/www\.youtube-nocookie\.com\/embed\//g, 'https://www.youtube.com/embed/');
  fs.writeFileSync(filePath, content, 'utf8');
  console.log('Cleaned', filePath);
}

cleanFile('website/data/teasers_data.js');
cleanFile('website/data/teasers.json');

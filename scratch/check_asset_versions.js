const fs = require('fs');
['index.html', 'movie.html', 'search.html', 'teasers.html'].forEach(f => {
  const c = fs.readFileSync('website/' + f, 'utf8');
  const m = c.match(/(?:href|src)=["'][^"']+\.(?:css|js)[^"']*["']/g) || [];
  console.log('===', f, '===');
  m.forEach(x => console.log(' ', x));
});

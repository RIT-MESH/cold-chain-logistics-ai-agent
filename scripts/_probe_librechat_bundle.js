const fs = require('fs');
const files = fs.readdirSync('/app/client/dist/assets').filter(f => f.endsWith('.js') && !f.endsWith('.js.br') && !f.endsWith('.js.gz'));
let rel = 0, abs = 0;
const samples = [];
for (const f of files) {
  const s = fs.readFileSync('/app/client/dist/assets/' + f, 'utf8');
  const re = /['"`](\/?api\/v1)/g;
  let m;
  while ((m = re.exec(s))) {
    if (m[1].startsWith('/')) abs++; else rel++;
    if (samples.length < 6) samples.push(s.slice(Math.max(0, m.index - 30), m.index + 20).replace(/\n/g, ' '));
  }
  const b = /baseURL\s*[:=]\s*['"`]([^'"`]{0,40})/g;
  while ((m = b.exec(s))) samples.push('baseURL=' + m[1]);
}
console.log('relative api/v1 refs:', rel, '| absolute:', abs);
samples.slice(0, 8).forEach(x => console.log('  ', x));

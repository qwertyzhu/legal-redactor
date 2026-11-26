const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function loadRedactor() {
  const fields = new Map();
  const document = {
    getElementById(id) {
      if (!fields.has(id)) {
        const hidden = new Set(['hidden']);
        fields.set(id, {
          value: '',
          textContent: '',
          classList: {
            add: name => hidden.add(name),
            remove: name => hidden.delete(name),
            toggle: (name, force) => force ? hidden.add(name) : hidden.delete(name),
            contains: name => hidden.has(name),
          },
          addEventListener() {},
        });
      }
      return fields.get(id);
    },
  };
  const context = vm.createContext({ document, window: { addEventListener() {} } });
  vm.runInContext(script, context);
  return { context, document };
}

test('static restore leaves ambiguous mobile placeholders and restores unique values', () => {
  const { context, document } = loadRedactor();
  document.getElementById('textSourceInput').value =
    '原告手机为 13900001111，被告手机为 13800002222。邮箱 test@example.com。';
  document.getElementById('textModeSelect').value = 'ai';
  context.startTextRedact();
  const redacted = document.getElementById('textOutput').value;
  assert.equal(redacted, '原告手机为 [手机号]，被告手机为 [手机号]。邮箱 [邮箱]。');

  const source = document.getElementById('restoreSourceInput');
  const output = document.getElementById('restoreOutput');
  const warnings = document.getElementById('restoreWarnings');
  source.value = redacted;
  context.startRestore();
  assert.equal(output.value, '原告手机为 [手机号]，被告手机为 [手机号]。邮箱 test@example.com。');
  assert.match(warnings.textContent, /\[手机号\].*未还原/);
  assert.equal(warnings.classList.contains('hidden'), false);

  const ledgerField = document.getElementById('restoreLedgerInput');
  const ledger = JSON.parse(ledgerField.value);
  ledger.entities = ledger.entities.filter(e => e.original !== '13800002222');
  ledger.entities.push(ledger.entities.find(e => e.original === '13900001111'));
  ledgerField.value = JSON.stringify(ledger);
  source.value = '原告手机为 [手机号]，备用联系也是 [手机号]。';
  context.startRestore();
  assert.equal(output.value, '原告手机为 13900001111，备用联系也是 13900001111。');
  assert.equal(warnings.classList.contains('hidden'), true);
});

test('static restore accepts numeric values from a custom ledger', () => {
  const { context, document } = loadRedactor();
  document.getElementById('restoreLedgerInput').value = JSON.stringify({ entities: [
    { original: 20240001, replacement: 12345 },
    { original: '20240001', replacement: '12345' },
  ] });
  document.getElementById('restoreSourceInput').value = '编号 12345。';
  context.startRestore();
  assert.equal(document.getElementById('restoreOutput').value, '编号 20240001。');
  assert.equal(document.getElementById('restoreWarnings').classList.contains('hidden'), true);
});

test('static restore gives longer overlapping tokens precedence', () => {
  const { context, document } = loadRedactor();
  document.getElementById('restoreLedgerInput').value = JSON.stringify({ entities: [
    { original: '短替身原文', replacement: 'AB' },
    { original: '长替身原文', replacement: 'BCD' },
    { original: '另一短替身原文', replacement: 'AX' },
    { original: '第一个原文', replacement: 'XYZ' },
    { original: '第二个原文', replacement: 'XYZ' },
  ] });
  document.getElementById('restoreSourceInput').value = 'ABCD / AXYZ / AX';
  context.startRestore();
  assert.equal(document.getElementById('restoreOutput').value, 'A长替身原文 / AXYZ / 另一短替身原文');
  assert.match(document.getElementById('restoreWarnings').textContent, /XYZ.*未还原/);
});

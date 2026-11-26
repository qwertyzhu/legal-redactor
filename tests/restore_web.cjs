const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

test('static restore leaves ambiguous mobile placeholders and restores unique values', () => {
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

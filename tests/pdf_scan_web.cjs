const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const imageOp = 85;
const scan = { text: '', ops: [imageOp] };
const cover = { text: 'Cover', ops: [] };
const blank = { text: '', ops: [] };
const renderStarted = new Error('PDF export started');

function loadRedactor(pages) {
  const document = { getElementById: () => ({ addEventListener() {} }) };
  const pdfjsLib = {
    OPS: { paintImageXObject: imageOp },
    getDocument: () => ({
      promise: Promise.resolve({
        numPages: pages.length,
        async getPage(number) {
          const { text, ops } = pages[number - 1];
          return {
            getTextContent: async () => ({ items: text ? [{ str: text }] : [] }),
            getOperatorList: async () => ({ fnArray: ops }),
          };
        },
      }),
    }),
  };
  const PDFLib = { PDFDocument: { create() { throw renderStarted; } } };
  return vm.runInNewContext(script + '\nredactPdf;', {
    document, window: { addEventListener() {} }, pdfjsLib, PDFLib,
  });
}

test('pure image-only PDF cannot reach export', async () => {
  const redact = loadRedactor([scan]);
  await assert.rejects(redact(new ArrayBuffer(0), {}), /第 1 页.*未脱敏/);
});

test('a scanned page after a text cover cannot reach export', async () => {
  const redact = loadRedactor([cover, cover, cover, scan]);
  await assert.rejects(redact(new ArrayBuffer(0), {}), /第 4 页.*未脱敏/);
});

test('a blank page does not prevent a text PDF from reaching export', async () => {
  const redact = loadRedactor([cover, blank]);
  await assert.rejects(redact(new ArrayBuffer(0), {}), err => err === renderStarted);
});

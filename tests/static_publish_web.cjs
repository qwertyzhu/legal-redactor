const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const redact = vm.runInNewContext(script + '\n(text, options) => applyPlanToText(text, buildRedactionPlan(text, options));', {
  document: { getElementById: () => ({ addEventListener() {} }) },
  window: { addEventListener() {} },
});

test('publish keeps compound surnames and truncates labeled addresses', () => {
  assert.equal(redact('原告欧阳明，住北京市海淀区中关村大街27号院3号楼502室。', { mode: 'publish' }),
    '原告欧阳某，住北京市海淀区。');
  assert.equal(redact('被告王明，住所地江苏省苏州市昆山市玉山镇前进路27号。', { mode: 'publish' }),
    '被告王某，住所地江苏省苏州市昆山市。');
});

test('publish masks names that are exactly a compound surname', () => {
  assert.equal(redact('原告欧阳', { mode: 'publish' }), '原告欧某');
});

test('publish falls back to the city instead of retaining a named development', () => {
  for (const [address, expected] of [
    ['苏州市阳光小区5栋', '苏州市'],
    ['苏州市星河园区8号楼', '苏州市'],
    ['苏州市云桥社区6号', '苏州市'],
    ['上海市浦东新区晨光小区3栋', '上海市浦东新区'],
    ['山东省东营市东营区测试路8号', '山东省东营市东营区'],
    ['南宁市某部队营区3号楼', '南宁市'],
    ['某市某部队营区3号楼', '某地址'],
  ]) {
    assert.equal(redact(`住${address}`, { mode: 'publish' }), `住${expected}`);
  }
});

test('publish fuzzes numerical and personal financial-uppercase amounts like the CLI', () => {
  const text = '工资人民币500,000元（伍拾万元整）。索赔3.5万元（叁万伍仟元整）。';
  assert.equal(redact(text, { mode: 'publish' }), '工资数十万元（数十万元）。索赔数万元（叁万伍仟元整）。');
});

test('publish keeps clauses immediately after numbered addresses', () => {
  for (const [text, expected] of [
    ['住北京市海淀区中关村大街27号院3号楼502室为约定送达地址', '住北京市海淀区为约定送达地址'],
    ['送达地址为北京市海淀区中关村大街27号，该地址为约定送达地址。', '送达地址为北京市海淀区，该地址为约定送达地址。'],
    ['住北京市海淀区测试路27号院为民小区3栋2单元作为通讯地址', '住北京市海淀区作为通讯地址'],
  ]) {
    assert.equal(redact(text, { mode: 'publish' }), expected);
  }
});

test('publish redacts both addresses when one is a prefix of the other', () => {
  const text = '住北京市海淀区测试路8号为约定送达地址。另住北京市海淀区测试路8号院3号楼。';
  assert.equal(redact(text, { mode: 'publish' }), '住北京市海淀区为约定送达地址。另住北京市海淀区。');
});

test('publish finds an address after an unseparated clause', () => {
  const text = '住北京市海淀区测试路8号为送达地址住苏州市测试路5号';
  assert.equal(redact(text, { mode: 'publish' }), '住北京市海淀区为送达地址住苏州市');
});

test('publish redacts court housing but retains short court venues', () => {
  for (const [text, expected] of [
    ['住某市某区法院宿舍3号楼为约定送达地址', '住某市某区为约定送达地址'],
    ['住某市某区法院宿舍3号楼', '住某市某区'],
    ['地址北京市海淀区法院。', '地址北京市海淀区法院。'],
    ['地址：上海市浦东新区法院正门。', '地址：上海市浦东新区法院正门。'],
    ['地址：上海市浦东新区法院外。', '地址：上海市浦东新区法院外。'],
    ['地址：某市法院阳光小区旁。', '地址：某市法院阳光小区旁。'],
  ]) {
    assert.equal(redact(text, { mode: 'publish' }), expected);
  }
});

test('publish preserves address references and following prose', () => {
  for (const [text, expected] of [
    ['住址同上二层所在街道已确认。两人一同上二层办理立案手续。', '住址某地址。两人一同上二层办理立案手续。'],
    ['住北京市海淀区测试路27号为送达地址原告知悉该道路用途。', '住北京市海淀区为送达地址原告知悉该道路用途。'],
    ['住北京市海淀区测试路27号院3号楼502室，送达地址为上述地址且该道路为公共道路。', '住北京市海淀区，送达地址某地址。'],
    ['住北京市海淀区测试路27号为送达地址为上述地址且该道路为公共道路。', '住北京市海淀区为送达地址为上述地址且该道路为公共道路。'],
    ['送达地址为同上系诉讼文书使用的道路送达地址。', '送达地址某地址。'],
    ['送达地址：同上。送达地址为上述地址。', '送达地址：同上。送达地址为上述地址。'],
    ['住址为同上502室。另在1502室开会。', '住址某地址。另在1502室开会。'],
    ['住址同上3号楼502室', '住址某地址'],
    ['地址为上述地址3号楼502室', '地址某地址'],
    ['住址同上3号楼502室经确认无误。', '住址某地址。'],
    ['住址同上8号楼所在街道已确认。', '住址某地址。'],
  ]) {
    assert.equal(redact(text, { mode: 'publish' }), expected);
  }
});

test('publish recognizes locally numbered addresses without administrative areas', () => {
  for (const address of ['和平路二十七号', '绿洲公寓第二栋']) {
    assert.equal(redact(`住${address}`, { mode: 'publish' }), '住某地址');
  }
});

test('publish additions do not affect production or disable the automatic-detection opt-out', () => {
  const text = '原告欧阳明，住北京市海淀区中关村大街27号院3号楼502室。工资人民币500,000元（伍拾万元整）。';
  assert.equal(redact(text, { mode: 'production' }), text);
  assert.equal(redact(text, { mode: 'publish', autoConfident: false }), text);
});

const $ = (id) => document.getElementById(id);

let receipts = [];
let products = [];
const viewCache = new Map();
let tokens = [];
let activeMatches = [];
let currentId = null;
let currentProduct = null;
let view = 'receipts';
let listFocusIndex = -1;

function printReceipt() {
  const sheet = document.querySelector('.receipt-sheet');
  if (sheet) {
    const scale = window.visualViewport?.scale ?? 1;
    if (scale !== 1) {
      sheet.style.zoom = String(scale);
    }
    window.addEventListener('afterprint', () => sheet.style.removeProperty('zoom'), { once: true });
  }
  window.print();
}

function focusSearch() {
  $('q').focus({ preventScroll: true });
}

function visibleListItems() {
  if (!$('list-view').hidden) return [...$('results').querySelectorAll('[data-id]')];
  if (!$('products-view').hidden) return [...$('product-results').querySelectorAll('[data-product]')];
  return [];
}

function resetListFocus() {
  listFocusIndex = -1;
  document.querySelectorAll('.card.is-selected').forEach((el) => el.classList.remove('is-selected'));
}

function setListFocus(index) {
  const items = visibleListItems();
  if (!items.length) {
    resetListFocus();
    return;
  }
  listFocusIndex = Math.max(0, Math.min(items.length - 1, index));
  items.forEach((el, i) => el.classList.toggle('is-selected', i === listFocusIndex));
  items[listFocusIndex].scrollIntoView({ block: 'nearest' });
}

function handleEscape(event) {
  event.preventDefault();
  if (document.activeElement === $('q') && tokens.length) {
    clearSearch();
    onSearchInput();
    return;
  }
  if (currentId) {
    currentId = null;
    if (currentProduct) showProductDetail(currentProduct);
    else route();
    focusSearch();
    return;
  }
  if (currentProduct) {
    currentProduct = null;
    clearSearch();
    renderProducts();
    focusSearch();
    return;
  }
  if (document.activeElement === $('q')) {
    $('q').blur();
    return;
  }
  focusSearch();
}

function normalize(text) {
  return (text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '');
}

function tokenize(query) {
  return normalize(query).split(/\s+/).filter(Boolean);
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const UMLAUT_CLASSES = {
  a: 'aäå', e: 'eë', i: 'iïí', o: 'oöø', u: 'uü', s: 'sß',
  A: 'AÄÅ', E: 'EË', I: 'IÏÍ', O: 'OÖØ', U: 'UÜ', S: 'Sß',
};

function flexiblePattern(token) {
  return token.split('').map((ch) => {
    const group = UMLAUT_CLASSES[ch];
    if (group) return `[${group}]`;
    return escapeRegex(ch);
  }).join('');
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function highlightTokens(queryTokens) {
  return queryTokens.filter(
    (token) => token.length >= 2 && /[a-z0-9]/i.test(token),
  );
}

function highlightHtml(text, queryTokens) {
  const tokens = highlightTokens(queryTokens);
  const escaped = escapeHtml(text);
  if (!tokens.length) return escaped;

  const ranges = [];
  for (const token of [...tokens].sort((a, b) => b.length - a.length)) {
    const re = new RegExp(flexiblePattern(token), 'gi');
    let match;
    while ((match = re.exec(escaped)) !== null) {
      ranges.push([match.index, match.index + match[0].length]);
    }
  }
  if (!ranges.length) return escaped;

  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [start, end] of ranges) {
    if (!merged.length || start > merged[merged.length - 1][1]) {
      merged.push([start, end]);
    } else {
      merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], end);
    }
  }

  let out = '';
  let pos = 0;
  for (const [start, end] of merged) {
    out += escaped.slice(pos, start);
    out += `<mark>${escaped.slice(start, end)}</mark>`;
    pos = end;
  }
  out += escaped.slice(pos);
  return out;
}

function rowHaystack(row) {
  return normalize([
    row.id,
    row.date,
    row.store,
    row.street,
    row.locality,
    row.postalCode,
    row.address,
    ...(row.items || []),
    row.total != null ? String(row.total) : '',
  ].join(' '));
}

function matches(row, queryTokens) {
  if (!queryTokens.length) return true;
  const hay = rowHaystack(row);
  return queryTokens.every((token) => hay.includes(token));
}

function formatTotal(total, currency) {
  if (total == null || Number.isNaN(Number(total))) return '';
  const amount = Number(total).toFixed(2).replace('.', ',');
  return currency ? `${amount} ${currency}` : amount;
}

function previewItems(items) {
  if (!items?.length) return '';
  const shown = items.slice(0, 6).join(', ');
  const extra = items.length > 6 ? ` … +${items.length - 6} more` : '';
  return shown + extra;
}

function getMatches() {
  return receipts.filter((row) => matches(row, tokens));
}

function productMatches(product) {
  if (!tokens.length) return true;
  const hay = normalize(product.name);
  return tokens.every((token) => hay.includes(token));
}

function hideAllViews() {
  $('list-view').hidden = true;
  $('products-view').hidden = true;
  $('product-detail-view').hidden = true;
  $('detail-view').hidden = true;
}

function updateNav() {
  const onProducts = view === 'products';
  $('nav-receipts').classList.toggle('is-active', !onProducts && !currentId && !currentProduct);
  $('nav-products').classList.toggle('is-active', onProducts && !currentId && !currentProduct);
}

function updateSearchPlaceholder() {
  $('q').placeholder = view === 'products'
    ? 'Search products…'
    : 'Store, street, city, products, date…';
}

function setSearchQuery(query) {
  $('q').value = query;
  tokens = tokenize(query);
  $('clear-q').hidden = !tokens.length;
}

function clearSearch() {
  setSearchQuery('');
}

function buildUrl(overrides = {}) {
  const params = new URLSearchParams();
  const state = {
    view,
    p: currentProduct,
    q: $('q').value.trim(),
    r: currentId,
    ...overrides,
  };
  if (state.view === 'products') params.set('view', 'products');
  if (state.p) params.set('p', state.p);
  if (state.q) params.set('q', state.q);
  if (state.r) params.set('r', state.r);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

function syncUrl(overrides = {}) {
  const qs = buildUrl(overrides);
  const next = qs ? `${location.pathname}${qs}` : location.pathname;
  if (location.pathname + location.search !== next) {
    history.pushState(null, '', next);
  }
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  view = params.get('view') === 'products' ? 'products' : 'receipts';
  currentProduct = params.get('p');
  if (currentProduct) view = 'products';
  currentId = params.get('r');
  $('q').value = params.get('q') || '';
  tokens = tokenize($('q').value);
  $('clear-q').hidden = !tokens.length;
  updateNav();
  updateSearchPlaceholder();
}

function route() {
  updateNav();
  updateSearchPlaceholder();
  if (currentId) return showDetail(currentId);
  if (currentProduct) return showProductDetail(currentProduct);
  if (view === 'products') return renderProducts();
  return renderList();
}

function renderList() {
  view = 'receipts';
  currentId = null;
  currentProduct = null;
  activeMatches = getMatches();
  const list = $('results');
  const empty = $('empty');
  const stats = $('stats');

  stats.textContent = tokens.length
    ? `${activeMatches.length} of ${receipts.length} receipts`
    : `${receipts.length} receipts`;

  list.innerHTML = activeMatches.map((row) => {
    const title = highlightHtml(row.store || 'Unknown store', tokens);
    const sub = highlightHtml([row.date, row.address].filter(Boolean).join(' · '), tokens);
    const items = highlightHtml(previewItems(row.items), tokens);
    return `<li>
      <a class="card" href="${buildUrl({ view: 'receipts', r: row.id, p: null }) || './'}"
         data-id="${row.id}">
        <div class="card-head">
          <strong>${title}</strong>
          <span class="card-total">${formatTotal(row.total, row.currency)}</span>
        </div>
        <div class="card-sub">${sub}</div>
        ${items ? `<div class="card-items">${items}</div>` : ''}
      </a>
    </li>`;
  }).join('');

  empty.hidden = activeMatches.length > 0;
  hideAllViews();
  $('list-view').hidden = false;
  resetListFocus();
  syncUrl({ view: 'receipts', p: null, r: null });
}

function renderProducts() {
  view = 'products';
  currentId = null;
  currentProduct = null;
  const filtered = products.filter((product) => productMatches(product));
  const list = $('product-results');
  const empty = $('products-empty');
  const stats = $('stats');

  stats.textContent = tokens.length
    ? `${filtered.length} of ${products.length} products`
    : `${products.length} products`;

  list.innerHTML = filtered.map((product) => {
    const name = highlightHtml(product.name, tokens);
    const last = product.purchases[0];
    const sub = last
      ? `Last: ${escapeHtml(last.date)} · ${escapeHtml(last.store)}`
      : '';
    return `<li>
      <a class="card" href="${buildUrl({ view: 'products', p: product.name, r: null, q: product.name })}"
         data-product="${encodeURIComponent(product.name)}">
        <div class="card-head">
          <strong>${name}</strong>
          <span class="product-count">${product.count}×</span>
        </div>
        ${sub ? `<div class="card-sub">${sub}</div>` : ''}
      </a>
    </li>`;
  }).join('');

  empty.hidden = filtered.length > 0;
  hideAllViews();
  $('products-view').hidden = false;
  resetListFocus();
  syncUrl({ view: 'products', p: null, r: null });
}

function showProductDetail(name) {
  const product = products.find((row) => row.name === name);
  if (!product) return;

  currentProduct = name;
  currentId = null;
  setSearchQuery(name);
  $('product-title').innerHTML = highlightHtml(product.name, tokens);
  $('product-sub').textContent = `Purchased ${product.count} time${product.count === 1 ? '' : 's'}`;

  $('product-purchases').innerHTML = product.purchases.map((purchase) => `
    <li>
      <a class="purchase-link" href="${buildUrl({
        view: 'products',
        p: name,
        r: purchase.receiptId,
        q: name,
      })}">
        <span class="purchase-date">${escapeHtml(purchase.date)}</span>
        <span class="purchase-store">${escapeHtml(purchase.store || 'Unknown store')}</span>
      </a>
    </li>
  `).join('');

  hideAllViews();
  $('product-detail-view').hidden = false;
  syncUrl({ view: 'products', p: name, r: null, q: name });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function loadReceiptView(file) {
  if (!file) throw new Error('Missing receipt file');
  if (viewCache.has(file)) return viewCache.get(file);
  const res = await fetch(file);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await new Response(
    res.body.pipeThrough(new DecompressionStream('gzip')),
  ).text();
  const model = JSON.parse(json);
  viewCache.set(file, model);
  return model;
}

function receiptLineClass(line) {
  const parts = [];
  if (line.bold) parts.push('css_bold');
  if (line.kind === 'lidl_plus') {
    parts.push(line.role === 'savings' ? 'lidl-plus-highlight' : 'lidl-plus-discount');
  } else if (line.role === 'discount' && line.kind === 'other') {
    parts.push('discount-other');
  }
  return parts.join(' ');
}

function renderReceipt(body, receiptView, queryTokens) {
  body.replaceChildren();

  const sheet = document.createElement('div');
  sheet.className = 'receipt-sheet';

  if (receiptView.logo) {
    const logo = document.createElement('img');
    logo.className = 'receipt-logo';
    logo.src = receiptView.logo;
    logo.alt = 'Lidl';
    sheet.appendChild(logo);
  }

  const lines = receiptView.lines || [];
  const tenderIndex = lines.findIndex((line) => line.role === 'tender');
  const mainLines = tenderIndex >= 0 ? lines.slice(0, tenderIndex) : lines;
  const tailLines = tenderIndex >= 0 ? lines.slice(tenderIndex) : [];

  sheet.appendChild(buildReceiptPre(mainLines, queryTokens));
  appendBarcode(sheet, receiptView);
  if (tailLines.length) {
    sheet.appendChild(buildReceiptPre(tailLines, queryTokens));
  }

  body.appendChild(sheet);
}

function buildReceiptPre(lines, queryTokens) {
  const pre = document.createElement('pre');
  for (const line of lines) {
    if (!line.text) {
      pre.appendChild(document.createTextNode('\n'));
      continue;
    }
    const span = document.createElement('span');
    span.className = receiptLineClass(line);
    span.innerHTML = highlightHtml(line.text, queryTokens);
    pre.appendChild(span);
    pre.appendChild(document.createTextNode('\n'));
  }
  return pre;
}

function appendBarcode(parent, receiptView) {
  if (!receiptView.barcodeImage && !receiptView.barcode) return;

  const wrap = document.createElement('div');
  wrap.className = 'receipt-barcode-wrap';

  if (receiptView.barcodeImage) {
    const img = document.createElement('img');
    img.className = 'receipt-barcode';
    img.src = receiptView.barcodeImage;
    img.alt = '';
    wrap.appendChild(img);
  }

  if (receiptView.barcode) {
    const label = document.createElement('div');
    label.className = 'receipt-barcode-text';
    label.textContent = receiptView.barcode;
    wrap.appendChild(label);
  }

  parent.appendChild(wrap);
}

async function showDetail(id) {
  const row = receipts.find((r) => r.id === id);
  if (!row) return;

  currentId = id;
  let navList;
  let navIndex;
  if (currentProduct) {
    const product = products.find((p) => p.name === currentProduct);
    navList = product ? product.purchases.map((p) => ({ id: p.receiptId })) : [{ id }];
    navIndex = navList.findIndex((r) => r.id === id);
    activeMatches = navList;
  } else {
    activeMatches = getMatches();
    navList = activeMatches.length ? activeMatches : receipts;
    navIndex = navList.findIndex((r) => r.id === id);
  }
  const index = navIndex;

  $('detail-title').innerHTML = highlightHtml(row.store || 'Receipt', tokens);
  $('detail-sub').innerHTML = highlightHtml(
    [row.date, row.address].filter(Boolean).join(' · '),
    tokens,
  );

  const hasNav = navList.length > 1;
  $('prev').disabled = !hasNav || navIndex <= 0;
  $('next').disabled = !hasNav || navIndex >= navList.length - 1;

  if (tokens.length && activeMatches.length && !currentProduct) {
    $('match-pos').textContent = `${index + 1} / ${activeMatches.length}`;
  } else if (navList.length > 1) {
    $('match-pos').textContent = `${navIndex + 1} / ${navList.length}`;
  } else {
    $('match-pos').textContent = '';
  }

  hideAllViews();
  $('detail-view').hidden = false;
  $('back').textContent = currentProduct ? '← Product' : (view === 'products' ? '← Products' : '← Back');

  const body = $('receipt-body');
  body.textContent = 'Loading…';

  syncUrl();
  window.scrollTo({ top: 0, behavior: 'smooth' });

  try {
    const receiptView = await loadReceiptView(row.file);
    renderReceipt(body, receiptView, tokens);
  } catch (err) {
    body.textContent = `Failed to load receipt: ${err.message}`;
  }
}

function navigate(delta) {
  let navList;
  if (currentProduct) {
    const product = products.find((p) => p.name === currentProduct);
    navList = product ? product.purchases.map((p) => ({ id: p.receiptId })) : [];
  } else {
    navList = activeMatches.length ? activeMatches : receipts;
  }
  const index = navList.findIndex((r) => r.id === currentId);
  if (index < 0) return;
  const next = navList[index + delta];
  if (next) showDetail(next.id);
}

function onSearchInput() {
  tokens = tokenize($('q').value);
  $('clear-q').hidden = !tokens.length;
  if (!tokens.length && (currentProduct || (currentId && view === 'products'))) {
    currentProduct = null;
    currentId = null;
  }
  route();
}

function bindEvents() {
  $('q').addEventListener('input', onSearchInput);

  $('clear-q').addEventListener('click', () => {
    clearSearch();
    onSearchInput();
    $('q').focus();
  });

  $('results').addEventListener('click', (event) => {
    const card = event.target.closest('[data-id]');
    if (!card) return;
    event.preventDefault();
    view = 'receipts';
    currentProduct = null;
    showDetail(card.dataset.id);
  });

  $('product-results').addEventListener('click', (event) => {
    const card = event.target.closest('[data-product]');
    if (!card) return;
    event.preventDefault();
    view = 'products';
    showProductDetail(decodeURIComponent(card.getAttribute('data-product')));
  });

  $('product-purchases').addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    event.preventDefault();
    const params = new URLSearchParams(link.getAttribute('href').split('?')[1] || '');
    currentProduct = params.get('p');
    view = 'products';
    showDetail(params.get('r'));
  });

  $('nav-receipts').addEventListener('click', (event) => {
    event.preventDefault();
    view = 'receipts';
    currentProduct = null;
    currentId = null;
    renderList();
  });

  $('nav-products').addEventListener('click', (event) => {
    event.preventDefault();
    view = 'products';
    currentProduct = null;
    currentId = null;
    renderProducts();
  });

  $('back').addEventListener('click', () => {
    currentId = null;
    if (currentProduct) showProductDetail(currentProduct);
    else if (view === 'products') {
      clearSearch();
      renderProducts();
    } else renderList();
  });

  $('product-back').addEventListener('click', () => {
    currentProduct = null;
    clearSearch();
    renderProducts();
  });

  $('prev').addEventListener('click', () => navigate(-1));
  $('next').addEventListener('click', () => navigate(1));
  $('print').addEventListener('click', printReceipt);

  $('brand').addEventListener('click', (event) => {
    event.preventDefault();
    view = 'receipts';
    currentId = null;
    currentProduct = null;
    renderList();
  });

  window.addEventListener('popstate', () => {
    readUrl();
    route();
  });

  document.addEventListener('keydown', (event) => {
    const inSearch = document.activeElement === $('q');

    if (event.key === 'Escape') {
      handleEscape(event);
      return;
    }

    if (inSearch && event.key === 'Enter') {
      const items = visibleListItems();
      if (items.length && listFocusIndex >= 0) {
        event.preventDefault();
        items[listFocusIndex].click();
      }
      return;
    }

    if (inSearch && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      const items = visibleListItems();
      if (items.length) {
        event.preventDefault();
        setListFocus(listFocusIndex + (event.key === 'ArrowDown' ? 1 : -1));
      }
      return;
    }

    if (inSearch) return;

    if (!$('detail-view').hidden) {
      if (event.key === 'ArrowLeft') { event.preventDefault(); navigate(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); navigate(1); }
      return;
    }

    const items = visibleListItems();
    if (items.length) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setListFocus(listFocusIndex + 1); }
      if (event.key === 'ArrowUp') { event.preventDefault(); setListFocus(listFocusIndex - 1); }
      if (event.key === 'Enter' && listFocusIndex >= 0) {
        event.preventDefault();
        items[listFocusIndex].click();
      }
    }

    if (event.key === '/' && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      focusSearch();
    }
  });
}

async function loadArchive() {
  const url = window.LIDL_DATA;
  if (!url) {
    throw new Error('Missing archive data — run: lidl-plus backup index');
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load archive (${res.status})`);
  }
  const json = await new Response(
    res.body.pipeThrough(new DecompressionStream('gzip')),
  ).text();
  return JSON.parse(json);
}

async function init() {
  const archive = await loadArchive();
  if (!archive?.r || !archive?.p) {
    throw new Error('Invalid archive data — run: lidl-plus backup index');
  }
  receipts = archive.r;
  products = archive.p;
  readUrl();
  bindEvents();
  route();
  focusSearch();
}

init().catch((err) => {
  document.body.insertAdjacentHTML('beforeend',
    `<p class="empty">Failed to load archive: ${escapeHtml(String(err))}</p>`);
});

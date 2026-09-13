/*
 * Savvli header search - shared predictive dropdown for every page.
 *
 * Requires stores-data.js to be loaded first (defines the global STORES
 * object). Attaches to any <form class="header-search"> on the page -
 * works on every content page and every generated SEO page without
 * needing page-specific code.
 *
 * Graceful fallback: the form itself still has a real action="index.html"
 * method="get", so if this script fails to load for any reason, typing a
 * store name and pressing Enter still works via a normal page submission
 * to index.html?store=... - just without the live dropdown.
 */
(function () {
  if (typeof STORES === 'undefined') return;

  var storeNames = Object.keys(STORES);

  function slugifyStore(name) {
    return name.toLowerCase()
      .replace(/['\u2019]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function goToStore(name) {
    var store = STORES[name];
    if (store && store.offers && store.offers.length > 0) {
      window.location.href = slugifyStore(name) + '-cash-back.html';
    } else {
      window.location.href = 'index.html?store=' + encodeURIComponent(name);
    }
  }

  document.querySelectorAll('form.header-search').forEach(function (form) {
    var input = form.querySelector('input[type="text"]');
    if (!input) return;

    var suggestionsEl = document.createElement('div');
    suggestionsEl.className = 'header-suggestions';
    suggestionsEl.setAttribute('role', 'listbox');
    form.appendChild(suggestionsEl);

    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');

    var highlightedIndex = -1;
    var currentMatches = [];

    function renderSuggestions(query) {
      if (!query) {
        suggestionsEl.classList.remove('show');
        input.setAttribute('aria-expanded', 'false');
        currentMatches = [];
        return;
      }
      var q = query.toLowerCase();
      currentMatches = storeNames.filter(function (n) {
        return n.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 8);
      highlightedIndex = -1;

      if (currentMatches.length === 0) {
        suggestionsEl.innerHTML = '';
        suggestionsEl.classList.remove('show');
        input.setAttribute('aria-expanded', 'false');
        return;
      }

      suggestionsEl.innerHTML = currentMatches.map(function (name, i) {
        var store = STORES[name];
        var hint = store.offers && store.offers.length > 0
          ? store.offers.length + ' offers'
          : 'checking rates...';
        return '<div class="header-suggestion-item" role="option" data-index="' + i + '">' +
          '<span>' + escapeHtml(name) + '</span>' +
          '<span class="hint">' + hint + '</span>' +
          '</div>';
      }).join('');

      suggestionsEl.querySelectorAll('.header-suggestion-item').forEach(function (el, i) {
        el.addEventListener('click', function () {
          goToStore(currentMatches[i]);
        });
      });

      suggestionsEl.classList.add('show');
      input.setAttribute('aria-expanded', 'true');
    }

    input.addEventListener('input', function (e) {
      renderSuggestions(e.target.value);
    });

    input.addEventListener('keydown', function (e) {
      var items = suggestionsEl.querySelectorAll('.header-suggestion-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (items.length === 0) return;
        highlightedIndex = (highlightedIndex + 1) % items.length;
        items.forEach(function (el, i) { el.classList.toggle('highlighted', i === highlightedIndex); });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (items.length === 0) return;
        highlightedIndex = (highlightedIndex - 1 + items.length) % items.length;
        items.forEach(function (el, i) { el.classList.toggle('highlighted', i === highlightedIndex); });
      } else if (e.key === 'Enter') {
        if (highlightedIndex >= 0 && currentMatches[highlightedIndex]) {
          e.preventDefault();
          goToStore(currentMatches[highlightedIndex]);
        } else if (currentMatches.length > 0) {
          e.preventDefault();
          goToStore(currentMatches[0]);
        }
        // else: no matches, let the form submit normally to index.html?store=...
      } else if (e.key === 'Escape') {
        suggestionsEl.classList.remove('show');
        input.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('form.header-search')) {
        suggestionsEl.classList.remove('show');
        input.setAttribute('aria-expanded', 'false');
      }
    });
  });
})();

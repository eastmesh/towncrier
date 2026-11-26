(() => {
  const key = 'towncrier.theme.v1';
  const button = document.getElementById('themeToggle');
  if (!button) return;

  function applyTheme(theme) {
    const light = theme === 'light';
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    button.textContent = light ? '☾ Dark mode' : '☀ Light mode';
    button.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
    button.setAttribute('aria-pressed', String(light));
  }

  let savedTheme = 'dark';
  try {
    savedTheme = localStorage.getItem(key) === 'light' ? 'light' : 'dark';
  } catch {}
  applyTheme(savedTheme);
  button.addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(theme);
    try {
      localStorage.setItem(key, theme);
    } catch {}
  });
})();

// Shared save-confirmation hook: flashes the triggering save button gold and
// shows a centered "Saved" toast for ~1.5s. Server redirects for successful
// saves append ?saved=<id>, matched against a button's data-save-id; AJAX
// callers (schedule form, Composer) call window.towncrierFlashSaved directly
// once their own request has succeeded.
(() => {
  function flashSaved(button) {
    if (!button) return;
    button.classList.add('save-success');
    const toast = document.createElement('div');
    toast.className = 'save-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.textContent = 'Saved';
    document.body.appendChild(toast);
    setTimeout(() => {
      button.classList.remove('save-success');
      toast.remove();
    }, 1500);
  }
  window.towncrierFlashSaved = flashSaved;

  const params = new URLSearchParams(location.search);
  const saved = params.get('saved');
  if (saved && /^[a-z-]+$/.test(saved)) {
    flashSaved(document.querySelector(`[data-save-id="${saved}"]`));
  }
  if (params.has('saved')) {
    params.delete('saved');
    const query = params.toString();
    history.replaceState(null, '', location.pathname + (query ? `?${query}` : '') + location.hash);
  }
})();

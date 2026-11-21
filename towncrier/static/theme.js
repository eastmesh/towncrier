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

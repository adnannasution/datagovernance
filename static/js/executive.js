// Executive Reliability Dashboard — client interactions (vanilla, no deps).

// Tab switching
function exShowTab(name, btn) {
  document.querySelectorAll('.exec-dash .ex-panel').forEach(function (p) {
    p.classList.toggle('hidden', p.getAttribute('data-panel') !== name);
  });
  document.querySelectorAll('.exec-dash .ex-tab').forEach(function (t) {
    t.classList.remove('active');
  });
  if (btn) btn.classList.add('active');
}

// Refresh Snapshot — placeholder (no backend). Later: re-fetch snapshot API.
function exRefreshSnapshot(btn) {
  if (!btn) return;
  var original = btn.textContent;
  btn.classList.add('loading');
  btn.textContent = '⟳ Refreshing…';
  setTimeout(function () {
    btn.classList.remove('loading');
    btn.textContent = original;
    // TODO: replace with fetch('/api/executive/snapshot') and re-render.
  }, 900);
}

// Expose for inline handlers
window.exShowTab = exShowTab;
window.exRefreshSnapshot = exRefreshSnapshot;

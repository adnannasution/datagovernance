// Auto-refresh status untuk dokumen yang masih processing
document.querySelectorAll('.badge-pending, .badge-processing').forEach(badge => {
  const row = badge.closest('tr');
  if (!row) return;
  const link = row.querySelector('a[href^="/document/"]');
  if (!link) return;
  const docId = link.href.split('/document/')[1];
  if (!docId) return;

  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/document/${docId}/status`);
      const data = await res.json();
      if (data.status === 'ready' || data.status === 'error') {
        clearInterval(interval);
        badge.className = `badge badge-${data.status}`;
        badge.textContent = data.status;
      }
    } catch (_) {}
  }, 5000);
});
